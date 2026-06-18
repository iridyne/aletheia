"""Tests for event-driven strategy designer trigger."""
from unittest.mock import patch, MagicMock
import time

import pytest


class TestEventDrivenTrigger:
    """Test the debounce + trigger logic in scheduler."""

    def test_trigger_fires_first_time(self):
        import aletheia.agent_runtime.scheduler as sched
        sched._last_event_design_time = 0.0

        with patch.object(sched, "_run_strategy_designer") as mock_run:
            result = sched.trigger_strategy_designer_on_demand()
            assert result is True
            mock_run.assert_called_once()

    def test_trigger_debounced_within_cooldown(self):
        import aletheia.agent_runtime.scheduler as sched
        sched._last_event_design_time = time.time()  # just fired

        with patch.object(sched, "_run_strategy_designer") as mock_run:
            result = sched.trigger_strategy_designer_on_demand()
            assert result is False
            mock_run.assert_not_called()

    def test_trigger_fires_after_cooldown(self):
        import aletheia.agent_runtime.scheduler as sched
        sched._last_event_design_time = time.time() - 2000  # 33+ min ago

        with patch.object(sched, "_run_strategy_designer") as mock_run:
            result = sched.trigger_strategy_designer_on_demand()
            assert result is True
            mock_run.assert_called_once()

    def test_trigger_updates_timestamp(self):
        import aletheia.agent_runtime.scheduler as sched
        sched._last_event_design_time = 0.0
        before = time.time()

        with patch.object(sched, "_run_strategy_designer"):
            sched.trigger_strategy_designer_on_demand()

        assert sched._last_event_design_time >= before


class TestSignalEmitterTrigger:
    """Test that signal_emitter calls the trigger for high-confidence signals."""

    def test_high_conf_emits_trigger(self):
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        members = [{
            "id": "m1", "symbol": "BTC/USDT", "scanner_family": "momentum",
            "total_score": 65.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "BTC/USDT",
            "reasons": ["strong_momentum"],
            "return_1h": 2.0, "return_4h": 4.0, "rsi_14": 60,
            "funding_rate": None, "return_24h": None,
            "scanner_direction": "long",
        }]

        with patch("aletheia.universe.signal_emitter.insert_opportunity") as mock_insert, \
             patch("aletheia.universe.signal_emitter.find_open_opportunity_by_symbol_snapshot", return_value=None), \
             patch("aletheia.universe.signal_emitter.lookup_factor_signal", return_value={"status": "watch"}), \
             patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand") as mock_trigger:
            count = emit_deterministic_opportunities(
                members, "snap-1", scored_features,
            )
            assert count == 1
            mock_trigger.assert_called_once()

    def test_medium_conf_no_trigger(self):
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        members = [{
            "id": "m1", "symbol": "BTC/USDT", "scanner_family": "momentum",
            "total_score": 50.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "BTC/USDT",
            "reasons": ["moderate"],
            "return_1h": 1.0, "return_4h": 2.0, "rsi_14": 55,
            "funding_rate": None, "return_24h": None,
            "scanner_direction": "long",
        }]

        with patch("aletheia.universe.signal_emitter.insert_opportunity") as mock_insert, \
             patch("aletheia.universe.signal_emitter.find_open_opportunity_by_symbol_snapshot", return_value=None), \
             patch("aletheia.universe.signal_emitter.lookup_factor_signal", return_value={"status": "watch"}), \
             patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand") as mock_trigger:
            count = emit_deterministic_opportunities(
                members, "snap-1", scored_features,
            )
            assert count == 1
            mock_trigger.assert_not_called()

    def test_no_emit_no_trigger(self):
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand") as mock_trigger:
            count = emit_deterministic_opportunities([], "snap-1", [])
            assert count == 0
            mock_trigger.assert_not_called()
