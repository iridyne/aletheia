"""Tests for universe API routes."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from aletheia.config.schema import UniverseConfig


@pytest.fixture
def client():
    from aletheia.server.app import app
    return TestClient(app)


@pytest.fixture
def disabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=False)
    cfg.market_data = MagicMock()
    return cfg


@pytest.fixture
def enabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True)
    cfg.market_data = MagicMock()
    return cfg


def test_get_current_when_disabled(client, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
            with patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=None):
                resp = client.get("/api/universe/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["fallback"] is True
    assert data["enabled"] is False
    assert len(data["members"]) == 3


def test_get_current_with_active_snapshot(client, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
    ]
    snap = {"id": "snap_1", "status": "active", "created_at": "2025-01-01T00:00:00"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.server.routes.universe.load_config", return_value=enabled_cfg):
            with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
                with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                    with patch("aletheia.server.routes.universe.repo.get_active_universe_snapshot", return_value=snap):
                        resp = client.get("/api/universe/current")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert len(data["members"]) == 1
    assert data["members"][0]["symbol"] == "DOGE/USDT"


def test_get_current_uses_service_snapshot_namespace_with_mixed_config(client):
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, market_type="mixed")
    cfg.market_data = MagicMock()
    snap = {
        "id": "snap_mixed_assets",
        "status": "active",
        "summary": '{"scanned": 1000, "filtered": 245, "selected": 28}',
    }
    members = [{"id": "member_1", "snapshot_id": snap["id"], "symbol": "BTC/USDT"}]

    with patch("aletheia.universe.service.load_config", return_value=cfg), \
         patch("aletheia.server.routes.universe.load_config", return_value=cfg), \
         patch(
             "aletheia.server.routes.universe.repo.get_active_universe_snapshot",
             return_value=snap,
         ) as get_snapshot, \
         patch(
             "aletheia.universe.service.UniverseService.get_current_universe",
             return_value=members,
         ):
        resp = client.get("/api/universe/current")

    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot"] == snap
    assert data["members"] == members
    assert data["fallback"] is False
    get_snapshot.assert_called_once_with()


def test_refresh_when_disabled(client, disabled_cfg):
    with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
        resp = client.post("/api/universe/refresh")
    assert resp.status_code == 400
    assert "disabled" in resp.json()["detail"]


def test_refresh_uses_service_snapshot_namespace_with_mixed_config(client):
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, market_type="mixed")

    with patch("aletheia.server.routes.universe.load_config", return_value=cfg), \
         patch(
             "aletheia.universe.service.UniverseService.refresh",
             new_callable=AsyncMock,
             return_value="snap_1",
         ) as refresh:
        resp = client.post("/api/universe/refresh")

    assert resp.status_code == 200
    assert resp.json()["snapshot_id"] == "snap_1"
    refresh.assert_awaited_once_with()


def test_list_snapshots(client):
    mock_snaps = [
        {"id": "s1", "status": "active", "created_at": "2025-01-01"},
        {"id": "s2", "status": "superseded", "created_at": "2024-12-31"},
    ]
    with patch("aletheia.server.routes.universe.repo.list_universe_snapshots", return_value=mock_snaps):
        resp = client.get("/api/universe/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_get_policy_returns_config_defaults(client, disabled_cfg):
    with patch("aletheia.server.routes.universe.load_config", return_value=disabled_cfg):
        with patch("aletheia.server.routes.universe.repo.get_active_universe_policy", return_value=None):
            resp = client.get("/api/universe/policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "config_defaults"
    assert data["explore_ratio"] == 0.2


def test_get_member_not_found(client):
    with patch("aletheia.server.routes.universe.repo.get_universe_member", return_value=None):
        resp = client.get("/api/universe/members/nonexistent")
    assert resp.status_code == 404


def test_get_member_found(client):
    member = {"id": "m1", "asset_id": "a1", "symbol": "BTC/USDT", "scanner_family": "momentum", "rank": 1}
    features = {"id": "f1", "asset_id": "a1", "rsi_14": 55.0, "atr_pct": 2.1}
    opps = [{"id": "o1", "symbol": "BTC/USDT", "status": "open", "direction_bias": "long", "created_at": "2026-01-01T00:00:00"}]
    with patch("aletheia.server.routes.universe.repo.get_universe_member", return_value=member), \
         patch("aletheia.server.routes.universe.repo.get_latest_feature_snapshot", return_value=features), \
         patch("aletheia.server.routes.universe.repo.list_opportunities_by_member", return_value=opps):
        resp = client.get("/api/universe/members/m1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["member"]["symbol"] == "BTC/USDT"
    assert body["features"]["rsi_14"] == 55.0
    assert len(body["opportunities"]) == 1


def test_registry_stats(client):
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = {"c": 100}
    with patch("aletheia.storage.sqlite.get_db", return_value=mock_db):
        resp = client.get("/api/universe/registry/stats")
    assert resp.status_code == 200
