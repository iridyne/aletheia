"""Tests for market-aware exit intelligence (exit_rules + market_signal_reader)."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from aletheia.portfolio.exit_rules import (
    evaluate_market_exit_rules,
    MarketExitAction,
)
from aletheia.portfolio.market_signal_reader import MarketSignalContext


class FakeMarketAwareExitsConfig:
    enabled = True
    min_hold_hours_before_market_exit = 0.5
    max_signal_age_min = 35.0
    urgent_exit_score = 80
    urgent_exit_max_pnl_pct = -0.3
    tighten_stop_score = 60
    confidence_collapse_score = 30
    tighten_atr_mult = 0.8
    tighten_pct = 0.8


class FakeTradingConfig:
    market_aware_exits = FakeMarketAwareExitsConfig()


class FakeCfg:
    trading = FakeTradingConfig()


def _pos(direction="long", entry_price=100.0, stop_price=None, opened_hours_ago=2.0):
    opened = (datetime.now(timezone.utc) - timedelta(hours=opened_hours_ago)).isoformat()
    return {
        "id": "test_pos",
        "symbol": "SOL/USDT",
        "direction": direction,
        "entry_price": entry_price,
        "stop_price": stop_price,
        "opened_at": opened,
        "execution_mode": "okx_demo",
    }


def _signal(direction_bias="short", confidence_score=72, atr_pct=1.8, age_min=5.0):
    return MarketSignalContext(
        symbol="SOL/USDT",
        direction_bias=direction_bias,
        confidence_score=confidence_score,
        signal_age_min=age_min,
        atr_pct=atr_pct,
    )


class TestUrgentExit:
    def test_counter_signal_high_score_triggers_urgent_exit(self):
        pos = _pos(entry_price=100.0)
        signal = _signal(direction_bias="short", confidence_score=85)
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "urgent_exit"
        assert result.exit_price == 99.0
        assert "counter_signal_urgent" in result.reason

    def test_no_urgent_exit_when_winning(self):
        """Winning trade: urgent_exit blocked by pnl guard (pnl > urgent_exit_max_pnl_pct)."""
        pos = _pos(entry_price=100.0)
        signal = _signal(direction_bias="short", confidence_score=85)
        result = evaluate_market_exit_rules(pos, 102.0, signal, FakeCfg())
        # Won't urgent_exit (winning), but may tighten_stop (counter signal is still real)
        assert result.action != "urgent_exit"

    def test_short_position_counter_signal_long(self):
        pos = _pos(direction="short", entry_price=100.0)
        signal = _signal(direction_bias="long", confidence_score=85)
        result = evaluate_market_exit_rules(pos, 101.0, signal, FakeCfg())
        assert result.action == "urgent_exit"


class TestTightenStop:
    def test_counter_signal_medium_score_tightens(self):
        pos = _pos(entry_price=100.0, stop_price=96.0)
        signal = _signal(direction_bias="short", confidence_score=65, atr_pct=2.0)
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "tighten_stop"
        # new_stop = 99.0 - (99.0 * 2.0/100 * 0.8) = 99.0 - 1.584 = 97.416
        assert result.new_stop_price is not None
        assert result.new_stop_price > 96.0

    def test_no_tighten_when_stop_already_tighter(self):
        pos = _pos(entry_price=100.0, stop_price=98.5)
        signal = _signal(direction_bias="short", confidence_score=65, atr_pct=2.0)
        # new_stop would be ~97.4, but current stop is 98.5 (already tighter)
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "none"

    def test_confirming_signal_no_action(self):
        pos = _pos(direction="long", entry_price=100.0)
        signal = _signal(direction_bias="long", confidence_score=75)
        result = evaluate_market_exit_rules(pos, 102.0, signal, FakeCfg())
        assert result.action == "none"


class TestConfidenceCollapse:
    def test_neutral_low_score_losing_tightens(self):
        pos = _pos(entry_price=100.0, stop_price=95.0)
        signal = _signal(direction_bias="neutral", confidence_score=25, atr_pct=1.5)
        result = evaluate_market_exit_rules(pos, 98.0, signal, FakeCfg())
        assert result.action == "tighten_stop"
        assert "confidence_collapse" in result.reason

    def test_neutral_low_score_winning_no_action(self):
        pos = _pos(entry_price=100.0)
        signal = _signal(direction_bias="neutral", confidence_score=25)
        result = evaluate_market_exit_rules(pos, 103.0, signal, FakeCfg())
        assert result.action == "none"


class TestGuards:
    def test_disabled_returns_none(self):
        class DisabledCfg:
            class trading:
                class market_aware_exits:
                    enabled = False
                    min_hold_hours_before_market_exit = 0.5
                    max_signal_age_min = 35.0
                    urgent_exit_score = 80
                    urgent_exit_max_pnl_pct = -0.3
                    tighten_stop_score = 60
                    confidence_collapse_score = 30
                    tighten_atr_mult = 0.8
                    tighten_pct = 0.8
        pos = _pos()
        signal = _signal(confidence_score=90)
        result = evaluate_market_exit_rules(pos, 90.0, signal, DisabledCfg())
        assert result.action == "none"
        assert "disabled" in result.reason

    def test_too_early_returns_none(self):
        pos = _pos(opened_hours_ago=0.1)  # 6 min < 30 min minimum
        signal = _signal(confidence_score=65)
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "none"
        assert "too_early" in result.reason

    def test_no_signal_returns_none(self):
        pos = _pos()
        signal = MarketSignalContext(symbol="SOL/USDT")
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "none"
        assert "no_signal" in result.reason

    def test_stale_signal_returns_none(self):
        pos = _pos()
        signal = _signal(confidence_score=65, age_min=40.0)
        result = evaluate_market_exit_rules(pos, 99.0, signal, FakeCfg())
        assert result.action == "none"
        assert "stale" in result.reason


class TestMarketSignalReader:
    def test_get_signals_empty_symbols(self):
        from aletheia.portfolio.market_signal_reader import get_market_signals_for_positions
        result = get_market_signals_for_positions([])
        assert result == {}
