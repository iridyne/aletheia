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
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
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
