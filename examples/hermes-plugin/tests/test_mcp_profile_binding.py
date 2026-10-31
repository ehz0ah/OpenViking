"""MCP launch entries remain bound to the profile that starts the child."""

import argparse
import importlib
import json

import pytest


@pytest.mark.parametrize("profile_bound,home_set", [(False, True), (True, False)])
def test_unbound_launch_refuses_before_connection(
    external_provider, monkeypatch, profile_bound, home_set
):
    home, _, module, _ = external_provider("launch-guard")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    if home_set:
        monkeypatch.setenv("HERMES_HOME", str(home))
    else:
        monkeypatch.delenv("HERMES_HOME")
    contacted = []

    async def serve(resolved):
        contacted.append(resolved)

    monkeypatch.setattr(adapter, "serve", serve)
    with pytest.raises(SystemExit):
        adapter.run(argparse.Namespace(profile_bound=profile_bound))
    assert contacted == []


def test_bound_launch_uses_supplied_profile_home(external_provider, monkeypatch):
    home, _, module, _ = external_provider("bound-launch")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    monkeypatch.setenv("HERMES_HOME", str(home))
    contacted = []

    async def serve(resolved):
        contacted.append(resolved)

    monkeypatch.setattr(adapter, "serve", serve)
    adapter.run(argparse.Namespace(profile_bound=True))
    assert contacted == [home.resolve()]


def test_new_entry_and_legacy_migration_preserve_profile_policy(external_provider):
    home, _, module, _ = external_provider("portable-entry")
    adapter = importlib.import_module(module.__name__ + ".mcp_tools")
    config = {
        "mcp_servers": {
            "openviking": {
                "trust": "full",
                "enabled": False,
                "tools": {"exclude": ["forget"]},
                "headers": {"X-Gateway": "${GATEWAY_SECRET}"},
            }
        }
    }
    adapter.configure(config, str(home))
    entry = config["mcp_servers"]["openviking"]
    assert entry["args"] == ["openviking", "mcp", "--profile-bound"]
    assert "HERMES_HOME" not in entry["env"]
    entry["args"] = ["openviking", "mcp"]
    entry["env"]["HERMES_HOME"] = str(home.parent / "source")
    (home / "config.yaml").write_text(json.dumps(config), encoding="utf-8")
    assert adapter.repair_profile_home(home)
    from hermes_cli.config import read_user_config_raw

    saved = read_user_config_raw(home / "config.yaml")["mcp_servers"]["openviking"]
    assert saved["args"] == ["openviking", "mcp", "--profile-bound"]
    assert "HERMES_HOME" not in saved["env"]
    for key in ("trust", "enabled", "tools", "headers"):
        assert saved[key] == entry[key]
    assert adapter.repair_profile_home(home) is None
