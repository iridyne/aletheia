"""Tests for live-limited gated path."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_live_disabled_by_default(client):
    resp = await client.get("/api/status")
    assert resp.json()["live_state"] == "live_disabled"


@pytest.mark.anyio
async def test_live_unlock_requires_confirm(client):
    resp = await client.post("/api/settings/live-unlock", json={"confirm": False})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_live_unlock_with_confirm(client):
    # First-ever unlock now requires a second explicit acknowledgment.
    with patch("aletheia.analytics.go_live_scorecard.compute_scorecard") as sc:
        sc.return_value = {"composite_score": 95.0, "traffic_light": "green", "blockers": []}
        resp = await client.post(
            "/api/settings/live-unlock",
            json={"confirm": True, "acknowledge_first_unlock": True},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "live_limited"

    # Verify status reflects change
    resp = await client.get("/api/status")
    assert resp.json()["live_state"] == "live_limited"


@pytest.mark.anyio
async def test_live_lock(client):
    with patch("aletheia.analytics.go_live_scorecard.compute_scorecard") as sc:
        sc.return_value = {"composite_score": 95.0, "traffic_light": "green", "blockers": []}
        await client.post("/api/settings/live-unlock", json={"confirm": True, "acknowledge_first_unlock": True})
    resp = await client.post("/api/settings/live-lock")
    assert resp.status_code == 200
    assert resp.json()["status"] == "live_disabled"


@pytest.mark.anyio
async def test_live_order_blocked_when_disabled(tmp_path, monkeypatch):
    """Verify that the risk engine blocks live orders when live is disabled."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    save_config(cfg)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
    )
    assert result["status"] == "rejected"
    assert "live_disabled" in str(result.get("risk_violations", []))
    reset_db()


# --- Issue #388: trust-boundary state changes must fail-closed when audit write fails ---

@pytest.mark.anyio
async def test_live_unlock_refused_when_audit_write_fails(client, monkeypatch):
    """Issue #388: live_unlock must refuse the state change when the JSONL audit
    write fails.  Before the fix, write_audit() swallowed the error and returned
    without raising; live_unlock ignored the return value and persisted the unlock
    regardless."""
    # live.py binds write_audit at import time, so patch the name in that module.
    import aletheia.server.routes.live as live_mod
    monkeypatch.setattr(live_mod, "write_audit", lambda *a, **kw: False)

    with patch("aletheia.analytics.go_live_scorecard.compute_scorecard") as sc:
        sc.return_value = {"composite_score": 95.0, "traffic_light": "green", "blockers": []}
        resp = await client.post(
            "/api/settings/live-unlock",
            json={"confirm": True, "acknowledge_first_unlock": True},
        )

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "audit_write_failed"

    # Config must NOT have been mutated — live state stays disabled.
    status = await client.get("/api/status")
    assert status.json()["live_state"] == "live_disabled"


@pytest.mark.anyio
async def test_live_lock_proceeds_with_audit_warning_on_write_failure(client, monkeypatch):
    """Issue #388: live_lock (a safety action) must still apply the lock even
    when audit write fails, but must surface an audit_warning in the response
    so operators know the chain is incomplete."""
    # First unlock successfully.
    with patch("aletheia.analytics.go_live_scorecard.compute_scorecard") as sc:
        sc.return_value = {"composite_score": 95.0, "traffic_light": "green", "blockers": []}
        await client.post("/api/settings/live-unlock",
                          json={"confirm": True, "acknowledge_first_unlock": True})

    import aletheia.server.routes.live as live_mod
    monkeypatch.setattr(live_mod, "write_audit", lambda *a, **kw: False)

    resp = await client.post("/api/settings/live-lock")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "live_disabled"
    assert "audit_warning" in data, "live_lock must surface audit failure as audit_warning"

    status = await client.get("/api/status")
    assert status.json()["live_state"] == "live_disabled"
