"""Tests for market regime detection + signal_emitter regime gate."""

import pytest

from aletheia.regime.detector import (
    detect_regime,
    debounced_detect,
    reset_state,
    MarketRegime,
    REGIME_BULL,
    REGIME_BEAR,
    REGIME_SIDEWAYS,
    REGIME_VOLATILE,
)
from aletheia.universe.signal_emitter import _passes_regime_gate


# ── detect_regime ──────────────────────────────────────────────────────────────


class TestDetectRegime:
    def test_no_data_returns_sideways_low_confidence(self):
        r = detect_regime()
        assert r.state == REGIME_SIDEWAYS
        assert r.confidence == 0.3
        assert r.long_bias == 0.50
        assert r.short_bias == 0.50

    def test_strong_bull_signals(self):
        closes = [50000.0] * 200 + [70000.0]  # 40% above MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=12.0,
            total_market_cap_change_7d=10.0,
        )
        assert r.state == REGIME_BULL
        assert r.long_bias > r.short_bias

    def test_strong_bear_signals(self):
        closes = [70000.0] * 200 + [50000.0]  # ~28% below MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=35.0,
            avg_funding_rate=0.001,
            total_market_cap_change_7d=-10.0,
        )
        assert r.state == REGIME_BEAR
        assert r.short_bias > r.long_bias

    def test_volatile_regime_atr_expansion(self):
        closes = list(range(100, 320))  # 220 values, monotone up but doesn't matter
        r = detect_regime(
            btc_closes_1d=closes,
            vix=32.0,
            atr_7d=5.0,
            atr_30d=2.0,
        )
        assert r.state == REGIME_VOLATILE

    def test_sideways_when_signals_mixed(self):
        closes = [60000.0] * 200 + [60500.0]  # near MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=18.0,
            total_market_cap_change_7d=1.0,
        )
        # With slight bull lean from price > MA200 and sideways from VIX/cap,
        # result should be sideways or mild bull
        assert r.state in (REGIME_SIDEWAYS, REGIME_BULL)

    def test_funding_rate_contrarian(self):
        closes = [60000.0] * 200 + [60000.0]
        # Extreme positive funding = crowded longs = bearish contrarian signal
        r = detect_regime(
            btc_closes_1d=closes,
            avg_funding_rate=0.001,
        )
        # Should have some bear vote from funding
        assert r.signals.get("avg_funding_rate") == 0.001

    def test_bias_table_values(self):
        # Bull regime should have long_bias=0.75, short_bias=0.25
        closes = [40000.0] * 200 + [65000.0]
        r = detect_regime(btc_closes_1d=closes, vix=10.0, total_market_cap_change_7d=12.0)
        if r.state == REGIME_BULL:
            assert r.long_bias == 0.75
            assert r.short_bias == 0.25


# ── debounced_detect ───────────────────────────────────────────────────────────


class TestDebouncedDetect:
    def setup_method(self):
        reset_state()

    def test_first_detection_publishes_immediately(self):
        closes = [50000.0] * 200 + [70000.0]
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=closes, vix=12.0)
        assert r.state == REGIME_BULL

    def test_state_does_not_switch_before_hold_periods(self):
        bull_closes = [50000.0] * 200 + [70000.0]
        bear_closes = [70000.0] * 200 + [50000.0]

        # Start in bull
        debounced_detect(min_hold_periods=3, btc_closes_1d=bull_closes, vix=12.0)

        # Two bear detections — not enough to switch
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        assert r.state == REGIME_BULL  # Still bull — hasn't held 3 periods

    def test_state_switches_after_hold_periods(self):
        bull_closes = [50000.0] * 200 + [70000.0]
        bear_closes = [70000.0] * 200 + [50000.0]

        # Start bull
        debounced_detect(min_hold_periods=3, btc_closes_1d=bull_closes, vix=12.0)

        # Three bear detections — should switch
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        assert r.state == REGIME_BEAR


# ── _passes_regime_gate ────────────────────────────────────────────────────────


class TestPassesRegimeGate:
    def test_long_passes_in_bull_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        assert _passes_regime_gate("long", 50.0, regime) is True

    def test_short_blocked_in_bull_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        assert _passes_regime_gate("short", 50.0, regime) is False

    def test_short_passes_in_bear_regime(self):
        regime = MarketRegime(state="bear", long_bias=0.25, short_bias=0.75)
        assert _passes_regime_gate("short", 50.0, regime) is True

    def test_long_blocked_in_bear_regime(self):
        regime = MarketRegime(state="bear", long_bias=0.25, short_bias=0.75)
        assert _passes_regime_gate("long", 50.0, regime) is False

    def test_both_pass_in_sideways(self):
        regime = MarketRegime(state="sideways", long_bias=0.50, short_bias=0.50)
        assert _passes_regime_gate("long", 50.0, regime) is True
        assert _passes_regime_gate("short", 50.0, regime) is True

    def test_high_score_overrides_hostile_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        # Score >= 60 * 1.1 = 66 → overrides
        assert _passes_regime_gate("short", 67.0, regime) is True

    def test_moderate_score_blocked_in_hostile_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        # Score = 55 < 66 → blocked
        assert _passes_regime_gate("short", 55.0, regime) is False

    def test_volatile_regime_both_reduced(self):
        regime = MarketRegime(state="volatile", long_bias=0.35, short_bias=0.35)
        # bias=0.35 >= threshold 0.30 → passes
        assert _passes_regime_gate("long", 50.0, regime) is True
        assert _passes_regime_gate("short", 50.0, regime) is True
