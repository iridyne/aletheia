"""Integration test — full universe pipeline with mocked CCXT."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from aletheia.config.schema import UniverseConfig, AletheiaConfig
from aletheia.universe.service import UniverseService
from aletheia.universe.registry import sync_registry
from aletheia.universe.features import collect_features
from aletheia.universe.filters import apply_tradability_filters
from aletheia.universe.scanners import run_all_scanners
from aletheia.universe.allocator import allocate_universe, compute_total_score

# Authoritative registered-family set — derived from run_all_scanners itself so
# these assertions track the code instead of a hardcoded list that silently goes
# stale (this test previously hardcoded 4 families, omitting 'event' and later
# 'short_bias'). all_scores carries every registered family regardless of score.
_VALID_FAMILIES = set(run_all_scanners({}).get("all_scores", {}).keys())


def _make_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(
        enabled=True,
        max_candidates=5,
        core_pool_size=3,
        min_volume_24h_usdt=1_000_000,
        max_spread_bps=30.0,
        explore_ratio=0.2,
        family_quota={"momentum": 0.30, "mean_reversion": 0.30, "volatility": 0.20, "derivatives": 0.20},
        blacklist=["SCAM/USDT"],
    )
    cfg.market_data = MagicMock()
    cfg.market_data.ccxt_exchange = "bitget"
    return cfg


def _fake_features():
    """Simulate features that would come from collect_features."""
    return [
        {
            "asset_id": "a1", "symbol": "DOGE/USDT",
            "quote_volume_24h": 50_000_000, "spread_bps": 5.0,
            "data_freshness_sec": 120, "volume_1h": 3_000_000,
            "rsi_14": 62.0, "atr_pct": 4.5,
            "return_1h": 3.0, "return_4h": 5.5, "return_24h": 8.0,
            "funding_rate": 0.0003, "open_interest": 100_000_000,
            "oi_delta_1h": 5.0,
        },
        {
            "asset_id": "a2", "symbol": "PEPE/USDT",
            "quote_volume_24h": 30_000_000, "spread_bps": 8.0,
            "data_freshness_sec": 200, "volume_1h": 2_000_000,
            "rsi_14": 28.0, "atr_pct": 7.0,
            "return_1h": -4.0, "return_4h": -7.5, "return_24h": -12.0,
            "funding_rate": -0.01, "open_interest": 50_000_000,
            "oi_delta_1h": -8.0,
        },
        {
            "asset_id": "a3", "symbol": "SOL/USDT",
            "quote_volume_24h": 80_000_000, "spread_bps": 3.0,
            "data_freshness_sec": 60, "volume_1h": 5_000_000,
            "rsi_14": 55.0, "atr_pct": 3.0,
            "return_1h": 1.0, "return_4h": 2.0, "return_24h": 4.0,
            "funding_rate": 0.0001, "open_interest": 200_000_000,
            "oi_delta_1h": 2.0,
        },
        {
            "asset_id": "a4", "symbol": "SCAM/USDT",
            "quote_volume_24h": 20_000_000, "spread_bps": 4.0,
            "data_freshness_sec": 150, "volume_1h": 1_500_000,
            "rsi_14": 50.0, "atr_pct": 2.5,
            "return_1h": 0.5, "return_4h": 1.0, "return_24h": 2.0,
            "funding_rate": None, "open_interest": None,
            "oi_delta_1h": None,
        },
        {
            "asset_id": "a5", "symbol": "LOWVOL/USDT",
            "quote_volume_24h": 500_000, "spread_bps": 2.0,
            "data_freshness_sec": 100, "volume_1h": 50_000,
            "rsi_14": 50.0, "atr_pct": 1.0,
            "return_1h": 0.1, "return_4h": 0.2, "return_24h": 0.3,
            "funding_rate": None, "open_interest": None,
            "oi_delta_1h": None,
        },
    ]


def test_full_pipeline_filters_and_scores():
    """End-to-end: features → filter → score → allocate."""
    cfg = _make_cfg()
    features = _fake_features()

    # Step 1: filter
    filtered = apply_tradability_filters(features, cfg.universe)
    symbols = [f["symbol"] for f in filtered]
    assert "SCAM/USDT" not in symbols  # blacklisted
    assert "LOWVOL/USDT" not in symbols  # below volume threshold
    assert "DOGE/USDT" in symbols
    assert "PEPE/USDT" in symbols
    assert "SOL/USDT" in symbols

    # Step 2: scan and score
    scored = []
    for feat in filtered:
        scanner_result = run_all_scanners(feat)
        total = compute_total_score(feat, scanner_result, cfg.universe)
        scored.append({**feat, **scanner_result, "total_score": total})

    assert all(0 <= item["total_score"] <= 100 for item in scored)
    assert all(item["scanner_family"] in _VALID_FAMILIES for item in scored)

    # Step 3: allocate
    allocated = allocate_universe(scored, cfg.universe)
    assert len(allocated) <= cfg.universe.max_candidates
    assert all("ttl_sec" in item for item in allocated)
    assert all("risk_flags" in item for item in allocated)


def test_pipeline_all_blacklisted_returns_empty():
    cfg = _make_cfg()
    cfg.universe.blacklist = ["DOGE/USDT", "PEPE/USDT", "SOL/USDT", "SCAM/USDT", "LOWVOL/USDT"]
    features = _fake_features()
    filtered = apply_tradability_filters(features, cfg.universe)
    assert filtered == []


def test_pipeline_scorer_assigns_families():
    features = _fake_features()
    cfg = _make_cfg()
    filtered = apply_tradability_filters(features, cfg.universe)

    families_seen = set()
    for feat in filtered:
        result = run_all_scanners(feat)
        families_seen.add(result["scanner_family"])

    # At least 1 family assigned (may be all momentum if data skews that way)
    assert len(families_seen) >= 1
    assert all(f in _VALID_FAMILIES for f in families_seen)


def test_pipeline_ttl_varies_by_family():
    cfg = _make_cfg()
    features = _fake_features()
    filtered = apply_tradability_filters(features, cfg.universe)

    scored = []
    for feat in filtered:
        scanner_result = run_all_scanners(feat)
        total = compute_total_score(feat, scanner_result, cfg.universe)
        scored.append({**feat, **scanner_result, "total_score": total})

    allocated = allocate_universe(scored, cfg.universe)
    if len(allocated) >= 2:
        ttls = set(item["ttl_sec"] for item in allocated)
        # Different families → different TTLs (if multiple families selected)
        families = set(item["scanner_family"] for item in allocated)
        if len(families) > 1:
            assert len(ttls) > 1


def test_market_observer_universe_integration():
    """Verify market_observer correctly reads universe members."""
    cfg = _make_cfg()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "SOL/USDT", "scanner_family": "volatility", "total_score": 60,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}

    svc = UniverseService()
    with patch("aletheia.universe.service.load_config", return_value=cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe(limit=5)

    assert len(members) == 2
    # Market observer would consume these as symbol + metadata
    for m in members:
        assert "symbol" in m
        assert "scanner_family" in m
        assert "snapshot_id" in m


def test_opportunity_metadata_fields_exist():
    """Verify Opportunity model accepts universe fields."""
    from aletheia.storage.models import Opportunity
    opp = Opportunity(
        symbol="DOGE/USDT",
        market="spot",
        direction_bias="long",
        thesis="test",
        confidence="medium",
        source_agent="market_observer",
        universe_snapshot_id="snap_123",
        universe_member_id="m_456",
        scanner_family="momentum",
    )
    assert opp.universe_snapshot_id == "snap_123"
    assert opp.universe_member_id == "m_456"
    assert opp.scanner_family == "momentum"
