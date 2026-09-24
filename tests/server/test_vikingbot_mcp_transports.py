# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Real transport tests for VikingBot's MCP v2 client integration."""

import socket
import sys
import threading
import time
from contextlib import AsyncExitStack, contextmanager
from types import SimpleNamespace

import pytest
import uvicorn
from mcp.server import MCPServer
from vikingbot.agent.tools.mcp import connect_mcp_servers


class _RecordingRegistry:
    def __init__(self):
        self.tools = {}

    def register(self, tool):
        self.tools[tool.name] = tool


@contextmanager
def _serve(app):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        if not thread.is_alive():
            raise RuntimeError("MCP test server stopped during startup")
        time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("MCP test server did not start")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            raise RuntimeError("MCP test server did not stop")


def _server_config(*, transport_type, url="", command="", args=None):
    return SimpleNamespace(
        type=transport_type,
        command=command,
        args=args or [],
        env={},
        url=url,
        headers={},
        tool_timeout=5,
        enabled_tools=["*"],
    )


@pytest.mark.asyncio
async def test_vikingbot_connects_to_real_stdio_server():
    server_script = """
from mcp.server import MCPServer

server = MCPServer("stdio-test")

@server.tool()
def echo(value: str) -> str:
    return f"echo:{value}"

server.run(transport="stdio")
"""
    registry = _RecordingRegistry()
    config = _server_config(
        transport_type="stdio",
        command=sys.executable,
        args=["-c", server_script],
    )

    async with AsyncExitStack() as stack:
        await connect_mcp_servers({"stdio": config}, registry, stack)
        tool = registry.tools["mcp_stdio_echo"]
        result = await tool.execute(SimpleNamespace(), value="ready")

    assert result == "echo:ready"


@pytest.mark.parametrize("transport_type", ["sse", "streamableHttp"])
@pytest.mark.asyncio
async def test_vikingbot_connects_to_real_http_server(transport_type):
    server = MCPServer(f"{transport_type}-test")

    @server.tool()
    def echo(value: str) -> str:
        return f"echo:{value}"

    app = (
        server.sse_app()
        if transport_type == "sse"
        else server.streamable_http_app(stateless_http=True)
    )
    registry = _RecordingRegistry()

    with _serve(app) as base_url:
        suffix = "/sse" if transport_type == "sse" else "/mcp"
        config = _server_config(transport_type=transport_type, url=f"{base_url}{suffix}")
        async with AsyncExitStack() as stack:
            await connect_mcp_servers({"http": config}, registry, stack)
            tool = registry.tools["mcp_http_echo"]
            result = await tool.execute(SimpleNamespace(), value="ready")

    assert result == "echo:ready"
