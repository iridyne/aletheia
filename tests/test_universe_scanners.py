"""Tests for universe scanner families."""

import pytest
from aletheia.universe.scanners import (
    score_momentum, score_mean_reversion, score_volatility,
    score_derivatives, score_event, score_short_bias, run_all_scanners,
)


def _base_feat(**overrides):
    feat = {
        "return_1h": 0.0,
        "market_type": "perp",
        "return_4h": 0.0,
        "return_24h": 0.0,
        "rsi_14": 50.0,
        "atr_pct": 2.0,
        "funding_rate": None,
        "open_interest": None,
        "oi_delta_1h": None,
        "volume_1h": 1_000_000,
        "quote_volume_24h": 10_000_000,
        "vol_ratio": None,
        "bb_width_pct": None,
        "bb_squeeze": False,
        "relative_strength_24h": None,
        "relative_strength_4h": None,
        "rs_beta_adj_24h": None,
    }
    feat.update(overrides)
    return feat


# --- Momentum ---

def test_momentum_relative_strength_long():
    feat = _base_feat(relative_strength_24h=6.0, relative_strength_4h=2.0, rsi_14=60, vol_ratio=2.0)
    score, reasons, direction = score_momentum(feat)
    assert score >= 50
    assert direction == "long"
    assert any("RS24h" in r for r in reasons)


def test_momentum_relative_strength_short():
    feat = _base_feat(relative_strength_24h=-6.0, relative_strength_4h=-2.0, rsi_14=40, vol_ratio=2.0)
    score, reasons, direction = score_momentum(feat)
    assert score >= 50
    assert direction == "short"


def test_momentum_fallback_4h():
    feat = _base_feat(return_4h=5.0, rsi_14=60)
    score, reasons, direction = score_momentum(feat)
    assert score > 0
    assert direction == "long"


def test_momentum_no_signal():
    feat = _base_feat(return_4h=0.5, relative_strength_24h=0.5)
    score, _, direction = score_momentum(feat)
    assert score == 0
    assert direction is None


def test_momentum_funding_penalty():
    feat = _base_feat(relative_strength_24h=4.0, funding_rate=0.02)
    score, reasons, direction = score_momentum(feat)
    assert direction == "long"
    assert any("crowded" in r for r in reasons)


def test_momentum_overextended_penalty():
    feat = _base_feat(relative_strength_24h=6.0, return_24h=15.0)
    score_with, _, _ = score_momentum(feat)
    feat2 = _base_feat(relative_strength_24h=6.0, return_24h=5.0)
    score_without, _, _ = score_momentum(feat2)
    assert score_with < score_without


# --- Momentum: Taker Ratio ---

def test_momentum_taker_buy_confirms_long():
    feat = _base_feat(relative_strength_24h=5.0, rsi_14=60, vol_ratio=2.0, taker_buy_ratio=1.5)
    score, reasons, direction = score_momentum(feat)
    assert direction == "long"
    assert any("taker buy" in r for r in reasons)


def test_momentum_taker_sell_confirms_short():
    feat = _base_feat(relative_strength_24h=-5.0, rsi_14=40, vol_ratio=2.0, taker_buy_ratio=0.6)
    score, reasons, direction = score_momentum(feat)
    assert direction == "short"
    assert any("taker sell" in r for r in reasons)


def test_momentum_taker_neutral_no_bonus():
    feat = _base_feat(relative_strength_24h=5.0, rsi_14=60, vol_ratio=2.0, taker_buy_ratio=1.0)
    score, reasons, direction = score_momentum(feat)
    assert direction == "long"
    assert not any("taker" in r for r in reasons)


# --- Mean Reversion ---

def test_mean_reversion_extreme_overbought():
    feat = _base_feat(rsi_14=85, return_4h=9.0)
    score, reasons, direction = score_mean_reversion(feat)
    assert score >= 50
    assert direction == "short"
    assert any("overbought" in r for r in reasons)


def test_mean_reversion_extreme_oversold():
    feat = _base_feat(rsi_14=18, return_4h=-9.0)
    score, reasons, direction = score_mean_reversion(feat)
    assert score >= 50
    assert direction == "long"
    assert any("oversold" in r for r in reasons)


