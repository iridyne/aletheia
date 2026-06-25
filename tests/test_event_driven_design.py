"""Tests for event-driven strategy designer trigger."""
import sqlite3
from unittest.mock import patch, MagicMock
import time

import pytest


def setup_env(tmp_path, monkeypatch):
    """Real DATA_DIR + DB so emit_deterministic_opportunities persists for real."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


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


class TestSignalEmitterPersistence:
    """End-to-end: emit_deterministic_opportunities writes a REAL opportunity row.

    Unlike TestSignalEmitterTrigger (which mocks insert_opportunity and only checks
    the trigger fires), these run the full emission path against a real DB and read
    the persisted row back — catching bugs in confidence labeling, score rounding,
    direction inference, and field mapping that a mocked insert cannot see.
    """

    def _query_opps(self):
        from aletheia.storage.sqlite import get_db
        rows = get_db().execute(
            "SELECT symbol, direction_bias, confidence, confidence_score, "
            "source_agent, scanner_family, universe_member_id FROM opportunities"
        ).fetchall()
        return [dict(r) for r in rows]

    def test_high_conf_persists_opportunity_with_correct_fields(self, tmp_path, monkeypatch):
        setup_env(tmp_path, monkeypatch)
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        members = [{
            "id": "m1", "symbol": "BTC/USDT", "scanner_family": "momentum",
            "total_score": 65.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "BTC/USDT", "reasons": ["strong_momentum"],
            "return_1h": 2.0, "return_4h": 4.0, "rsi_14": 60,
            "funding_rate": None, "return_24h": None, "scanner_direction": "long",
        }]

        # Only mock the trigger (network/scheduler boundary), NOT the DB write.
        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand"):
            count = emit_deterministic_opportunities(members, "snap-1", scored_features)

        assert count == 1
        opps = self._query_opps()
        assert len(opps) == 1
        o = opps[0]
        assert o["symbol"] == "BTC/USDT"
        assert o["direction_bias"] == "long"
        assert o["confidence"] == "high"          # total_score 65 >= 60 threshold
        assert o["confidence_score"] == 65        # int(round(65.0))
        assert o["source_agent"] == "universe_scanner"
        assert o["scanner_family"] == "momentum"
        assert o["universe_member_id"] == "m1"

    def test_medium_conf_label_below_threshold(self, tmp_path, monkeypatch):
        setup_env(tmp_path, monkeypatch)
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        # total_score 50 is in [45, 60) → "medium", and below high-conf trigger.
        members = [{
            "id": "m1", "symbol": "ETH/USDT", "scanner_family": "momentum",
            "total_score": 50.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "ETH/USDT", "reasons": ["moderate"],
            "return_1h": 1.0, "return_4h": 2.0, "rsi_14": 55,
            "funding_rate": None, "return_24h": None, "scanner_direction": "long",
        }]

        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand") as mock_trigger:
            count = emit_deterministic_opportunities(members, "snap-1", scored_features)
            # Medium confidence must NOT fire the high-conf event trigger.
            mock_trigger.assert_not_called()

        assert count == 1
        opps = self._query_opps()
        assert len(opps) == 1
        assert opps[0]["confidence"] == "medium"
        assert opps[0]["confidence_score"] == 50

    def test_below_min_score_emits_nothing(self, tmp_path, monkeypatch):
        setup_env(tmp_path, monkeypatch)
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        # total_score 30 < _MIN_SCORE (45) → skipped entirely.
        members = [{
            "id": "m1", "symbol": "BTC/USDT", "scanner_family": "momentum",
            "total_score": 30.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "BTC/USDT", "reasons": ["weak"],
            "return_1h": 0.5, "return_4h": 1.0, "rsi_14": 52,
            "funding_rate": None, "return_24h": None, "scanner_direction": "long",
        }]

        count = emit_deterministic_opportunities(members, "snap-1", scored_features)
        assert count == 0
        assert self._query_opps() == []

    def test_dedupe_skips_existing_open_opportunity(self, tmp_path, monkeypatch):
        setup_env(tmp_path, monkeypatch)
        from aletheia.universe.signal_emitter import emit_deterministic_opportunities

        members = [{
            "id": "m1", "symbol": "BTC/USDT", "scanner_family": "momentum",
            "total_score": 65.0, "market_type": "spot", "expires_at": None,
        }]
        scored_features = [{
            "symbol": "BTC/USDT", "reasons": ["strong_momentum"],
            "return_1h": 2.0, "return_4h": 4.0, "rsi_14": 60,
            "funding_rate": None, "return_24h": None, "scanner_direction": "long",
        }]

        with patch("aletheia.agent_runtime.scheduler.trigger_strategy_designer_on_demand"):
            first = emit_deterministic_opportunities(members, "snap-1", scored_features)
            # Same symbol + snapshot again → dedupe path must skip it.
            second = emit_deterministic_opportunities(members, "snap-1", scored_features)

        assert first == 1
        assert second == 0
        assert len(self._query_opps()) == 1
