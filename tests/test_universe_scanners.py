"""Tests for universe scanner families."""

import pytest
from aletheia.universe.scanners import (
    score_momentum, score_mean_reversion, score_volatility,
    score_derivatives, score_event, run_all_scanners,
)


def _base_feat(**overrides):
    feat = {
        "return_1h": 0.0,
        "return_4h": 0.0,
        "return_24h": 0.0,
        "rsi_14": 50.0,
        "atr_pct": 2.0,
        "funding_rate": None,
        "open_interest": None,
        "oi_delta_1h": None,
        "volume_1h": 1_000_000,
        "quote_volume_24h": 10_000_000,
    }
    feat.update(overrides)
    return feat


def test_momentum_high_return():
    feat = _base_feat(return_1h=4.0, return_4h=6.0, rsi_14=65)
    score, reasons = score_momentum(feat)
    assert score >= 60
    assert any("1h return" in r for r in reasons)


def test_momentum_low_return():
    feat = _base_feat(return_1h=0.1, return_4h=0.2)
    score, _ = score_momentum(feat)
    assert score < 20


def test_momentum_aligned_trend():
    feat = _base_feat(return_1h=2.0, return_4h=3.0, return_24h=5.0, rsi_14=60)
    score, _ = score_momentum(feat)
    assert score >= 50


def test_mean_reversion_extreme_rsi():
    feat = _base_feat(rsi_14=85, return_4h=8.0)
    score, reasons = score_mean_reversion(feat)
    assert score >= 50
    assert any("overbought" in r for r in reasons)


def test_mean_reversion_oversold():
    feat = _base_feat(rsi_14=18, return_4h=-9.0)
    score, reasons = score_mean_reversion(feat)
    assert score >= 60
    assert any("oversold" in r for r in reasons)


def test_mean_reversion_neutral():
    feat = _base_feat(rsi_14=50, return_4h=1.0)
    score, _ = score_mean_reversion(feat)
    assert score < 20


def test_volatility_high_atr():
    feat = _base_feat(atr_pct=6.0, return_1h=3.0, return_4h=5.0)
    score, reasons = score_volatility(feat)
    assert score >= 60
    assert any("ATR" in r for r in reasons)


def test_volatility_low_atr():
    feat = _base_feat(atr_pct=0.5, return_1h=0.1)
    score, _ = score_volatility(feat)
    assert score < 20


def test_derivatives_extreme_funding():
    feat = _base_feat(funding_rate=0.015)
    score, reasons = score_derivatives(feat)
    assert score >= 30
    assert any("funding" in r for r in reasons)


def test_derivatives_no_data():
    feat = _base_feat(funding_rate=None, oi_delta_1h=None)
    score, _ = score_derivatives(feat)
    assert score < 30


def test_score_normalization():
    feat = _base_feat(return_1h=10.0, return_4h=20.0, rsi_14=90, atr_pct=10.0)
    for scorer in [score_momentum, score_mean_reversion, score_volatility, score_derivatives]:
        score, _ = scorer(feat)
        assert 0.0 <= score <= 100.0


def test_run_all_scanners_picks_best():
    feat = _base_feat(rsi_14=85, return_4h=9.0)
    result = run_all_scanners(feat)
    assert result["scanner_family"] == "mean_reversion"
    assert result["opportunity_score"] > 0
    assert "reasons" in result
    assert "all_scores" in result


def test_run_all_scanners_momentum_wins():
    feat = _base_feat(return_1h=5.0, return_4h=8.0, rsi_14=65, atr_pct=4.0)
    result = run_all_scanners(feat)
    assert result["scanner_family"] == "momentum"


def test_event_scanner_placeholder():
    feat = _base_feat()
    score, reasons = score_event(feat)
    assert score == 0.0
    assert reasons == []


def test_run_all_scanners_includes_event():
    feat = _base_feat()
    result = run_all_scanners(feat)
    assert "event" in result["all_scores"]
    assert result["all_scores"]["event"] == 0.0
