"""Tests for UniverseService — fallback behavior, get_current_universe, refresh."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from aletheia.universe.service import UniverseService
from aletheia.config.schema import UniverseConfig, AletheiaConfig


@pytest.fixture
def svc():
    return UniverseService()


@pytest.fixture
def disabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=False)
    return cfg


@pytest.fixture
def enabled_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(enabled=True, fallback_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    return cfg


def test_fallback_when_disabled(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"
    assert members[0]["scanner_family"] == "core"
    assert members[0]["snapshot_id"] is None


def test_fallback_when_no_snapshot(svc, enabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=None):
            members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"


def test_fallback_when_snapshot_empty(svc, enabled_cfg):
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=[]):
                members = svc.get_current_universe()
    assert len(members) == 3
    assert members[0]["scanner_family"] == "core"


def test_returns_active_members(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "PEPE/USDT", "scanner_family": "volatility", "total_score": 60,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 2
    assert members[0]["symbol"] == "DOGE/USDT"


def test_filters_expired_members(svc, enabled_cfg):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "FRESH/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "STALE/USDT", "scanner_family": "momentum", "total_score": 70,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": past, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 1
    assert members[0]["symbol"] == "FRESH/USDT"


def test_filters_non_execution_eligible(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "GOOD/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "BAD/USDT", "scanner_family": "momentum", "total_score": 70,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 0,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    assert len(members) == 1
    assert members[0]["symbol"] == "GOOD/USDT"


def test_fallback_all_expired(svc, enabled_cfg):
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    mock_members = [
        {"symbol": "OLD/USDT", "scanner_family": "momentum", "total_score": 80,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": past, "rank": 1},
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe()
    # Should fallback since all expired
    assert len(members) == 3
    assert members[0]["symbol"] == "BTC/USDT"


def test_limit_parameter(svc, enabled_cfg):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": f"SYM{i}/USDT", "scanner_family": "momentum", "total_score": 90 - i,
         "snapshot_id": "snap_1", "id": f"m{i}", "execution_eligible": 1,
         "expires_at": future, "rank": i}
        for i in range(10)
    ]
    snap = {"id": "snap_1", "status": "active"}
    with patch("aletheia.universe.service.load_config", return_value=enabled_cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe(limit=3)
    assert len(members) == 3


def test_fallback_static_universe(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        symbols = svc.fallback_static_universe()
    assert symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


@pytest.mark.asyncio
async def test_refresh_returns_none_on_failure(svc):
    mock_cfg = MagicMock()
    mock_cfg.universe = UniverseConfig(enabled=True)
    with patch("aletheia.universe.service.load_config", return_value=mock_cfg):
        with patch("aletheia.universe.service.repo.insert_universe_scanner_run"):
            with patch("aletheia.universe.service.repo.update_universe_scanner_run"):
                with patch("aletheia.universe.registry.sync_registry", side_effect=Exception("net")):
                    result = await svc.refresh()
    assert result is None
