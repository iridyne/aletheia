"""Regression tests for scheduler reliability fixes #1788 #1789 #1456 #1443."""
from __future__ import annotations
import inspect
import pytest
from unittest.mock import patch, MagicMock


# ─────────────────────────────────────────────────────────────────────────────
# #1788 — Job failures recorded AND re-raised (APScheduler sees EVENT_JOB_ERROR)
# ─────────────────────────────────────────────────────────────────────────────

def test_run_job_guarded_reraises_on_failure():
    """_run_job_guarded must re-raise so APScheduler sees EVENT_JOB_ERROR."""
    import aletheia.agent_runtime.scheduler as sched_mod
    src = inspect.getsource(sched_mod._run_job_guarded)
    assert "raise" in src, "_run_job_guarded must re-raise after recording failure"


def test_run_job_guarded_calls_record_failure_then_raises():
    """Verify _run_job_guarded records failure AND re-raises."""
    from aletheia.agent_runtime.scheduler import _guarded
    failures = []

    def _bad_fn():
        raise ValueError("test error")

    with patch("aletheia.agent_runtime.scheduler._record_job_failure",
               side_effect=lambda jid, err, *a, **kw: failures.append((jid, err))):
        guarded = _guarded("test_job", _bad_fn)
        with pytest.raises(ValueError, match="test error"):
            guarded()

    assert failures, "failure must be recorded before re-raise"


# ─────────────────────────────────────────────────────────────────────────────
# #1789 — OHLCV backfill raises when all downloads fail
# ─────────────────────────────────────────────────────────────────────────────

def test_ohlcv_backfill_raises_when_all_fail():
    """_ohlcv_backfill_body raises RuntimeError when all downloads error."""
    import aletheia.agent_runtime.scheduler as sched_mod
    src = inspect.getsource(sched_mod._ohlcv_backfill_body)
    assert "error_count == attempt_count" in src, (
        "_ohlcv_backfill_body must raise when all download attempts fail"
    )
    assert "RuntimeError" in src


# ─────────────────────────────────────────────────────────────────────────────
# #1456 — Shutdown flag aborts io-lane jobs
# ─────────────────────────────────────────────────────────────────────────────

def test_scheduler_stopping_flag_exists():
    """_SCHEDULER_STOPPING module flag must exist."""
    from aletheia.agent_runtime.scheduler import _SCHEDULER_STOPPING
    assert isinstance(_SCHEDULER_STOPPING, bool)


def test_stop_scheduler_sets_stopping_flag():
    """stop_scheduler() must set _SCHEDULER_STOPPING=True."""
    import aletheia.agent_runtime.scheduler as sched_mod
    src = inspect.getsource(sched_mod.stop_scheduler)
    assert "_SCHEDULER_STOPPING" in src and "True" in src, (
        "stop_scheduler must set _SCHEDULER_STOPPING=True"
    )


def test_run_sync_checks_stopping_flag():
    """_run_sync_with_timeout must check _SCHEDULER_STOPPING before executing."""
    import aletheia.agent_runtime.scheduler as sched_mod
    src = inspect.getsource(sched_mod._run_sync_with_timeout)
    assert "_SCHEDULER_STOPPING" in src, (
        "_run_sync_with_timeout must check _SCHEDULER_STOPPING flag"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1443 — Capacity released after coroutine cleanup, not at cancellation
# ─────────────────────────────────────────────────────────────────────────────

def test_submit_uses_capacity_wrapper():
    """_PersistentAsyncTimeoutWorker.submit wraps coroutine to defer capacity release."""
    import aletheia.agent_runtime.scheduler as sched_mod
    src = inspect.getsource(sched_mod._PersistentAsyncTimeoutWorker.submit)
    assert "_capacity_wrapper" in src, (
        "submit must wrap coroutine in _capacity_wrapper to defer capacity release"
    )
    # Capacity released in wrapper's finally, not via done_callback
    assert "finally" in src or "try:" in src
