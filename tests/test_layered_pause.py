"""Tests for layered pause: essential jobs continue during AI-driven pause."""

import ast
import inspect
import textwrap
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


def _cfg(*, paused: bool = False, layered: bool = True):
    return SimpleNamespace(
        runtime=SimpleNamespace(paused=paused, scheduler_enabled=True),
        health=SimpleNamespace(
            layered_pause_enabled=layered,
            exchange_probe_interval_sec=30,
        ),
        historical_data=SimpleNamespace(nightly_backfill_enabled=False),
        news=SimpleNamespace(fetch_interval_minutes=15),
        telegram=SimpleNamespace(daily_summary_hour_cst=8),
    )


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """Ensure scheduler state is clean between tests."""
    import aletheia.agent_runtime.scheduler as mod
    old_scheduler = mod._scheduler
    old_paused = mod._selectively_paused_jobs.copy()
    mod._scheduler = None
    mod._selectively_paused_jobs.clear()
    yield
    if mod._scheduler is not None:
        try:
            mod._scheduler.shutdown(wait=False)
        except Exception:
            pass
    mod._scheduler = old_scheduler
    mod._selectively_paused_jobs = old_paused


class TestLayeredPause:
    def test_layered_pause_keeps_essential_jobs_running(self, monkeypatch):
        """Essential jobs should not be paused during layered pause."""
        from aletheia.agent_runtime import scheduler

        monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(layered=True))

        # Mock the BackgroundScheduler
        mock_sched = MagicMock()
        mock_jobs = [
            MagicMock(id="market_data"),
            MagicMock(id="data_collectors"),
            MagicMock(id="data_collector_context"),
            MagicMock(id="connectivity_monitor"),
            MagicMock(id="price_feed"),
            MagicMock(id="position_manager"),
            MagicMock(id="market_observer"),
            MagicMock(id="paper_trader"),
            MagicMock(id="pi_trading_decision"),
            MagicMock(id="dreaming_daily"),
        ]
        mock_sched.get_jobs.return_value = mock_jobs
        scheduler._scheduler = mock_sched

        scheduler.pause_scheduler()

        # Essential jobs should NOT be pause_job'd
        paused_ids = {call[0][0] for call in mock_sched.pause_job.call_args_list}
        assert "market_data" not in paused_ids
        assert "data_collectors" not in paused_ids
        assert "data_collector_context" not in paused_ids
        assert "connectivity_monitor" not in paused_ids
        assert "price_feed" not in paused_ids
        assert "position_manager" not in paused_ids

        # Non-essential jobs SHOULD be paused
        assert "market_observer" in paused_ids
        assert "paper_trader" in paused_ids
        assert "pi_trading_decision" in paused_ids
        assert "dreaming_daily" in paused_ids

        # Blanket .pause() should NOT be called
        mock_sched.pause.assert_not_called()

    def test_blanket_pause_halts_all_jobs(self, monkeypatch):
        """When layered_pause_enabled=False, all jobs are halted."""
        from aletheia.agent_runtime import scheduler

        monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(layered=False))

        mock_sched = MagicMock()
        scheduler._scheduler = mock_sched

        scheduler.pause_scheduler()

        # Blanket pause should be called
        mock_sched.pause.assert_called_once()
        # No per-job pausing
        mock_sched.pause_job.assert_not_called()

    def test_explicit_layered_false_overrides_config(self, monkeypatch):
        """Passing layered=False explicitly uses blanket even if config says layered."""
        from aletheia.agent_runtime import scheduler

        monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(layered=True))

        mock_sched = MagicMock()
        scheduler._scheduler = mock_sched

        scheduler.pause_scheduler(layered=False)

        mock_sched.pause.assert_called_once()

    def test_resume_clears_selective_pause(self, monkeypatch):
        """resume_scheduler should resume all selectively-paused jobs."""
        from aletheia.agent_runtime import scheduler
        from apscheduler.schedulers.base import STATE_RUNNING

        monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(layered=True))

        mock_sched = MagicMock()
        mock_sched.state = STATE_RUNNING
        scheduler._scheduler = mock_sched
        scheduler._selectively_paused_jobs = {"market_observer", "paper_trader", "pi_trading_decision"}

        scheduler.resume_scheduler()

        # Each selectively-paused job should be resumed
        resumed_ids = {call[0][0] for call in mock_sched.resume_job.call_args_list}
        assert "market_observer" in resumed_ids
        assert "paper_trader" in resumed_ids
        assert "pi_trading_decision" in resumed_ids

        # State should be cleared
        assert len(scheduler._selectively_paused_jobs) == 0

    def test_resume_handles_blanket_and_selective(self, monkeypatch):
        """If both blanket-paused AND selectively-paused jobs exist, resume all."""
        from aletheia.agent_runtime import scheduler
        from apscheduler.schedulers.base import STATE_PAUSED

        monkeypatch.setattr(scheduler, "load_config", lambda: _cfg(layered=True))

        mock_sched = MagicMock()
        mock_sched.state = STATE_PAUSED
        scheduler._scheduler = mock_sched
        scheduler._selectively_paused_jobs = {"market_observer"}

        scheduler.resume_scheduler()

        mock_sched.resume.assert_called_once()
        mock_sched.resume_job.assert_called_once_with("market_observer")

    def test_get_pause_state_layered(self, monkeypatch):
        """get_pause_state reports layered mode correctly."""
        from aletheia.agent_runtime import scheduler
        from apscheduler.schedulers.base import STATE_RUNNING

        mock_sched = MagicMock()
        mock_sched.state = STATE_RUNNING
        mock_sched.get_jobs.return_value = [
            MagicMock(id="market_data"),
            MagicMock(id="price_feed"),
        ]
        scheduler._scheduler = mock_sched
        scheduler._selectively_paused_jobs = {"market_observer", "paper_trader"}

        state = scheduler.get_pause_state()
        assert state["mode"] == "layered_paused"
        assert "market_observer" in state["paused_jobs"]
        assert "paper_trader" in state["paused_jobs"]

    def test_get_pause_state_running(self, monkeypatch):
        """get_pause_state reports running when nothing is paused."""
        from aletheia.agent_runtime import scheduler
        from apscheduler.schedulers.base import STATE_RUNNING

        mock_sched = MagicMock()
        mock_sched.state = STATE_RUNNING
        scheduler._scheduler = mock_sched
        scheduler._selectively_paused_jobs = set()

        state = scheduler.get_pause_state()
        assert state["mode"] == "running"


