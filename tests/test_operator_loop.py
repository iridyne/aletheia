"""Tests for PI Operator Loop — Stage 1-4 end-to-end."""
import json
import sqlite3

import pytest


def _init_test_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def _make_config(tmp_path):
    """Create minimal config.yaml for testing."""
    import yaml
    cfg = {
        "runtime": {"paused": False, "kill_switch_active": False},
        "agent": {"permission_mode": "Paper"},
        "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
        "risk": {"max_demo_position_notional_usdt": 200.0},
        "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg), encoding="utf-8")
    return cfg


# ─── Stage 1: Assessment Only ────────────────────────────────────────────────

class TestBuildSystemAssessment:
    def test_assessment_returns_dataclass(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import build_system_assessment, SystemAssessment

        assessment = build_system_assessment()
        assert isinstance(assessment, SystemAssessment)
        assert assessment.timestamp != ""
        assert assessment.strategies_total >= 0

    def test_assessment_captures_kill_switch(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": True},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 200.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        import aletheia.config.loader as loader
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")

        from aletheia.agent_shell.operator_loop import build_system_assessment
        assessment = build_system_assessment()
        assert assessment.kill_switch_active is True


class TestDecideActions:
    def test_no_actions_on_healthy_system(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            strategies_total=10,
            strategies_paper_running=3,
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            win_rate_7d=50.0,
        )
        actions = decide_actions(assessment)
        assert actions == []

    def test_retire_demote_converged_to_ts(self, tmp_path, monkeypatch):
        """Convergence (owner decision 2026-06-28): strategy retire/demote is owned by the
        TS operator-shell (command registry). Python decide_actions no longer emits
        retire_strategy/demote_strategy — the old handlers wrote recommend_retire/demote
        PiDecisions that nothing consumed (dead writes). Locked by spec PI-S03."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            underperforming_strategies=[
                {"id": "strat-1", "name": "bad_strat", "state": "paper_running",
                 "expectancy": -2.0, "win_rate": 20.0, "trade_count": 15},
            ],
        )
        actions = decide_actions(assessment)
        # Python loop must NOT emit retire/demote anymore (converged to TS operator-shell).
        assert [a for a in actions if a.action_type in ("retire_strategy", "demote_strategy")] == []

    def test_degraded_signal_is_diagnosis_only(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="degraded",
            signal_win_rate=35.0,
        )
        actions = decide_actions(assessment)
        assert [a for a in actions if a.action_type == "tighten_confidence"] == []
        diagnoses = [a for a in actions if a.action_type == "notify_operator"]
        assert len(diagnoses) == 1
        assert diagnoses[0].params["diagnosis_kind"] == "aggregate_signal_quality"
        assert "35.0%" in diagnoses[0].reason

    def test_reduce_sizing_on_low_win_rate(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            win_rate_7d=30.0,
        )
        actions = decide_actions(assessment)
        reduce = [a for a in actions if a.action_type == "reduce_sizing"]
        assert len(reduce) == 1


    def test_notify_on_kill_switch(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            kill_switch_active=True,
        )
        actions = decide_actions(assessment)
        notify = [a for a in actions if a.action_type == "notify_operator"]
        assert len(notify) == 1

    def test_request_backfill_converged_to_ts(self, tmp_path, monkeypatch):
        """Convergence (owner decision 2026-06-28): data-gap backfill is owned by the TS
        operator-shell Auto Repair engine. The Python _action_request_backfill was a no-op
        stub, so decide_actions no longer emits request_backfill. Locked by spec PI-S03."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=False,
            signal_status="healthy",
        )
        actions = decide_actions(assessment)
        # Python loop must NOT emit request_backfill anymore (converged to TS Auto Repair).
        assert [a for a in actions if a.action_type == "request_backfill"] == []

    def test_strategy_research_is_not_an_automatic_operator_action(self, tmp_path, monkeypatch):
        """Methodology research is manual while the product is converged on the money loop."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            SystemAssessment,
            classify_action,
            decide_actions,
        )

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            uncovered_symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
            strategies_paper_running=0,
            strategies_retired_24h=5,
        )

        actions = decide_actions(assessment)
        assert [a for a in actions if a.action_type == "trigger_strategy_research"] == []
        assert classify_action("trigger_strategy_research") == 2


# ─── Stage 2: Action Execution ───────────────────────────────────────────────

class TestActionExecution:
    def test_notify_action_reports_best_effort_request(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="notify_operator", level=1,
            params={"message": "test notification"}, reason="test",
        )
        result = _execute_level1_action(action)
        assert result["status"] == "requested"
        assert result["delivery"] == "best_effort"

    def test_notify_dispatch_failure_is_not_executed(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import OperatorAction, _execute_level1_action
        import aletheia.notifications as notifications

        def fail_notify(*_args, **_kwargs):
            raise RuntimeError("injected notification failure")

        monkeypatch.setattr(notifications, "notify_operator", fail_notify)
        result = _execute_level1_action(OperatorAction(
            action_type="notify_operator",
            level=1,
            params={"message": "test notification"},
            reason="test",
        ))

        assert result["status"] == "failed"
        assert result["reason"] == "notification_dispatch_failed"

    def test_level_classification(self, tmp_path, monkeypatch):
        from aletheia.agent_shell.operator_loop import classify_action
        assert classify_action("deactivate_kill_switch") == 3
        assert classify_action("unknown_action") == 2

        # These historical Python handlers either performed no operation, performed
        # a differently named operation, or self-issued their own approval token.
        # Until a real owner + external approval consumer exists they must fail over
        # to the proposal path instead of remaining automatically executable.
        for action_type in (
            "retire_strategy",
            "demote_strategy",
            "request_backfill",
            "clean_stale_heartbeats",
            "restart_collector",
            "rebuild_sqlite_index",
        ):
            assert classify_action(action_type) == 2

    def test_unowned_selfheal_actions_are_not_directly_executable(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import OperatorAction, _execute_level1_action

        for action_type in (
            "retire_strategy",
            "demote_strategy",
            "request_backfill",
            "clean_stale_heartbeats",
            "restart_collector",
            "rebuild_sqlite_index",
        ):
            result = _execute_level1_action(
                OperatorAction(action_type=action_type, level=1, reason="test")
            )
            assert result["status"] == "skipped"
            assert result["reason"] == f"unknown action type: {action_type}"


# ─── Stage 3: Config Patch Rate Limiting ─────────────────────────────────────

class TestRateLimiting:
    def test_config_patch_rate_limit(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
            _MAX_CONFIG_PATCHES_PER_DAY,
        )
        _action_log.clear()

        # First few should succeed, then get rate-limited
        successes = 0
        for i in range(_MAX_CONFIG_PATCHES_PER_DAY + 2):
            action = OperatorAction(
                action_type="tighten_confidence", level=1,
                params={"delta": 2}, reason=f"test {i}",
            )
            result = _execute_level1_action(action)
            if result["status"] == "executed":
                successes += 1
        # First one succeeds, subsequent may hit cooldown or rate limit
        assert successes >= 1
        assert successes <= _MAX_CONFIG_PATCHES_PER_DAY

    def test_cooldown_prevents_same_param(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="tighten_confidence", level=1,
            params={"delta": 2}, reason="first",
        )
        result1 = _execute_level1_action(action)
        assert result1["status"] == "executed"

        # Same action immediately — should hit cooldown
        action2 = OperatorAction(
            action_type="tighten_confidence", level=1,
            params={"delta": 2}, reason="second",
        )
        result2 = _execute_level1_action(action2)
        assert result2["status"] == "cooldown"


# ─── Stage 3: Capital Allocation + Scanner Tuning ────────────────────────────

class TestCapitalAllocation:
    def test_reduce_sizing_executes(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="reduce_sizing", level=1,
            params={"reduce_pct": 20}, reason="test drawdown",
        )
        result = _execute_level1_action(action)
        assert result["status"] == "executed"
        assert result["old"] == 200.0
        assert result["new"] == 160.0

    def test_reduce_sizing_respects_floor(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 25.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="reduce_sizing", level=1,
            params={"reduce_pct": 30}, reason="test",
        )
        result = _execute_level1_action(action)
        # 25 * 0.7 = 17.5, but floor is 20
        assert result["status"] == "executed"
        assert result["new"] == 20.0

    def test_tighten_stop_loss_executes(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="tighten_stop_loss", level=1,
            params={"delta": 0.5}, reason="test",
        )
        result = _execute_level1_action(action)
        assert result["status"] == "executed"
        assert result["old"] == 3.0
        assert result["new"] == 2.5

    def test_scanner_weight_adjust(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _execute_level1_action, _action_log,
        )
        _action_log.clear()

        action = OperatorAction(
            action_type="adjust_scanner_weight", level=1,
            params={"family": "momentum", "delta": 0.15}, reason="test",
        )
        result = _execute_level1_action(action)
        assert result["status"] == "executed"
        assert result["family"] == "momentum"
        assert result["delta"] == 0.15

        from aletheia.config.loader import load_config
        assert load_config().universe.scanner_weights["momentum"] == 1.15

    def test_scanner_weight_save_failure_is_not_recorded_as_success(
        self, tmp_path, monkeypatch,
    ):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        import aletheia.agent_shell.operator_loop as operator_loop
        import aletheia.config.loader as loader

        recorded: list[tuple] = []
        monkeypatch.setattr(
            operator_loop,
            "_record_action",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
        )

        def fail_save(_cfg):
            raise OSError("injected save failure")

        monkeypatch.setattr(loader, "save_config", fail_save)
        result = operator_loop._execute_level1_action(operator_loop.OperatorAction(
            action_type="adjust_scanner_weight",
            level=1,
            params={"family": "momentum", "delta": 0.15},
            reason="test",
        ))

        assert result["status"] == "failed"
        assert result["reason"] == "config_persistence_failed"
        assert recorded == []


# ─── Stage 4: Full Loop Integration ─────────────────────────────────────────

class TestRunOperatorLoop:
    def test_full_loop_skips_when_paused(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        cfg_dict = {
            "runtime": {"paused": True, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 200.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        import aletheia.config.loader as loader
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")

        from aletheia.agent_shell.operator_loop import run_operator_loop, _action_log
        _action_log.clear()
        summary = run_operator_loop(trigger="test")
        assert summary["status"] == "skipped"
        assert summary["reason"] == "gateway_paused"

    def test_full_loop_runs_and_persists(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import run_operator_loop, _action_log
        _action_log.clear()

        summary = run_operator_loop(trigger="test")
        assert summary.get("status") != "skipped"
        assert "assessment" in summary
        assert "actions_decided" in summary
        assert summary["trigger"] == "test"

        # Verify persistence
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT * FROM knowledge_items WHERE item_type='operator_assessment'"
        ).fetchall()
        assert len(rows) >= 1
        tags_data = json.loads(rows[0]["tags"])
        assert tags_data["trigger"] == "test"

        # Issue #387: the assessment payload must appear in the FTS-searchable
        # 'content' column, not just in tags.
        content = rows[0]["content"]
        assert "System State" in content or "strategies_total" in content, (
            "_persist_assessment must write the assessment dict into content "
            "(was reading 'system_health' instead of 'assessment')"
        )

    def test_level3_actions_blocked(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, classify_action, _LEVEL_3_ACTIONS,
        )
        for action_type in _LEVEL_3_ACTIONS:
            assert classify_action(action_type) == 3

    def test_level2_actions_create_proposals(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import OperatorAction, _create_proposal

        action = OperatorAction(
            action_type="restart_collector", level=1,
            params={"strategy_id": "test-strat"},
            reason="win_rate exceeds threshold",
        )
        result = _create_proposal(action)
        assert result["status"] == "proposed"
        assert "artifact_id" in result


# ─── Deterministic config-control policy (not Learning contribution) ─────────

class TestParamDrift:
    def test_drift_detects_tightened_confidence(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "high", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 200.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import _get_param_drift
        assert _get_param_drift("min_confidence_to_trade") == "tightened"

    def test_drift_detects_neutral_confidence(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import _get_param_drift
        assert _get_param_drift("min_confidence_to_trade") == "neutral"

    def test_drift_detects_tightened_sizing(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 100.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import _get_param_drift
        assert _get_param_drift("max_demo_position_notional_usdt") == "tightened"


class TestLoosenRules:
    def test_rule5a_activity_dead_zone_auto_restores_confidence(self, tmp_path, monkeypatch):
        """4h+ no fills + confidence tightened -> bounded Level 1 recovery."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=0,
            trades_48h=0,
            hours_since_last_trade=4.0,
            win_rate_7d=9.5,
            param_drift={"min_confidence_to_trade": "tightened",
                         "max_demo_position_notional_usdt": "neutral",
                         "default_stop_loss_pct": "neutral"},
        )
        actions = decide_actions(assessment)
        loosen = [a for a in actions if a.action_type == "loosen_confidence"]
        assert len(loosen) == 1
        assert loosen[0].level == 1
        assert "no fills in 4.0h" in loosen[0].reason

    def test_rule5a_does_not_fire_if_not_tightened(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=0,
            hours_since_last_trade=15.0,
            param_drift={"min_confidence_to_trade": "neutral",
                         "max_demo_position_notional_usdt": "neutral",
                         "default_stop_loss_pct": "neutral"},
        )
        actions = decide_actions(assessment)
        loosen = [a for a in actions if a.action_type == "loosen_confidence"]
        assert len(loosen) == 0

    def test_rule5b_winning_streak_auto_executes_sizing_increase(self, tmp_path, monkeypatch):
        """3+ streak + PF>1.5 + tightened sizing + WR>=35 -> Level 1 recovery."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=5,
            recent_streak=4,
            profit_factor_7d=2.0,
            win_rate_7d=60.0,
            param_drift={"min_confidence_to_trade": "neutral",
                         "max_demo_position_notional_usdt": "tightened",
                         "default_stop_loss_pct": "neutral"},
        )
        actions = decide_actions(assessment)
        inc = [a for a in actions if a.action_type == "increase_sizing"]
        assert len(inc) == 1
        assert inc[0].level == 1

    def test_rule5b_suppressed_when_win_rate_low(self, tmp_path, monkeypatch):
        """increase_sizing and reduce_sizing cannot fire in the same cycle."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=5,
            recent_streak=4,
            profit_factor_7d=2.0,
            win_rate_7d=30.0,
            param_drift={"min_confidence_to_trade": "neutral",
                         "max_demo_position_notional_usdt": "tightened",
                         "default_stop_loss_pct": "neutral"},
        )
        actions = decide_actions(assessment)
        assert not any(a.action_type == "increase_sizing" for a in actions)
        assert any(a.action_type == "reduce_sizing" for a in actions)

    def test_rule5c_tp_dominated_proposes_widen_stop(self, tmp_path, monkeypatch):
        """70%+ TP exits + stop tightened → widen_stop_loss L2."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=5,
            win_rate_7d=60.0,
            exit_composition_7d={"take_profit": 8, "stop_loss": 2},
            param_drift={"min_confidence_to_trade": "neutral",
                         "max_demo_position_notional_usdt": "neutral",
                         "default_stop_loss_pct": "tightened"},
        )
        actions = decide_actions(assessment)
        widen = [a for a in actions if a.action_type == "widen_stop_loss"]
        assert len(widen) == 1
        assert widen[0].level == 2

    def test_rule5d_neutral_drift_on_healthy_wr(self, tmp_path, monkeypatch):
        """Healthy WR + trades_48h + drifted param → neutral_drift proposal."""
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=5,
            trades_48h=8,
            win_rate_7d=50.0,
            param_drift={"min_confidence_to_trade": "tightened",
                         "max_demo_position_notional_usdt": "neutral",
                         "default_stop_loss_pct": "neutral"},
        )
        actions = decide_actions(assessment)
        drift = [a for a in actions if a.action_type.startswith("neutral_drift_")]
        assert len(drift) == 1
        assert drift[0].level == 2
        assert "min_confidence_to_trade" in drift[0].action_type

    def test_rule5d_only_one_param_per_cycle(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            signal_win_rate=55.0,
            strategies_paper_running=3,
            trades_24h=5,
            trades_48h=8,
            win_rate_7d=50.0,
            param_drift={"min_confidence_to_trade": "tightened",
                         "max_demo_position_notional_usdt": "tightened",
                         "default_stop_loss_pct": "tightened"},
        )
        actions = decide_actions(assessment)
        drift = [a for a in actions if a.action_type.startswith("neutral_drift_")]
        assert len(drift) == 1  # only one per cycle


class TestConsecutiveEscalation:
    def test_escalation_after_3_consecutive_tightens(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _maybe_escalate, _persist_action
        )

        # Simulate 3 prior tighten actions
        for _ in range(3):
            _persist_action("config_patch", param="min_confidence_to_trade",
                           before="medium", after="high", direction="tighten")

        action = OperatorAction(
            action_type="tighten_confidence", level=1,
            reason="signal degraded",
        )
        result = _maybe_escalate(action, "min_confidence_to_trade", "tighten")
        assert result.level == 2
        assert "[ESCALATED" in result.reason

    def test_no_escalation_below_threshold(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            OperatorAction, _maybe_escalate, _persist_action
        )

        # Only 2 prior tightens
        for _ in range(2):
            _persist_action("config_patch", param="min_confidence_to_trade",
                           before="medium", after="high", direction="tighten")

        action = OperatorAction(
            action_type="tighten_confidence", level=1,
            reason="signal degraded",
        )
        result = _maybe_escalate(action, "min_confidence_to_trade", "tighten")
        assert result.level == 1


class TestPersistentActionLog:
    def test_persist_action_survives_memory_clear(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            _persist_action, _count_actions_today_persistent,
            _last_action_time_persistent, _action_log,
        )

        _persist_action("config_patch", param="test_param",
                        before="a", after="b", direction="tighten")

        # Clear memory cache
        _action_log.clear()

        # Should still find the action from DB
        count = _count_actions_today_persistent("config_patch", "test_param")
        assert count == 1

        last = _last_action_time_persistent("config_patch", "test_param")
        assert last is not None


class TestLoosenHandlers:
    def test_loosen_confidence_never_below_neutral(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        # Set confidence to "high" (tightened above neutral "medium")
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "high", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 200.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import OperatorAction, _action_loosen_confidence, _action_log
        from aletheia.config.loader import load_config

        _action_log.clear()
        action = OperatorAction(action_type="loosen_confidence", level=2, reason="test")
        result = _action_loosen_confidence(action)
        assert result["status"] == "executed"
        assert result["new"] == "medium"  # back to neutral, not below

    def test_increase_sizing_caps_at_neutral(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        # Set sizing to 180 (below neutral=200), +20% = 216 → capped at 200
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 3.0},
            "risk": {"max_demo_position_notional_usdt": 180.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import OperatorAction, _action_increase_sizing, _action_log
        from aletheia.config.loader import load_config

        _action_log.clear()
        action = OperatorAction(action_type="increase_sizing", level=2,
                                params={"increase_pct": 20}, reason="test")
        result = _action_increase_sizing(action)
        assert result["status"] == "executed"
        # 180 * 1.2 = 216, capped at neutral 200
        assert result["new"] == 200.0

    def test_widen_stop_loss_caps_at_neutral(self, tmp_path, monkeypatch):
        import yaml
        _init_test_db(tmp_path, monkeypatch)
        # Set stop loss to 1.0 (tightened below neutral=2.0)
        cfg_dict = {
            "runtime": {"paused": False, "kill_switch_active": False},
            "agent": {"permission_mode": "Paper"},
            "trading": {"min_confidence_to_trade": "medium", "default_stop_loss_pct": 1.0},
            "risk": {"max_demo_position_notional_usdt": 200.0},
            "historical_data": {"nightly_backfill_enabled": False, "min_bars_for_backtest": 200},
        }
        (tmp_path / "config.yaml").write_text(yaml.dump(cfg_dict), encoding="utf-8")
        from aletheia.agent_shell.operator_loop import OperatorAction, _action_widen_stop_loss, _action_log
        from aletheia.config.loader import load_config

        _action_log.clear()
        # Request +1.5 delta, should cap at neutral=2.0
        action = OperatorAction(action_type="widen_stop_loss", level=2,
                                params={"delta": 1.5}, reason="test")
        result = _action_widen_stop_loss(action)
        assert result["status"] == "executed"
        assert result["new"] == 2.0  # capped at neutral


class TestContentHealthSelfSupervision:
    """Operator self-supervision: the manager ingests doctor content.* diagnostics and
    surfaces them as a Level-1 notify only (it flags, it never auto-fixes)."""

    def test_content_health_emits_level1_notify_only(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import (
            SystemAssessment, decide_actions, classify_action,
        )
        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            strategies_total=10, strategies_paper_running=3,
            ohlcv_ready=True, signal_status="healthy",
            signal_win_rate=55.0, win_rate_7d=50.0,
            content_health=[
                {"name": "content.lesson_chain", "severity": "warn", "detail": "chain dead"},
                {"name": "content.shadow_trades", "severity": "warn", "detail": "7560 stale"},
            ],
        )
        actions = decide_actions(assessment)
        notifies = [a for a in actions if a.action_type == "notify_operator"]
        assert len(notifies) == 1, f"expected one content notify, got {[a.action_type for a in actions]}"
        n = notifies[0]
        assert n.level == 1 and classify_action(n.action_type) == 1
        assert "content" in n.params.get("message", "").lower()
        assert n.params.get("content_health") == assessment.content_health
        # the manager must NOT escalate content anomalies to any Level 2/3 action
        assert all(classify_action(a.action_type) == 1 for a in actions)

    def test_no_content_notify_when_clean(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions
        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            strategies_total=10, strategies_paper_running=3,
            ohlcv_ready=True, signal_status="healthy",
            signal_win_rate=55.0, win_rate_7d=50.0,
            content_health=[],
        )
        assert decide_actions(assessment) == []

    def test_build_system_assessment_populates_content_health(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        import aletheia.storage.sqlite as sq
        # 20 one-directional (short) proposals in 24h → content.proposal_bias warn.
        # (The strategy-FSM zombie WARN was removed with the rules engine, Position C
        # 2026-07-02; proposal_bias is a still-live content probe that WARNs.)
        # Disable the direction brake so the probe emits "warn" (unmitigated bias).
        import aletheia.agent_shell.trading_decision as td
        monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *a, **kw: None)
        now = datetime.now(timezone.utc).isoformat()
        for i in range(20):
            sq._db_conn.execute(
                "INSERT INTO trade_proposals (id, symbol, market_type, direction, created_at, status) "
                "VALUES (?,?,?,?,?,?)", (f"p{i}", "BTC/USDT", "spot", "short", now, "pending"),
            )
        sq._db_conn.commit()
        from aletheia.agent_shell.operator_loop import build_system_assessment
        assessment = build_system_assessment()
        names = {c["name"] for c in assessment.content_health}
        assert "content.proposal_bias" in names, \
            f"content_health did not capture the proposal bias: {assessment.content_health}"
        assert all(c["severity"] in ("warn", "blocker") for c in assessment.content_health)