def test_mean_reversion_neutral():
    feat = _base_feat(rsi_14=50, return_4h=1.0)
    score, _, direction = score_mean_reversion(feat)
    assert score == 0
    assert direction is None


def test_mean_reversion_volume_exhaustion():
    feat = _base_feat(rsi_14=82, vol_ratio=0.5)
    score, reasons, _ = score_mean_reversion(feat)
    assert any("exhaustion" in r for r in reasons)


def test_mean_reversion_funding_confirms():
    feat = _base_feat(rsi_14=82, funding_rate=0.008)
    score, reasons, direction = score_mean_reversion(feat)
    assert direction == "short"
    assert any("crowded" in r for r in reasons)


# --- Volatility ---

def test_volatility_breakout_with_volume():
    feat = _base_feat(atr_pct=6.0, ret_4h=5.0, return_4h=5.0, vol_ratio=2.5)
    score, reasons, direction = score_volatility(feat)
    assert score >= 50
    assert direction == "long"
    assert any("ATR" in r for r in reasons)


def test_volatility_no_volume_penalized():
    feat = _base_feat(atr_pct=6.0, return_4h=5.0, vol_ratio=0.8)
    score, _, direction = score_volatility(feat)
    # High ATR but no volume → heavily penalized
    feat2 = _base_feat(atr_pct=6.0, return_4h=5.0, vol_ratio=3.0)
    score2, _, _ = score_volatility(feat2)
    assert score < score2


def test_volatility_bb_squeeze():
    feat = _base_feat(atr_pct=4.0, return_4h=3.0, vol_ratio=2.5, bb_width_pct=1.5, bb_squeeze=True)
    score, reasons, _ = score_volatility(feat)
    assert any("squeeze" in r.lower() for r in reasons)


def test_volatility_bb_narrow_fallback():
    feat = _base_feat(atr_pct=4.0, return_4h=3.0, vol_ratio=2.5, bb_width_pct=1.5, bb_squeeze=False)
    score, reasons, _ = score_volatility(feat)
    assert any("narrow" in r.lower() for r in reasons)


def test_volatility_taker_confirms_direction():
    feat = _base_feat(atr_pct=6.0, return_4h=5.0, vol_ratio=2.5, taker_buy_ratio=1.4)
    score, reasons, direction = score_volatility(feat)
    assert direction == "long"
    assert any("taker confirms" in r for r in reasons)


def test_volatility_no_direction():
    feat = _base_feat(atr_pct=6.0, return_4h=1.0, vol_ratio=3.0)
    score, _, direction = score_volatility(feat)
    assert score == 0
    assert direction is None


# --- Derivatives ---

def test_derivatives_extreme_funding():
    feat = _base_feat(funding_rate=0.015)
    score, reasons, direction = score_derivatives(feat)
    assert score >= 30
    assert direction == "short"
    assert any("funding" in r for r in reasons)


def test_derivatives_negative_funding():
    feat = _base_feat(funding_rate=-0.012)
    score, reasons, direction = score_derivatives(feat)
    assert score >= 30
    assert direction == "long"


def test_derivatives_no_data():
    feat = _base_feat(funding_rate=None, oi_delta_1h=None, rsi_14=50)
    score, _, direction = score_derivatives(feat)
    assert score == 0
    assert direction is None


def test_derivatives_scanner_skips_spot_even_with_contaminated_fields():
    feat = _base_feat(
        market_type="spot",
        funding_rate=0.02,
        oi_delta_1h=25.0,
        return_1h=2.0,
    )

    assert score_derivatives(feat) == (0.0, [], None)


@pytest.mark.parametrize("market_type", [None, "", "margin", "option", "unknown"])
def test_derivatives_scanner_requires_explicit_market_type(market_type):
    feat = _base_feat(
        market_type=market_type,
        funding_rate=0.02,
        oi_delta_1h=25.0,
        return_1h=2.0,
    )

    assert score_derivatives(feat) == (0.0, [], None)


