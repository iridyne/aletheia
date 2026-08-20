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


def test_service_token_authenticates_internal_heartbeat_under_owner_auth(
    monkeypatch,
    api_only_runtime,
):
    import aletheia.config.secrets as secret_store
    import aletheia.server.app as app_module
    import aletheia.server.operator_service_auth as service_auth

    monkeypatch.setattr(app_module, "load_config", lambda persist_migrations=False: _auth_config())
    monkeypatch.setattr(
        secret_store,
        "get_secret",
        lambda key: "owner-token" if key == "remote_control_api_token" else None,
    )
    monkeypatch.setattr(service_auth, "is_valid_operator_service_token", lambda t: t == "service-token")

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
    monkeypatch.setattr(service_auth, "is_valid_operator_service_token", lambda t: t == "service-token")

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
    monkeypatch.setattr(service_auth, "is_valid_operator_service_token", lambda t: t == "service-token")

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


# ---------------------------------------------------------------------------
# Per-process credential lifecycle (issue #1106)
# ---------------------------------------------------------------------------

def _reset_token_registry(monkeypatch):
    import aletheia.operator_service_identity as osi
    monkeypatch.setattr(osi, "_active_session_tokens", set())
    monkeypatch.setattr(osi, "_live_shell_token", None)


def _mock_secret_store(monkeypatch):
    import aletheia.operator_service_identity as osi
    store: dict = {}
    # Patch the names as bound in the osi module (where rotate/load actually call them).
    monkeypatch.setattr(osi, "set_secret", lambda k, v: store.update({k: v}))
    monkeypatch.setattr(osi, "_file_get", lambda k: store.get(k))
    return store


def test_shadow_token_remains_valid_after_live_shell_rotation(monkeypatch):
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    shadow_token = osi.issue_session_token()
    live_token = osi.rotate_operator_service_token()

    assert osi.is_valid_operator_service_token(shadow_token), "shadow invalidated by live rotation"
    assert osi.is_valid_operator_service_token(live_token), "live token must be valid"


def test_revoked_shadow_token_is_rejected(monkeypatch):
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    shadow_token = osi.issue_session_token()
    live_token = osi.rotate_operator_service_token()

    osi.revoke_session_token(shadow_token)
    assert not osi.is_valid_operator_service_token(shadow_token), "revoked token must be rejected"
    assert osi.is_valid_operator_service_token(live_token), "live token must survive shadow revocation"


def test_shadow_startup_does_not_invalidate_live_shell(monkeypatch):
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    live_token = osi.rotate_operator_service_token()
    _shadow_token = osi.issue_session_token()

    assert osi.is_valid_operator_service_token(live_token), "shadow startup must not invalidate live token"


def test_live_respawn_does_not_invalidate_active_shadow(monkeypatch):
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    shadow_token = osi.issue_session_token()
    _first_live = osi.rotate_operator_service_token()
    _second_live = osi.rotate_operator_service_token()  # simulate a second respawn

    assert osi.is_valid_operator_service_token(shadow_token), "shadow invalidated by live respawn"
    assert not osi.is_valid_operator_service_token(_first_live), "old live token must be rotated away"
    assert osi.is_valid_operator_service_token(_second_live)


def test_multiple_shadow_runs_have_independent_credentials(monkeypatch):
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    tokens = [osi.issue_session_token() for _ in range(3)]

    # All three are independently valid
    for t in tokens:
        assert osi.is_valid_operator_service_token(t)

    # Revoking one does not affect the others
    osi.revoke_session_token(tokens[1])
    assert osi.is_valid_operator_service_token(tokens[0])
    assert not osi.is_valid_operator_service_token(tokens[1])
    assert osi.is_valid_operator_service_token(tokens[2])


# ---------------------------------------------------------------------------
# Cross-store authority (issue #1104)
# ---------------------------------------------------------------------------

def test_stale_keyring_does_not_override_file_after_rotation(monkeypatch):
    """Existing keyring value + failed set_password() must not cause current_operator_service_token()
    to return a different value from the rotated/injected token (issue #1104)."""
    import aletheia.operator_service_identity as osi
    import aletheia.config.secrets as secrets_mod
    _reset_token_registry(monkeypatch)

    file_store: dict[str, str] = {}
    # Patch osi module names (where rotate/load actually call them).
    monkeypatch.setattr(osi, "set_secret", lambda k, v: file_store.update({k: v}))
    monkeypatch.setattr(osi, "_file_get", lambda k: file_store.get(k))
    # Verify stale get_secret is not consulted — _load_persisted now uses _file_get.
    monkeypatch.setattr(secrets_mod, "get_secret", lambda k: "stale-keyring-token-A")

    new_token = osi.rotate_operator_service_token()
    confirmed = osi.current_operator_service_token()

    assert confirmed == new_token, "current_operator_service_token must match rotated token"
    assert confirmed != "stale-keyring-token-A", "stale keyring value must not bleed through"


def test_successful_rotation_current_token_matches_injected(monkeypatch):
    """Successful rotation: current_operator_service_token() must equal the returned token."""
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    _mock_secret_store(monkeypatch)

    new_token = osi.rotate_operator_service_token()
    confirmed = osi.current_operator_service_token()

    assert confirmed == new_token


def test_rotation_file_write_failure_surfaces_error(monkeypatch):
    """If set_secret raises, rotate_operator_service_token must propagate the error rather
    than returning a token that the backend cannot verify."""
    import pytest
    import aletheia.operator_service_identity as osi
    _reset_token_registry(monkeypatch)
    monkeypatch.setattr(osi, "_file_get", lambda k: None)

    def _raise(k, v):
        raise OSError("disk full")

    monkeypatch.setattr(osi, "set_secret", _raise)

    with pytest.raises(OSError):
        osi.rotate_operator_service_token()
