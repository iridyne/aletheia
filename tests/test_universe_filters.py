"""Tests for universe tradability filters."""

import pytest
from aletheia.universe.filters import apply_tradability_filters, _compute_tradability_score
from aletheia.config.schema import UniverseConfig


@pytest.fixture
def cfg():
    return UniverseConfig(
        min_volume_24h_usdt=5_000_000,
        max_spread_bps=25.0,
        blacklist=["SCAM/USDT"],
    )


def _make_feat(symbol="BTC/USDT", volume=10_000_000, spread=5.0, freshness=300):
    return {
        "asset_id": "a1",
        "feature_snapshot_id": f"feature:{symbol}",
        "symbol": symbol,
        "venue": "okx",
        "market_type": "spot",
        "quote_volume_24h": volume,
        "spread_bps": spread,
        "data_freshness_sec": freshness,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "return_24h": 5.0,
        "rsi_14": 55.0,
        "atr_pct": 2.5,
    }


def test_volume_filter_pass(cfg):
    features = [_make_feat(volume=10_000_000)]
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 1


def test_volume_filter_fail(cfg):
    features = [_make_feat(volume=1_000_000)]
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 0


def test_spread_filter_pass(cfg):
    features = [_make_feat(spread=20.0)]
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 1


def test_spread_filter_extreme_fail(cfg):
    features = [_make_feat(spread=60.0)]  # > max_spread_bps * 2
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 0


def test_spread_none_passes(cfg):
    feat = _make_feat()
    feat["spread_bps"] = None
    result = apply_tradability_filters([feat], cfg)
    assert len(result) == 1


def test_blacklist_exclusion(cfg):
    features = [_make_feat(symbol="SCAM/USDT")]
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 0


def test_data_freshness_filter_pass(cfg):
    features = [_make_feat(freshness=600)]
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 1


def test_data_freshness_filter_stale(cfg):
    features = [_make_feat(freshness=8000)]  # > 7200
    result = apply_tradability_filters(features, cfg)
    assert len(result) == 0


def test_tradability_score_high_quality(cfg):
    feat = _make_feat(volume=50_000_000, spread=3.0, freshness=60)
    score = _compute_tradability_score(feat, cfg)
    assert score >= 80


def test_tradability_score_minimum(cfg):
    feat = _make_feat(volume=5_000_000, spread=25.0, freshness=3000)
    score = _compute_tradability_score(feat, cfg)
    assert 30 <= score <= 70


def test_multiple_assets_filter(cfg):
    features = [
        _make_feat("BTC/USDT", volume=50_000_000, spread=3.0),
        _make_feat("LOWVOL/USDT", volume=100_000, spread=5.0),
        _make_feat("ETH/USDT", volume=30_000_000, spread=8.0),
        _make_feat("SCAM/USDT", volume=20_000_000, spread=2.0),
    ]
    result = apply_tradability_filters(features, cfg)
    symbols = [r["symbol"] for r in result]
    assert "BTC/USDT" in symbols
    assert "ETH/USDT" in symbols
    assert "LOWVOL/USDT" not in symbols
    assert "SCAM/USDT" not in symbols


def test_gold_backed_rwa_is_not_classified_as_a_stablecoin(cfg):
    features = [
        _make_feat("XAUT/USDT", volume=20_000_000),
        _make_feat("PAXG/USDT", volume=20_000_000),
        _make_feat("USDC/USDT", volume=20_000_000),
    ]

    result = apply_tradability_filters(features, cfg)

    assert {row["symbol"] for row in result} == {"XAUT/USDT", "PAXG/USDT"}


def test_tradability_evidence_records_first_rejection_then_not_applicable(cfg):
    from aletheia.analytics.filter_evidence import decode_filter_decision_batch
    from aletheia.storage.sqlite import get_db

    btc = _make_feat("BTC/USDT", volume=20_000_000)
    stable = _make_feat("USDC/USDT", volume=20_000_000)
    stable["asset_id"] = "stable"
    stable["feature_snapshot_id"] = "feature:stable"

    result = apply_tradability_filters(
        [btc, stable], cfg, scanner_run_id="tradability-run",
    )

    assert [row["symbol"] for row in result] == ["BTC/USDT"]
    rows = get_db().execute(
        "SELECT filter_name, candidate_count, candidate_facts "
        "FROM filter_decision_batches WHERE run_id=? ORDER BY filter_name",
        ("tradability-run",),
    ).fetchall()
    assert len(rows) == 5
    facts_by_filter = {
        row["filter_name"]: decode_filter_decision_batch(row["candidate_facts"])
        for row in rows
    }
    stablecoin_facts = facts_by_filter["tradability_stablecoin"]
    assert [fact["decision"] for fact in stablecoin_facts] == ["passed", "rejected"]
    blacklist_facts = facts_by_filter["tradability_blacklist"]
    assert [fact["decision"] for fact in blacklist_facts] == [
        "passed", "not_applicable",
    ]
