from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def _auth_config():
    return SimpleNamespace(
        tunnel=SimpleNamespace(enabled=False),
        remote_control=SimpleNamespace(enabled=True, require_token=True),
    )


def test_operator_service_route_allowlist_is_bounded():
    from aletheia.server.operator_service_auth import operator_service_route_allowed

    assert operator_service_route_allowed("POST", "/api/operator/internal/heartbeat")
    assert operator_service_route_allowed("PATCH", "/api/operator/internal/tasks/task-1")
    assert operator_service_route_allowed("GET", "/api/operator/sessions/session-1")
    assert operator_service_route_allowed("GET", "/api/health")
    assert operator_service_route_allowed("POST", "/api/agent-runs/start")

    assert not operator_service_route_allowed("POST", "/api/operator/pause")
    assert not operator_service_route_allowed("POST", "/api/settings/risk")
    assert not operator_service_route_allowed("POST", "/api/gateway/restart")
    assert not operator_service_route_allowed("POST", "/api/risk/kill-switch")
    assert not operator_service_route_allowed("GET", "/api/settings")


def test_service_token_authenticates_internal_heartbeat_under_owner_auth(monkeypatch):
    import aletheia.config.secrets as secret_store
    import aletheia.server.app as app_module
    import aletheia.server.operator_service_auth as service_auth

    monkeypatch.setattr(app_module, "load_config", lambda persist_migrations=False: _auth_config())
    monkeypatch.setattr(
        secret_store,
        "get_secret",
        lambda key: "owner-token" if key == "remote_control_api_token" else None,
    )
    monkeypatch.setattr(service_auth, "current_operator_service_token", lambda: "service-token")

    client = TestClient(app_module.app)
    response = client.post(
        "/api/operator/internal/heartbeat",
        headers={"X-Aletheia-Operator-Service": "service-token"},
        json={
            "status": "online",
            "mode": "Observing",
            "permission_mode": "ReadOnly",
            "runtime_mode": "current",
            "runtime_state": "current",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "online"


def test_service_token_cannot_authorize_owner_facing_mutation(monkeypatch):
    import aletheia.config.secrets as secret_store
    import aletheia.server.app as app_module
    import aletheia.server.operator_service_auth as service_auth

    monkeypatch.setattr(app_module, "load_config", lambda persist_migrations=False: _auth_config())
    monkeypatch.setattr(
        secret_store,
        "get_secret",
        lambda key: "owner-token" if key == "remote_control_api_token" else None,
    )
    monkeypatch.setattr(service_auth, "current_operator_service_token", lambda: "service-token")

    client = TestClient(app_module.app)
    response = client.post(
        "/api/gateway/restart",
        headers={"X-Aletheia-Operator-Service": "service-token"},
    )

    assert response.status_code == 401


def test_external_request_without_owner_or_service_identity_still_fails(monkeypatch):
    import aletheia.config.secrets as secret_store
    import aletheia.server.app as app_module
    import aletheia.server.operator_service_auth as service_auth

    monkeypatch.setattr(app_module, "load_config", lambda persist_migrations=False: _auth_config())
    monkeypatch.setattr(
        secret_store,
        "get_secret",
        lambda key: "owner-token" if key == "remote_control_api_token" else None,
    )
    monkeypatch.setattr(service_auth, "current_operator_service_token", lambda: "service-token")

    client = TestClient(app_module.app)
    response = client.post(
        "/api/operator/internal/heartbeat",
        json={
            "status": "online",
            "mode": "Observing",
            "permission_mode": "ReadOnly",
        },
    )

    assert response.status_code == 401


def test_operator_shell_env_receives_service_token_without_owner_token():
    from aletheia.gateway.operator_shell import _build_operator_shell_env
    from aletheia.operator_service_identity import OPERATOR_SERVICE_ENV

    cfg = SimpleNamespace(
        app=SimpleNamespace(dashboard_port=8787),
        operator=SimpleNamespace(
            permission_mode="ReadOnly",
            heartbeat_interval_sec=10,
            main_session_id="operator-main",
        ),
    )

    env = _build_operator_shell_env(
        cfg,
        runtime_id="runtime-test",
        service_token="service-token",
    )

    assert env[OPERATOR_SERVICE_ENV] == "service-token"
    assert "remote_control_api_token" not in env
    assert "X-Aletheia-Token" not in env
