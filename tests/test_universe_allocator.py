"""Tests for universe allocator — scoring, quota, TTL, explore/exploit."""

import pytest
from aletheia.universe.allocator import (
    compute_total_score, allocate_universe, compute_late_entry_penalty,
    get_family_multipliers,
)
from aletheia.config.schema import UniverseConfig


@pytest.fixture
def cfg():
    return UniverseConfig(
        max_candidates=10,
        core_pool_size=5,
        explore_ratio=0.2,
        family_quota={"momentum": 0.30, "mean_reversion": 0.30, "volatility": 0.20, "derivatives": 0.20},
    )


def _scored_item(symbol, family, score, **kwargs):
    item = {
        "asset_id": f"id_{symbol}",
        "symbol": symbol,
        "scanner_family": family,
        "opportunity_score": score,
        "total_score": score,
        "tradability_score": 70.0,
        "quote_volume_24h": 10_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 300,
    }
    item.update(kwargs)
    return item


def test_family_quota_enforcement(cfg):
    items = []
    for i in range(15):
        items.append(_scored_item(f"MOM{i}", "momentum", 90 - i))
    for i in range(5):
        items.append(_scored_item(f"MR{i}", "mean_reversion", 80 - i))

    result = allocate_universe(items, cfg)
    momentum_count = sum(1 for r in result if r["scanner_family"] == "momentum")
    # Exploit quota is 30% of 10 = 3, explore may randomly add more from remainder
    # But exploit-only should be capped; total with explore should stay reasonable
    exploit_count = int(cfg.max_candidates * (1 - cfg.explore_ratio))  # 8
    exploit_momentum = min(3, exploit_count)  # at most 3 in exploit phase
    assert momentum_count <= exploit_momentum + cfg.max_candidates - exploit_count  # 3 + 2 = 5 max from explore


def test_explore_exploit_split(cfg):
    items = [_scored_item(f"S{i}", "momentum", 90 - i * 5) for i in range(20)]
    result = allocate_universe(items, cfg)
    assert len(result) <= cfg.max_candidates


def test_ttl_assignment_by_family(cfg):
    items = [
        _scored_item("MOM1", "momentum", 80),
        _scored_item("MR1", "mean_reversion", 75),
        _scored_item("VOL1", "volatility", 70),
        _scored_item("DER1", "derivatives", 65),
    ]
    result = allocate_universe(items, cfg)
    ttls = {r["scanner_family"]: r["ttl_sec"] for r in result}
    assert ttls.get("momentum", 0) < ttls.get("derivatives", 0)


def test_allocator_checks_freshness_for_the_selected_market_type(cfg, monkeypatch):
    item = _scored_item("BTC/USDT", "momentum", 80, market_type="perp")
    seen: list[tuple[str, str]] = []

    def _fresh(symbol: str, market_type: str) -> bool:
        seen.append((symbol, market_type))
        return False

    monkeypatch.setattr("aletheia.universe.allocator.has_fresh_market_snapshot", _fresh)

    result = allocate_universe([item], cfg)

    assert seen == [("BTC/USDT", "perp")]
    assert result[0]["execution_eligible"] is False


def test_late_entry_penalty_extreme_return():
    feat = {"return_1h": 7.0, "return_4h": 12.0, "rsi_14": 87}
    penalty = compute_late_entry_penalty(feat)
    assert penalty >= 25


def test_late_entry_penalty_normal():
    feat = {"return_1h": 0.5, "return_4h": 1.0, "rsi_14": 55}
    penalty = compute_late_entry_penalty(feat)
    assert penalty == 0.0


def test_total_score_formula(cfg):
    feat = {
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 200,
    }
    scanner_result = {"opportunity_score": 70.0}
    score = compute_total_score(feat, scanner_result, cfg)
    assert 0.0 <= score <= 100.0
    assert score > 20  # should be decent with good inputs


def test_total_score_penalizes_stale_data(cfg):
    feat_fresh = {
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 100,
    }
    feat_stale = {**feat_fresh, "data_freshness_sec": 5000}
    scanner = {"opportunity_score": 70.0}
    score_fresh = compute_total_score(feat_fresh, scanner, cfg)
    score_stale = compute_total_score(feat_stale, scanner, cfg)
    assert score_fresh > score_stale


def test_max_candidates_cap(cfg):
    items = [_scored_item(f"S{i}", "momentum", 90 - i) for i in range(50)]
    result = allocate_universe(items, cfg)
    assert len(result) <= cfg.max_candidates


def test_empty_input():
    cfg = UniverseConfig()
    result = allocate_universe([], cfg)
    assert result == []


def test_core_vs_rotating_split(cfg):
    items = [
        _scored_item(f"HIGH{i}", "momentum", 90 - i) for i in range(5)
    ] + [
        _scored_item(f"MED{i}", "mean_reversion", 50 - i) for i in range(10)
    ]
    result = allocate_universe(items, cfg)
    # Top items by score should be in the result
    top_symbols = {r["symbol"] for r in result[:3]}
    assert "HIGH0" in top_symbols


# --- Calibration feedback tests ---


def test_calibration_boost_is_refrozen_without_candidate_identity(cfg):
    """Retrospective calibration alone cannot boost a candidate."""
    feat = {
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 200,
    }
    scanner_result = {"opportunity_score": 70.0, "scanner_family": "momentum"}
    score_neutral = compute_total_score(feat, scanner_result, cfg, calibration=None)
    score_boosted = compute_total_score(
        feat, scanner_result, cfg, calibration={"momentum": 1.15}
    )
    assert score_boosted == score_neutral


