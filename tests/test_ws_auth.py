"""Tests for WebSocket authentication boundary (#1214).

Covers:
  - token-required + no credentials → closed with 4401 before accept
  - token-required + valid X-Aletheia-Token header → accepted
  - token-required + valid ticket (existing browser path) → accepted
  - frictionless local mode → accepted without any credential
"""
from __future__ import annotations


import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_fingerprints(queries):
    return {k: "0" for k in queries}


class _NoopHub:
    def __init__(self):
        self.clients: set = set()

    async def add(self, ws) -> None:
        self.clients.add(ws)

    async def remove(self, ws) -> None:
        self.clients.discard(ws)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_ws_rejected_no_credentials_when_auth_required(monkeypatch):
    """No ticket, no header → closed with 4401 (unauthorised) before accept."""
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws"):
            pass  # pragma: no cover


def test_ws_accepted_with_valid_header_token_when_auth_required(monkeypatch):
    """A valid X-Aletheia-Token header is accepted as an alternative to a ticket."""
    from fastapi.testclient import TestClient
    import aletheia.config.secrets as secret_store
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)
    monkeypatch.setattr(ws_routes, "_run_fingerprints", _fake_fingerprints)
    monkeypatch.setattr(ws_routes, "_hub", _NoopHub())
    monkeypatch.setattr(
        secret_store, "get_secret",
        lambda key: "test-owner-token" if key == "remote_control_api_token" else None,
    )

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with client.websocket_connect(
        "/api/ws",
        headers={"X-Aletheia-Token": "test-owner-token"},
    ) as ws:
        data = ws.receive_json()
        assert data["type"] == "update"
        assert isinstance(data["changed"], list)


def test_ws_rejected_with_wrong_header_token_when_auth_required(monkeypatch):
    """Wrong X-Aletheia-Token header → rejected."""
    from fastapi.testclient import TestClient
    import aletheia.config.secrets as secret_store
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)
    monkeypatch.setattr(
        secret_store, "get_secret",
        lambda key: "real-token" if key == "remote_control_api_token" else None,
    )

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/api/ws",
            headers={"X-Aletheia-Token": "wrong-token"},
        ):
            pass  # pragma: no cover


def test_ws_accepted_with_valid_ticket_when_auth_required(monkeypatch):
    """Existing browser ticket path still works (regression guard)."""
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)
    monkeypatch.setattr(ws_routes, "_run_fingerprints", _fake_fingerprints)
    monkeypatch.setattr(ws_routes, "_hub", _NoopHub())

    ticket = ws_routes._issue_ws_ticket()
    client = TestClient(app_module.app, raise_server_exceptions=False)

    with client.websocket_connect(f"/api/ws?ticket={ticket}") as ws:
        data = ws.receive_json()
        assert data["type"] == "update"
        assert isinstance(data["changed"], list)


def test_ws_accepted_without_credentials_in_local_mode(monkeypatch):
    """Frictionless local mode: no ticket, no header → accepted."""
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: False)
    monkeypatch.setattr(ws_routes, "_run_fingerprints", _fake_fingerprints)
    monkeypatch.setattr(ws_routes, "_hub", _NoopHub())

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with client.websocket_connect("/api/ws") as ws:
        data = ws.receive_json()
        assert data["type"] == "update"
