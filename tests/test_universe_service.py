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


def test_active_paper_learning_scope_returns_exact_single_scope():
    from aletheia.universe.service import _active_paper_learning_scope

    scope = {"family": "momentum", "direction": "long", "market_type": "spot"}
    with (
        patch(
            "aletheia.learning.ledger.list_open_experiments",
            return_value=[
                {"stage": "shadow", "hypothesis_id": "shadow"},
                {"stage": "paper_10", "hypothesis_id": "paper"},
            ],
        ),
        patch(
            "aletheia.learning.ledger.get_record",
            return_value={"scope_json": json.dumps(scope)},
        ),
    ):
        assert _active_paper_learning_scope(
            "universe_family_calibration"
        ) == (True, scope)


def test_active_paper_learning_scope_fails_closed_on_ambiguity():
    from aletheia.universe.service import _active_paper_learning_scope

    with patch(
        "aletheia.learning.ledger.list_open_experiments",
        return_value=[
            {"stage": "paper_10", "hypothesis_id": "first"},
            {"stage": "paper_25", "hypothesis_id": "second"},
        ],
    ):
        assert _active_paper_learning_scope(
            "universe_family_calibration"
        ) == (True, None)


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


def test_change_suppression_fingerprint_covers_carrier_semantics():
    """A reused snapshot may not silently preserve stale execution semantics."""
    from copy import deepcopy

    from aletheia.universe.service import _member_fingerprint

    base = [{
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "venue": "binance",
        "market_type": "perp",
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "execution_eligible": True,
        "ttl_sec": 1800,
        "risk_flags": ["crowded"],
        "last_price": 60_000.0,
    }]
    baseline = _member_fingerprint(base, "risk_on")
    variants = {
        "venue": "okx",
        "scanner_direction": "short",
        "execution_eligible": False,
        "ttl_sec": 3600,
        "risk_flags": ["wide_spread"],
        "last_price": 60_001.0,
    }

    for field, value in variants.items():
        changed = deepcopy(base)
        changed[0][field] = value
        assert _member_fingerprint(changed, "risk_on") != baseline, field


def test_previous_snapshot_fingerprint_reconstructs_same_semantic_payload(monkeypatch):
    from aletheia.universe import service

    allocated = [{
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "venue": "binance",
        "market_type": "perp",
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "execution_eligible": True,
        "ttl_sec": 1800,
        "risk_flags": ["crowded"],
        "last_price": 60_000.0,
    }]
    member = {
        "asset_id": "asset-1",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "rank": 1,
        "scanner_family": "momentum",
        "scanner_direction": "long",
        "execution_eligible": 1,
        "ttl_sec": 1800,
        "risk_flags": '["crowded"]',
        "entry_price_usd": 60_000.0,
        "regime_tag": "risk_on",
    }
    monkeypatch.setattr(service.repo, "get_active_universe_snapshot", lambda _: {
        "id": "snap-1", "created_at": datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(service.repo, "list_universe_members", lambda *_args, **_kwargs: [member])
    monkeypatch.setattr(service.repo, "get_universe_asset", lambda _: {"venue": "binance"})

    previous, snapshot_id, _ = service._previous_snapshot_fingerprint("crypto", "risk_on")

    assert snapshot_id == "snap-1"
    assert previous == service._member_fingerprint(allocated, "risk_on")


def test_fallback_static_universe(svc, disabled_cfg):
    with patch("aletheia.universe.service.load_config", return_value=disabled_cfg):
        symbols = svc.fallback_static_universe()
    assert symbols == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


def test_feature_refresh_nudges_operator_daemon(monkeypatch):
    from aletheia.universe.service import _notify_operator_feature_refresh

    calls = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.notify_pi_daemon",
        lambda *, source: calls.append(source) or True,
    )

    assert _notify_operator_feature_refresh("universe_feature_refresh_test") is True
    assert calls == ["universe_feature_refresh_test"]


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
