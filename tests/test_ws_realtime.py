from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


def test_fingerprint_queries_match_the_migrated_schema():
    from aletheia.server.routes import ws as ws_routes
    from aletheia.storage.sqlite import get_db

    db = get_db()
    queries = (
        *ws_routes._REGULAR_QUERIES.values(),
        *ws_routes._ALERT_DB_QUERIES.values(),
    )
    for query in queries:
        db.execute(query).fetchone()


def test_position_fingerprint_changes_on_insert_and_update():
    from aletheia.server.routes import ws as ws_routes
    from aletheia.storage.sqlite import get_db

    db = get_db()
    query = {"positions": ws_routes._REGULAR_QUERIES["positions"]}
    empty = ws_routes._run_fingerprints(query)["positions"]
    db.execute(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,execution_mode) "
        "VALUES ('position-ws','BTC/USDT','spot','long',1,100,'paper_internal')"
    )
    inserted = ws_routes._run_fingerprints(query)["positions"]
    db.execute(
        "UPDATE positions SET current_price=101 WHERE id='position-ws'"
    )
    updated = ws_routes._run_fingerprints(query)["positions"]

    assert empty == "0"
    assert inserted != empty
    assert updated != inserted


@pytest.mark.asyncio
async def test_realtime_hub_shares_one_sampler_pair_across_clients(monkeypatch):
    from aletheia.server.routes import ws as ws_routes

    release = asyncio.Event()
    starts = {"alert": 0, "regular": 0}

    async def fake_alert(_hub, _loop):
        starts["alert"] += 1
        await release.wait()

    async def fake_regular(_hub, _loop):
        starts["regular"] += 1
        await release.wait()

    monkeypatch.setattr(ws_routes, "_alert_loop", fake_alert)
    monkeypatch.setattr(ws_routes, "_regular_loop", fake_regular)

    hub = ws_routes._RealtimeHub()
    first = object()
    second = object()

    await hub.add(first)
    first_tasks = (hub.alert_task, hub.regular_task)
    await hub.add(second)
    await asyncio.sleep(0)

    assert starts == {"alert": 1, "regular": 1}
    assert (hub.alert_task, hub.regular_task) == first_tasks

    await hub.remove(first)
    assert hub.alert_task is first_tasks[0]
    assert hub.regular_task is first_tasks[1]

    release.set()
    await hub.remove(second)
    assert hub.alert_task is None
    assert hub.regular_task is None


# ── WebSocket auth enforcement tests ─────────────────────────────────────────

def _fake_fingerprints(queries):
    return {k: "0" for k in queries}


def test_ws_ticket_issuance_and_single_use():
    from aletheia.server.routes.ws import _issue_ws_ticket, _consume_ws_ticket

    ticket = _issue_ws_ticket()
    assert len(ticket) > 20
    assert _consume_ws_ticket(ticket) is True
    # Second use of the same ticket is rejected (single-use)
    assert _consume_ws_ticket(ticket) is False


def test_ws_ticket_expiry(monkeypatch):
    import time as _time
    from aletheia.server.routes import ws as ws_routes

    monkeypatch.setattr(ws_routes, "_WS_TICKET_TTL", -1)  # already expired
    ticket = ws_routes._issue_ws_ticket()
    assert ws_routes._consume_ws_ticket(ticket) is False


def test_ws_rejected_without_ticket_when_auth_required(monkeypatch):
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws"):
            pass  # pragma: no cover


def test_ws_rejected_with_invalid_ticket_when_auth_required(monkeypatch):
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)

    client = TestClient(app_module.app, raise_server_exceptions=False)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws?ticket=not-a-real-ticket"):
            pass  # pragma: no cover


class _NoopHub:
    """Stand-in hub that records registrations without starting asyncio tasks."""

    def __init__(self):
        self.clients: set = set()

    async def add(self, ws) -> None:
        self.clients.add(ws)

    async def remove(self, ws) -> None:
        self.clients.discard(ws)


def test_ws_accepted_with_valid_ticket_when_auth_required(monkeypatch):
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


def test_ws_ticket_single_use_via_connection(monkeypatch):
    from fastapi.testclient import TestClient
    import aletheia.server.routes.ws as ws_routes
    import aletheia.server.app as app_module

    monkeypatch.setattr(ws_routes, "_ws_remote_auth_required", lambda: True)
    monkeypatch.setattr(ws_routes, "_run_fingerprints", _fake_fingerprints)
    monkeypatch.setattr(ws_routes, "_hub", _NoopHub())

    ticket = ws_routes._issue_ws_ticket()
    client = TestClient(app_module.app, raise_server_exceptions=False)

    with client.websocket_connect(f"/api/ws?ticket={ticket}") as ws:
        ws.receive_json()

    # Same ticket rejected on second connection attempt
    with pytest.raises(Exception):
        with client.websocket_connect(f"/api/ws?ticket={ticket}"):
            pass  # pragma: no cover


def test_ws_accepted_without_ticket_in_local_mode(monkeypatch):
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


def test_ws_ticket_endpoint_requires_owner_auth_when_remote_auth_active(monkeypatch):
    """GET /api/ws/ticket is behind HTTP auth when remote auth is active."""
    from fastapi.testclient import TestClient
    import aletheia.config.secrets as secret_store
    import aletheia.server.app as app_module

    def _auth_cfg():
        return SimpleNamespace(
            tunnel=SimpleNamespace(enabled=False),
            remote_control=SimpleNamespace(enabled=True, require_token=True),
        )

    monkeypatch.setattr(app_module, "load_config", lambda persist_migrations=False: _auth_cfg())
    monkeypatch.setattr(
        secret_store, "get_secret",
        lambda key: "owner-token" if key == "remote_control_api_token" else None,
    )

    client = TestClient(app_module.app)
    assert client.get("/api/ws/ticket").status_code == 401
    resp = client.get("/api/ws/ticket", headers={"X-Aletheia-Token": "owner-token"})
    assert resp.status_code == 200
    data = resp.json()
    assert "ticket" in data
    assert "ttl_seconds" in data