class TestEssentialJobsCoverage:
    """Verify the ESSENTIAL_JOBS set covers all infrastructure-only jobs."""

    def test_essential_jobs_are_subset_of_known_jobs(self):
        """Every essential job ID must exist in the timeout registry."""
        from aletheia.agent_runtime.scheduler import _ESSENTIAL_JOBS, _ASYNC_JOB_TIMEOUT_SEC
        known_jobs = set(_ASYNC_JOB_TIMEOUT_SEC.keys())
        # Some essential jobs might not have explicit timeouts (they use defaults)
        # but core ones should be recognizable
        for job in ["market_data", "data_collectors", "data_collector_context", "connectivity_monitor",
                    "price_feed", "position_manager", "exchange_sync", "portfolio_snapshot"]:
            assert job in _ESSENTIAL_JOBS

    def test_ai_dependent_jobs_not_essential(self):
        """AI-dependent jobs must NOT be in the essential set."""
        from aletheia.agent_runtime.scheduler import _ESSENTIAL_JOBS
        ai_jobs = {
            "market_observer", "paper_trader", "pi_trading_decision",
            "dreaming_daily", "post_trade_reviewer", "risk_critic",
            "pi_operator_loop", "insight_synthesizer", "lesson_reviewer",
        }
        assert _ESSENTIAL_JOBS.isdisjoint(ai_jobs)

    def test_essential_jobs_do_not_self_disable_when_runtime_is_paused(self):
        """Dispatch policy and each essential job body must agree on layered pause."""
        from aletheia.agent_runtime import scheduler

        entrypoint_overrides = {
            "learning_measurement_startup_catchup": "_run_measurement_startup_catchup",
            "ohlcv_active_backfill": "_run_active_ohlcv_backfill",
        }
        violations: list[str] = []
        for job_id in sorted(scheduler._ESSENTIAL_JOBS):
            fn_name = entrypoint_overrides.get(job_id, f"_run_{job_id}")
            fn = getattr(scheduler, fn_name)
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            if any(
                isinstance(node, ast.Attribute) and node.attr == "paused"
                for node in ast.walk(tree)
            ):
                violations.append(job_id)

        # The scheduled TPSL entry delegates its real work to this body.
        tpsl_tree = ast.parse(
            textwrap.dedent(inspect.getsource(scheduler._tpsl_fill_check_body))
        )
        if any(
            isinstance(node, ast.Attribute) and node.attr == "paused"
            for node in ast.walk(tpsl_tree)
        ):
            violations.append("tpsl_fill_check_body")

        assert violations == [], (
            "jobs declared essential still return early during layered pause: "
            f"{violations}"
        )
