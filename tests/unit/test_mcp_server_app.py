from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import openviking
import openviking.server.app as server_app
import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.config import ServerConfig
from openviking.server.mcp_endpoint import _IdentityASGIMiddleware


def test_create_mcp_app_applies_streamable_http_options(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_streamable_http_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(routes=[SimpleNamespace(app=downstream)])

    monkeypatch.setattr(mcp_endpoint.mcp, "streamable_http_app", fake_streamable_http_app)

    app = mcp_endpoint.create_mcp_app()

    assert isinstance(app, _IdentityASGIMiddleware)
    assert app.app is downstream
    assert captured["stateless_http"] is True
    assert captured["max_request_body_size"] == 4 * 1024 * 1024
    assert captured["transport_security"].enable_dns_rebinding_protection is False


def test_create_mcp_app_applies_custom_request_body_limit(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_streamable_http_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(routes=[SimpleNamespace(app=downstream)])

    monkeypatch.setattr(mcp_endpoint.mcp, "streamable_http_app", fake_streamable_http_app)

    app = mcp_endpoint.create_mcp_app(max_request_body_size=8 * 1024 * 1024)

    assert isinstance(app, _IdentityASGIMiddleware)
    assert captured["max_request_body_size"] == 8 * 1024 * 1024


def test_create_app_passes_configured_mcp_request_body_limit(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_create_mcp_app(*, max_request_body_size):
        captured["max_request_body_size"] = max_request_body_size
        return downstream

    monkeypatch.setattr(mcp_endpoint, "create_mcp_app", fake_create_mcp_app)
    config = ServerConfig(mcp_max_request_body_size_bytes=8 * 1024 * 1024)

    server_app.create_app(config=config)

    assert captured["max_request_body_size"] == 8 * 1024 * 1024


@pytest.mark.parametrize("value", [0, -1])
def test_server_config_rejects_non_positive_mcp_request_body_limit(value):
    with pytest.raises(ValidationError):
        ServerConfig(mcp_max_request_body_size_bytes=value)


def test_mcp_server_advertises_openviking_version():
    assert mcp_endpoint.mcp.version == openviking.__version__
