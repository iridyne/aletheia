"""Unsafe HTTP requests need trusted browser provenance, independently of CORS."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import AletheiaConfig
from aletheia.server.app import app, _tunnel_auth_guard


@pytest.fixture
def client(api_only_runtime):
    cfg = AletheiaConfig()
    cfg.health.startup_behavior_gate_enabled = False
    cfg.health.require_llm_at_startup = False
    save_config(cfg)
    client = TestClient(app, base_url="http://127.0.0.1:8787")
    yield client
    client.close()


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example"},
    {"Origin": "null"},
    {"Origin": ""},
    {"Origin": "http://127.0.0.1:8787/"},
    {"Origin": "http://127.0.0.1:8787?trusted"},
    {"Origin": "http://127.0.0.1:8787#trusted"},
    {"Origin": "http://user@127.0.0.1:8787"},
    {"Origin": "http://[invalid"},
    {"Origin": "http://127.0.0.1:9999"},
    {"Origin": "https://attacker.example", "Sec-Fetch-Site": "same-origin"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "none"},
    {"Sec-Fetch-Site": ""},
    {"Sec-Fetch-Mode": "navigate"},
    {"Referer": "https://attacker.example/form"},
    [("Origin", "http://127.0.0.1:8787"), ("Origin", "https://attacker.example")],
])
def test_untrusted_browser_cannot_acquire_manual_pause(client, headers):
    response = client.post("/api/gateway/pause", headers=headers)

    assert response.status_code == 403
    assert "manual" not in load_config().runtime.pause_owners


def test_hostile_resume_preserves_manual_owner_and_scheduler(client, monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.agent_runtime.pause_ownership import MANUAL_OWNER, acquire_pause

    acquire_pause(MANUAL_OWNER, reason="human_pause", generation="human-1")
    resume = Mock(return_value=True)
    monkeypatch.setattr(scheduler, "resume_scheduler", resume)

    response = client.post("/api/gateway/resume", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 403
    assert load_config().runtime.pause_owners[MANUAL_OWNER].generation == "human-1"
    assert load_config().runtime.paused is True
    resume.assert_not_called()

    response = client.post("/api/gateway/resume", headers={"Origin": "http://127.0.0.1:8787"})
    assert response.status_code == 200, response.text
    assert load_config().runtime.paused is False
    resume.assert_called_once()


def test_hostile_empty_restart_rejected_before_audit_or_scheduling(client, monkeypatch):
    from aletheia.server.routes import gateway

    monkeypatch.setattr(app.state, "_owns_pid", False, raising=False)
    monkeypatch.setattr(gateway, "_is_reload_mode", lambda: False)
    audit = Mock()
    # Never run the process-exit coroutine, even if this regression fails.
    schedule = Mock(side_effect=lambda coro: coro.close())
    monkeypatch.setattr(gateway, "write_audit", audit)
    monkeypatch.setattr(gateway, "asyncio", SimpleNamespace(create_task=schedule))

    response = client.post("/api/gateway/restart", headers={"Origin": "https://attacker.example"})

    assert response.status_code == 403
    audit.assert_not_called()
    schedule.assert_not_called()


@pytest.mark.parametrize("headers", [
    {},  # Explicit non-browser CLI.
    {"Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://127.0.0.1:8787", "Sec-Fetch-Site": "same-origin"},
    {"Origin": "http://localhost:8787"},
    {"Origin": "http://127.0.0.1:5173", "Sec-Fetch-Site": "same-site"},
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "cross-site"},
])
@pytest.mark.parametrize("remote_enabled", [False, True])
def test_supported_dashboard_and_cli_can_pause_without_token(client, headers, remote_enabled):
    cfg = load_config()
    cfg.remote_control.enabled = remote_enabled
    cfg.remote_control.require_token = False
    save_config(cfg)

    response = client.post("/api/gateway/pause", headers=headers)

    assert response.status_code == 200, response.text
    assert load_config().runtime.pause_owners["manual"].active is True


@pytest.mark.parametrize("url,origin", [
    ("http://dashboard.example:80", "http://dashboard.example"),
    ("https://dashboard.example:443", "https://dashboard.example"),
    ("https://dashboard.example", "https://dashboard.example:443"),
    ("http://[::1]:9123", "http://[::1]:9123"),
])
def test_direct_same_origin_including_default_ports(client, url, origin):
    # TestClient cannot parse IPv6 netlocs; the ASGI request still gets the real Host.
    response = client.post(f"{url.replace('[::1]', 'test')}/api/gateway/pause", headers={
        "Origin": origin, "Host": url.split("://", 1)[1],
    })

    assert response.status_code == 200, response.text
    assert load_config().runtime.paused is True


@pytest.mark.parametrize("mode", ["tunnel", "remote_control"])
def test_trusted_browser_origin_does_not_replace_required_owner_token(client, monkeypatch, mode):
    from aletheia.config import secrets

    cfg = load_config()
    getattr(cfg, mode).enabled = True
    cfg.remote_control.require_token = True
    save_config(cfg)
    monkeypatch.setattr(secrets, "get_secret", lambda key: "owner-token")
    headers = {"Origin": "http://127.0.0.1:8787"}

    for token in (None, "wrong-token"):
        response = client.post("/api/gateway/pause", headers={
            **headers, **({"X-Aletheia-Token": token} if token else {}),
        })
        assert response.status_code == 401
        assert "manual" not in load_config().runtime.pause_owners

    response = client.post("/api/gateway/pause", headers={**headers, "X-Aletheia-Token": "owner-token"})
    assert response.status_code == 200, response.text
    assert load_config().runtime.paused is True

    monkeypatch.setattr(secrets, "get_secret", lambda key: None)
    response = client.post("/api/gateway/pause", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_every_registered_unsafe_api_route_rejects_hostile_origin(client):
    async def mutation(_request):
        pytest.fail("Untrusted browser reached an unsafe API handler")

    mutations = {
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith(("/api/", "/_infra/"))
        for method in getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert {
        ("POST", "/api/gateway/pause"),
        ("POST", "/api/gateway/resume"),
        ("POST", "/api/gateway/restart"),
    } <= mutations
    for method, path in mutations:
        request = Request({
            "type": "http", "method": method, "path": path,
            "scheme": "http", "server": ("127.0.0.1", 8787), "query_string": b"",
            "headers": [(b"origin", b"https://attacker.example")],
        })
        response = await _tunnel_auth_guard(request, mutation)
        assert response.status_code == 403, (method, path)


def test_safe_requests_and_cors_preflight_remain_available(client):
    assert client.get("/api/ready", headers={"Origin": "https://attacker.example"}).status_code == 200
    response = client.options("/api/gateway/pause", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 200
    assert "manual" not in load_config().runtime.pause_owners
