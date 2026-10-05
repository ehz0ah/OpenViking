"""Profile connection adapter for the server's unmodified MCP tools.

Hermes owns discovery, tool filtering and approval. This process only resolves
the same connection as the memory provider and forwards MCP requests. It never
starts or stops an OpenViking server and never retries a tool call.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time
from builtins import BaseExceptionGroup
from pathlib import Path

SERVER_NAME = "openviking"
_ENV_KEYS = tuple(
    f"OPENVIKING_{key}"
    for key in (
        "ENDPOINT",
        "API_KEY",
        "ACCOUNT",
        "USER",
        "AGENT",
        "CLI_CONFIG_FILE",
    )
)
_CONNECTION_HEADERS = {
    "authorization",
    "x-api-key",
    "x-openviking-account",
    "x-openviking-user",
    "x-openviking-actor-peer",
}


def _leaf_error(exc):
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def configure(config: dict, hermes_home: str) -> None:
    """Save one adapter entry; preserve the user's MCP policy and custom headers."""
    servers = config.get("mcp_servers")
    if not isinstance(servers, dict):
        servers = config["mcp_servers"] = {}
    existing = servers.get(SERVER_NAME)
    entry = dict(existing) if isinstance(existing, dict) else {}
    for key in ("url", "transport", "auth", "oauth", "identity_header", "cwd"):
        entry.pop(key, None)
    env = dict(entry.get("env") or {})
    env.update({key: "${" + key + "}" for key in _ENV_KEYS})
    env["HERMES_HOME"] = str(Path(hermes_home).resolve())
    entry.update(command="hermes", args=["openviking", "mcp"], env=env)
    if isinstance(entry.get("headers"), dict):
        entry["headers"] = {
            k: v for k, v in entry["headers"].items() if k.lower() not in _CONNECTION_HEADERS
        }
    entry.setdefault("enabled", True)
    # Cold Quick Local starts happen in the memory provider, independently of
    # this child. Leave enough time for discovery to wait for that startup.
    entry.setdefault("connect_timeout", 60)
    servers[SERVER_NAME] = entry


def _provider_module():
    # Hermes imports cli.py under a synthetic package without executing the
    # provider. Load our own source to reuse its profile/credential resolution,
    # without initializing a second memory session or relying on host internals.
    name = "_openviking_mcp_connection"
    spec = importlib.util.spec_from_file_location(
        name,
        Path(__file__).with_name("__init__.py"),
        submodule_search_locations=[str(Path(__file__).parent)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def profile_entry(home, env):
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_cli.config import load_config_readonly
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_token = set_hermes_home_override(home)
    secret_token = set_secret_scope(env, profile_home=str(home))
    try:
        return load_config_readonly().get("mcp_servers", {}).get(SERVER_NAME, {})
    finally:
        reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


class Connection:
    def __init__(self, home: Path, provider_module):
        self.home = home
        self.ov = provider_module

    def resolve(self):
        env = self.ov._profile_openviking_env(str(self.home))
        config = self.ov._load_hermes_openviking_config(str(self.home), env=env)
        values = self.ov._resolve_connection_settings(config, env=env, hermes_home=self.home)
        entry = profile_entry(self.home, env)
        return values, entry, config.get("deployment") == self.ov.quick_local.DEPLOYMENT

    async def request(self, method: str, params):
        values, entry, _ = await asyncio.to_thread(self.resolve)
        return await _request(self.ov, values, entry, method, params)

    async def discover(self, params):
        """Wait for the provider's cold start; never own its detached process."""
        import httpx2

        _, entry, managed = await asyncio.to_thread(self.resolve)
        deadline = time.monotonic() + float(entry.get("connect_timeout", 60))
        while True:
            try:
                return await self.request("tools/list", params)
            except Exception as exc:
                if (
                    not managed
                    or not isinstance(_leaf_error(exc), httpx2.ConnectError)
                    or time.monotonic() >= deadline
                ):
                    raise
                await asyncio.sleep(0.25)


async def _request(ov, values, entry, method, params):
    import httpx2
    from mcp import ClientSession, types
    from mcp.client.streamable_http import streamable_http_client

    rest = ov._VikingClient(**values)
    extra = {
        k: v
        for k, v in (entry.get("headers") or {}).items()
        if k.lower() not in _CONNECTION_HEADERS
    }
    timeout = float(entry.get("timeout", 60))
    cert = entry.get("client_cert")
    if entry.get("client_key"):
        cert = (cert, entry["client_key"])
    elif isinstance(cert, list):
        cert = tuple(cert)
    url = values["endpoint"].rstrip("/") + "/mcp"
    # No redirect forwarding: neither credentials nor custom headers may
    # be sent to a second origin. Use the final server URL in setup.
    async with httpx2.AsyncClient(
        timeout=timeout,
        verify=entry.get("ssl_verify", True),
        follow_redirects=False,
        cert=cert,
    ) as http:
        headers = {**extra, **rest._headers()}
        response = await http.get(values["endpoint"] + "/api/v1/system/status", headers=headers)
        try:
            rest._parse_response(response)
        except Exception as exc:
            if not values["api_key"] or not rest._needs_trusted_identity_retry(exc):
                raise
            headers = {**extra, **rest._headers(include_tenant=True)}
            response = await http.get(values["endpoint"] + "/api/v1/system/status", headers=headers)
            rest._parse_response(response)
        http.headers.update(headers)
        async with streamable_http_client(url, http_client=http) as streams:
            async with ClientSession(
                streams[0], streams[1], read_timeout_seconds=timeout
            ) as session:
                await session.initialize()
                if method == "tools/list":
                    return await session.list_tools(params=params)
                return await session.send_request(
                    types.CallToolRequest(params=params), types.CallToolResult
                )


def validate_connection(ov, values):
    """Verify MCP discovery before setup saves this connection."""
    from hermes_cli.config import load_config_readonly

    values = {
        key: values.get(key) or ("default" if key in {"account", "user"} else "")
        for key in ov._CONNECTION_KEYS
    }
    entry = load_config_readonly().get("mcp_servers", {}).get(SERVER_NAME, {})
    try:
        asyncio.run(_request(ov, values, entry, "tools/list", None))
    except Exception as exc:
        return (
            False,
            f"OpenViking MCP is unavailable ({type(_leaf_error(exc)).__name__}). Use a server with /mcp enabled.",
        )
    return True, ""


async def serve(home: Path, provider_module=None):
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    connection = Connection(home, provider_module or _provider_module())

    async def list_tools(_context, params):
        try:
            return await connection.discover(params)
        except Exception as exc:
            raise RuntimeError(
                f"OpenViking MCP discovery failed ({type(_leaf_error(exc)).__name__}). "
                "Check the server and run hermes memory setup openviking."
            ) from None

    async def call_tool(_context, params):
        from mcp import types

        try:
            return await connection.request("tools/call", params)
        except Exception as exc:
            return types.CallToolResult(
                isError=True,
                content=[
                    types.TextContent(
                        type="text",
                        text=f"OpenViking MCP request failed ({type(_leaf_error(exc)).__name__}). "
                        "The operation was not retried. A write may have reached the server; check its result before retrying.",
                    )
                ],
            )

    server = Server("openviking", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run(_args):
    from hermes_constants import get_hermes_home

    # Hermes preserves unresolved optional env references in stdio config.
    # Treat those as absent, never as an API key or a path.
    for key in _ENV_KEYS:
        if os.environ.get(key) == "${" + key + "}":
            os.environ.pop(key)
    asyncio.run(serve(Path(get_hermes_home())))