def test_calibration_penalty_is_refrozen_without_candidate_identity(cfg):
    """Retrospective calibration alone cannot suppress a candidate."""
    feat = {
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 200,
    }
    scanner_result = {"opportunity_score": 70.0, "scanner_family": "momentum"}
    score_neutral = compute_total_score(feat, scanner_result, cfg, calibration=None)
    score_penalized = compute_total_score(
        feat, scanner_result, cfg, calibration={"momentum": 0.85}
    )
    assert score_penalized == score_neutral


def test_calibration_no_effect_on_unknown_family(cfg):
    """Calibration dict without the family key → multiplier defaults to 1.0."""
    feat = {
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "data_freshness_sec": 200,
    }
    scanner_result = {"opportunity_score": 70.0, "scanner_family": "volatility"}
    score_neutral = compute_total_score(feat, scanner_result, cfg, calibration=None)
    score_with_other = compute_total_score(
        feat, scanner_result, cfg, calibration={"momentum": 1.15}
    )
    assert score_neutral == score_with_other


def test_get_family_multipliers_empty_db(monkeypatch):
    """With no calibration data, returns empty dict."""
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "calibration_stats", lambda: [])
    result = get_family_multipliers()
    assert result == {}


def test_get_family_multipliers_insufficient_samples(monkeypatch):
    """Families below _MIN_CALIBRATION_SAMPLES threshold get no multiplier."""
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "calibration_stats", lambda: [
        {"scanner_family": "momentum", "horizon": "4h", "n": 5, "win_count": 4},
    ])
    result = get_family_multipliers()
    assert "momentum" not in result


def test_get_family_multipliers_above_threshold(monkeypatch):
    """Family with enough samples and positive net expectancy gets boost > 1.0."""
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "calibration_stats", lambda: [
        {"scanner_family": "momentum", "horizon": "4h", "n": 50,
         "win_count": 35, "avg_return": 1.5, "min_return": -2.0},
    ])
    evidence = {}
    result = get_family_multipliers(evidence_out=evidence)
    assert "momentum" in result
    assert result["momentum"] > 1.0
    assert result["momentum"] <= 1.10  # bounded to 1.0 + max_step
    assert evidence["momentum"]["source"] == "universe_forward_outcomes"
    assert evidence["momentum"]["eligible_outcome_n"] == 50
    assert len(evidence["momentum"]["evidence_fingerprint"]) == 64

    repeated = {}
    get_family_multipliers(evidence_out=repeated)
    assert repeated == evidence


def test_calibration_keeps_direction_and_market_cohorts_separate(monkeypatch):
    import aletheia.storage.repositories as _repo

    monkeypatch.setattr(_repo, "calibration_stats", lambda: [
        {
            "scanner_family": "momentum", "direction": "long", "market_type": "spot",
            "horizon": "4h", "n": 50, "win_count": 38, "avg_return": 1.2, "min_return": -1.5,
        },
        {
            "scanner_family": "momentum", "direction": "short", "market_type": "perp",
            "horizon": "4h", "n": 50, "win_count": 12, "avg_return": -1.2, "min_return": -3.0,
        },
    ])

    result = get_family_multipliers()

    assert result["momentum:long:spot"] > 1.0
    assert result["momentum:short:perp"] < 1.0
    assert "momentum" not in result


def test_calibration_penalizes_high_winrate_negative_expectancy(monkeypatch):
    """P1-3: a family that wins often but bleeds money (small wins, big losses → negative
    net expectancy) is PENALIZED, not boosted. Win rate alone would have boosted it."""
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "calibration_stats", lambda: [
        {"scanner_family": "momentum", "horizon": "4h", "n": 60,
         "win_count": 48, "avg_return": -0.8, "min_return": -6.0},  # 80% win rate, negative net
    ])
    result = get_family_multipliers()
    assert result["momentum"] < 1.0  # penalized on net expectancy, despite 80% win rate


def test_calibration_downside_harm_forbids_boost(monkeypatch):
    """P1-3: a positive-mean family with a catastrophic worst-case outcome gets no boost."""
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "calibration_stats", lambda: [
        {"scanner_family": "momentum", "horizon": "4h", "n": 60,
         "win_count": 40, "avg_return": 1.0, "min_return": -30.0},  # positive mean, fat tail
    ])
    result = get_family_multipliers()
    # No boost — the multiplier is neutral (1.0), never > 1.0, because of the tail risk.
    assert result.get("momentum", 1.0) <= 1.0


def test_total_score_uses_exact_calibration_cohort(cfg):
    feat = {
        "market_type": "perp",
        "tradability_score": 80.0,
        "quote_volume_24h": 50_000_000,
        "spread_bps": 5.0,
        "rsi_14": 55.0,
        "return_1h": -1.0,
        "return_4h": -2.0,
        "data_freshness_sec": 200,
    }
    scanner = {
        "opportunity_score": 70.0,
        "scanner_family": "momentum",
        "scanner_direction": "short",
    }

    boosted = compute_total_score(
        dict(feat), scanner, cfg,
        calibration={"momentum:short:perp": 1.2, "momentum:long:spot": 0.6},
    )
    penalized = compute_total_score(
        dict(feat), scanner, cfg,
        calibration={"momentum:short:perp": 0.8, "momentum:long:spot": 1.4},
    )

    assert boosted == penalized
