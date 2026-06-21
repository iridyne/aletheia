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

    def test_retire_strategy_action(self, tmp_path, monkeypatch):
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
        retire_actions = [a for a in actions if a.action_type == "retire_strategy"]
        assert len(retire_actions) == 1
        assert retire_actions[0].params["strategy_id"] == "strat-1"

    def test_tighten_confidence_on_degraded_signal(self, tmp_path, monkeypatch):
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
        tighten = [a for a in actions if a.action_type == "tighten_confidence"]
        assert len(tighten) == 1

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

    def test_trigger_designer_when_uncovered(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=True,
            signal_status="healthy",
            strategies_paper_running=1,
            uncovered_symbols=["SOL/USDT", "AVAX/USDT"],
        )
        actions = decide_actions(assessment)
        designer = [a for a in actions if a.action_type == "trigger_strategy_designer"]
        assert len(designer) == 1

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

    def test_request_backfill_when_ohlcv_not_ready(self, tmp_path, monkeypatch):
        _init_test_db(tmp_path, monkeypatch)
        _make_config(tmp_path)
        from aletheia.agent_shell.operator_loop import SystemAssessment, decide_actions

        assessment = SystemAssessment(
            timestamp="2026-01-01T00:00:00Z",
            ohlcv_ready=False,
            signal_status="healthy",
        )
        actions = decide_actions(assessment)
        backfill = [a for a in actions if a.action_type == "request_backfill"]
        assert len(backfill) == 1


# ─── Stage 2: Action Execution ───────────────────────────────────────────────

class TestActionExecution:
    def test_level1_action_executes(self, tmp_path, monkeypatch):
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
        monkeypatch.setattr(
            "aletheia.agent_shell.operator_loop.send_notification",
            lambda **kwargs: None,
            raising=False,
        )
        result = _execute_level1_action(action)
        assert result["status"] == "executed"

    def test_level_classification(self, tmp_path, monkeypatch):
        from aletheia.agent_shell.operator_loop import classify_action
        assert classify_action("retire_strategy") == 1
        assert classify_action("promote_strategy") == 2
        assert classify_action("deactivate_kill_switch") == 3
        assert classify_action("unknown_action") == 2


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
            action_type="promote_strategy", level=2,
            params={"strategy_id": "test-strat"},
            reason="win_rate exceeds threshold",
        )
        result = _create_proposal(action)
        assert result["status"] == "proposed"
        assert "artifact_id" in result
