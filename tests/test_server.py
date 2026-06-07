"""Integration tests for the FastAPI server."""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations
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
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    # /api/health now reports real health: "healthy" or "degraded".
    assert data["status"] in ("healthy", "degraded")
    assert "version" in data


@pytest.mark.anyio
async def test_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["live_state"] == "live_disabled"
    assert data["permission_mode"] == "Research"
    assert data["exchange_mode"] == "demo"


@pytest.mark.anyio
async def test_pause_resume(client):
    resp = await client.post("/api/gateway/pause")
    assert resp.status_code == 200
    assert resp.json()["paused"] is True

    resp = await client.get("/api/status")
    assert resp.json()["paused"] is True

    resp = await client.post("/api/gateway/resume")
    assert resp.status_code == 200
    assert resp.json()["paused"] is False


@pytest.mark.anyio
async def test_permission_mode_change(client):
    resp = await client.post("/api/settings/permission-mode", json={"mode": "PaperAuto"})
    assert resp.status_code == 200
    assert resp.json()["new"] == "PaperAuto"

    resp = await client.get("/api/status")
    assert resp.json()["permission_mode"] == "PaperAuto"


@pytest.mark.anyio
async def test_permission_mode_invalid(client):
    resp = await client.post("/api/settings/permission-mode", json={"mode": "invalid"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_exchange_mode_change(client):
    resp = await client.post("/api/settings/exchange-mode", json={"mode": "live"})
    assert resp.status_code == 200
    assert resp.json()["new"] == "live"


@pytest.mark.anyio
async def test_settings_get(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app"]["dashboard_port"] == 8787
    assert data["risk"]["max_daily_loss_pct"] == 1.0
