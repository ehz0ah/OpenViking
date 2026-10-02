#!/usr/bin/env python3
"""Run real Quick Local installation, local embeddings and profile lifecycle.

Uses a static test LLM configuration. It does not call a remote LLM; extraction
and interactive setup are separate live release checks.
"""

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--legacy-uv", action="store_true")
    args = parser.parse_args()
    root = args.output_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    host = args.hermes_root.resolve()
    sys.path.insert(0, str(host))
    for key in list(os.environ):
        if key.startswith(("OPENVIKING_", "HERMES_", "__HERMES_")):
            del os.environ[key]
    os.environ.update(
        HERMES_HOME=str(root / "a"),
        HERMES_RUNTIME_DIR=str(root / "host-runtime"),
        HERMES_ENABLE_PROJECT_PLUGINS="0",
    )
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override
    from plugins import memory

    memory._MEMORY_PLUGINS_DIR = root / "empty-bundled"
    source = Path(__file__).resolve().parents[1] / "examples/hermes-plugin"
    servers, providers = [], []
    evidence = {"legacy_uv": args.legacy_uv, "profiles": []}
    try:
        for name in ("a", "b"):
            home = root / name
            shutil.copytree(
                source,
                home / "plugins/openviking",
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("tests", "__pycache__"),
            )
            config = {
                "model": {"provider": "custom:test", "default": "test-model"},
                "custom_providers": [
                    {
                        "name": "test",
                        "base_url": "http://127.0.0.1:9/v1",
                        "api_key": "isolated-static-test-key",
                        "api_mode": "chat_completions",
                    }
                ],
                "memory": {"provider": "openviking", "openviking": {"deployment": "quick_local"}},
                "plugins": {"enabled": ["openviking"]},
            }
            (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
            if os.name != "nt":
                (home / "config.yaml").chmod(0o600)
            token = set_hermes_home_override(home)
            try:
                assert memory.find_provider_dir("openviking") == home / "plugins/openviking"
                provider = memory.load_memory_provider("openviking", register_skills=False)
                assert type(provider).__module__.startswith("_hermes_user_memory.")
                ql = importlib.import_module(type(provider).__module__ + ".quick_local")
                lifecycle = importlib.import_module(type(provider).__module__ + ".local_server")
                if args.legacy_uv:
                    assert not ql._pm_available(), "Use a pre-PM Hermes host for the legacy test"
                server = lifecycle.LocalServer(home)
                servers.append(server)
                providers.append(provider)
                module = importlib.import_module(type(provider).__module__)
                setup = ql.QuickLocalSetup(health_check=module._validate_openviking_reachability)
                setup.provision(hermes_home=home)
                provider.initialize("local-smoke-" + name, hermes_home=str(home), platform="cli")
                assert provider._client is not None
                provider.sync_turn(
                    "A captured local test message",
                    "Acknowledged",
                    session_id="local-smoke-" + name,
                )
                assert provider._drain_writers("local-smoke-" + name, timeout=15)
                session = provider._client.get("/api/v1/sessions/local-smoke-" + name)["result"]
                assert session["pending_tokens"] > 0
                browse = json.loads(
                    provider.handle_tool_call(
                        "viking_browse", {"path": "viking://", "action": "list"}
                    )
                )
                assert not browse.get("error")
                old_pid = server.status()["pid"]
                updated = server._config()
                updated["vlm"]["api_key"] = "updated-isolated-static-key"
                server.configure(updated)
                server.wait_ready(server.start(), setup._health_check, timeout=120)
                assert server.status()["pid"] != old_pid
                assert (
                    provider._client.get("/api/v1/sessions/local-smoke-" + name)["result"][
                        "pending_tokens"
                    ]
                    > 0
                )
                probe = (
                    "from openviking.models.embedder.local_embedders import LocalDenseEmbedder; import math; e=LocalDenseEmbedder(model_name='bge-small-zh-v1.5-f16',cache_dir="
                    + repr(str(server.paths.model_cache))
                    + "); v=e.embed('local embedding validation').dense_vector; assert len(v)==512 and all(math.isfinite(x) for x in v) and any(v)"
                )
                subprocess.run(
                    [str(server.paths.runtime_python), "-c", probe], check=True, timeout=120
                )
                evidence["profiles"].append(server.status())
            finally:
                reset_hermes_home_override(token)
        assert servers[0].status()["endpoint"] != servers[1].status()["endpoint"]
        # A's saved port is taken by B while A is stopped. A must move and keep
        # its captured session, without stopping or reconfiguring B.
        a, b = servers
        previous = a.status()["endpoint"]
        a.stop()
        config = b._config()
        config["server"]["port"] = b.ql._endpoint_port(previous)
        b.configure(config)
        b.wait_ready(
            b.start(), lambda url: (b.ql.server_belongs_to_profile(b.paths, url), ""), timeout=120
        )
        foreign_pid = b.status()["pid"]
        moved = a.wait_ready(
            a.start(), lambda url: (a.ql.server_belongs_to_profile(a.paths, url), ""), timeout=120
        )
        assert moved.endpoint != previous and b.status()["pid"] == foreign_pid
        evidence.update(
            external_loader=True,
            real_embeddings=True,
            captured_turns=True,
            restart_preserved_data=True,
            foreign_port_recovery=True,
        )
        print(json.dumps(evidence))
    finally:
        for provider in providers:
            provider.shutdown()
        for server in reversed(servers):
            if server.paths.server_config.exists():
                server.stop()
        (root / "evidence.json").write_text(json.dumps(evidence, indent=2) + "\n")


if __name__ == "__main__":
    main()