def test_derivatives_oi_surge_sets_direction_without_funding():
    """A rising-OI surge with a price move must produce a directed signal even
    when funding is neutral — otherwise OI never contributes to this family
    (the {0,10} 'half-dead' symptom)."""
    feat = _base_feat(funding_rate=0.00001, oi_delta_1h=18.0, return_1h=2.0)
    score, reasons, direction = score_derivatives(feat)
    assert score >= 20
    assert direction == "long"  # OI up + price up = new longs
    assert any("OI" in r for r in reasons)

    feat = _base_feat(funding_rate=None, oi_delta_1h=25.0, return_1h=-1.5)
    score, _, direction = score_derivatives(feat)
    assert score >= 30
    assert direction == "short"  # OI up + price down = new shorts


def test_derivatives_oi_surge_flat_price_no_fabricated_direction():
    """OI up but price ~flat → no directional read; score stays 0 (no direction
    to carry it). Guards against fabricating a direction from OI alone."""
    feat = _base_feat(funding_rate=0.00001, oi_delta_1h=18.0, return_1h=0.1)
    score, _, direction = score_derivatives(feat)
    assert direction is None
    assert score == 0


def test_derivatives_oi_drop_stays_ambiguous():
    """A falling-OI (deleveraging) move is direction-ambiguous: it must not set
    a direction on its own."""
    feat = _base_feat(funding_rate=0.00001, oi_delta_1h=-30.0, return_1h=-2.0)
    score, _, direction = score_derivatives(feat)
    assert direction is None
    assert score == 0


# --- Dedicated short bias ---

def test_short_bias_rejects_crowded_shorts_with_deeply_negative_funding():
    feat = _base_feat(
        return_4h=-4.0,
        return_24h=-6.0,
        relative_strength_24h=-5.0,
        funding_rate=-0.012,
        rsi_14=40.0,
    )

    assert score_short_bias(feat) == (0.0, [], None)


def test_short_bias_positive_funding_supports_short_without_creating_it():
    base = _base_feat(
        return_4h=-4.0,
        return_24h=-6.0,
        relative_strength_24h=-5.0,
        funding_rate=None,
        rsi_14=40.0,
    )
    funded = {**base, "funding_rate": 0.006}

    base_score, _, base_direction = score_short_bias(base)
    funded_score, funded_reasons, funded_direction = score_short_bias(funded)

    assert base_direction == funded_direction == "short"
    assert funded_score > base_score
    assert any("longs paying" in reason for reason in funded_reasons)


# --- Event (placeholder) ---

def test_event_scanner_placeholder():
    feat = _base_feat()
    score, reasons, direction = score_event(feat)
    assert score == 0.0
    assert reasons == []
    assert direction is None


# --- Score normalization ---

def test_score_normalization():
    feat = _base_feat(
        return_4h=20.0, return_24h=5.0, rsi_14=90, atr_pct=10.0,
        relative_strength_24h=10.0, vol_ratio=5.0,
    )
    for scorer in [score_momentum, score_mean_reversion, score_volatility, score_derivatives]:
        score, _, _ = scorer(feat)
        assert 0.0 <= score <= 100.0


# --- run_all_scanners ---

def test_run_all_scanners_picks_best():
    feat = _base_feat(rsi_14=85, return_4h=9.0)
    result = run_all_scanners(feat)
    assert result["scanner_family"] == "mean_reversion"
    assert result["opportunity_score"] > 0
    assert "reasons" in result
    assert "all_scores" in result
    assert "scanner_direction" in result


def test_run_all_scanners_momentum_wins():
    feat = _base_feat(relative_strength_24h=7.0, relative_strength_4h=3.0, rsi_14=62, vol_ratio=4.0)
    result = run_all_scanners(feat)
    assert result["scanner_family"] == "momentum"
    assert result["scanner_direction"] == "long"


def test_run_all_scanners_includes_event():
    feat = _base_feat()
    result = run_all_scanners(feat)
    assert "event" in result["all_scores"]
    assert result["all_scores"]["event"] == 0.0


def test_run_all_scanners_prefers_directional():
    feat = _base_feat(atr_pct=4.0, return_4h=0.5, rsi_14=50)
    result = run_all_scanners(feat)
    # No scanner can produce direction with these neutral values
    # So score should be 0 or very low
    assert result["opportunity_score"] == 0 or result["scanner_direction"] is None
