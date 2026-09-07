from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from aletheia.agent_runtime import scheduler


def test_scheduler_snapshot_distinguishes_config_from_runtime_and_tracks_tick(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda **_: SimpleNamespace(runtime=SimpleNamespace(scheduler_enabled=True)),
    )
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_scheduler_last_tick", None)

    assert scheduler.status_snapshot() == {
        "enabled": True,
        "running": False,
        "state": "not_started",
        "job_count": 0,
        "last_tick": None,
    }

    scheduler._record_scheduler_tick(None)
    assert scheduler.status_snapshot()["last_tick"] is not None


def test_scheduler_snapshot_reports_failed_config_read_as_unavailable(monkeypatch) -> None:
    from unittest.mock import Mock

    monkeypatch.setattr(scheduler, "load_config", Mock(side_effect=[
        TypeError("invalid config value"),
        SimpleNamespace(runtime=SimpleNamespace(scheduler_enabled=True)),
    ]))
    monkeypatch.setattr(scheduler, "_scheduler", None)

    assert scheduler.status_snapshot()["enabled"] is None


class _Job:
    def __init__(self, next_run_time):
        self.next_run_time = next_run_time


class _Scheduler:
    def __init__(self, *, state, job=None):
        self.state = state
        self.job = job

    def get_job(self, job_id):
        return self.job


def test_scheduled_job_status_reports_explicit_pause_blockers(monkeypatch) -> None:
    from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

    monkeypatch.setattr(
        scheduler,
        "_scheduler",
        _Scheduler(
            state=STATE_RUNNING,
            job=_Job(datetime.now(timezone.utc) + timedelta(minutes=1)),
        ),
    )
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", set())
    assert scheduler.scheduled_job_status("pi_trading_decision")["blocker"] is None

    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", {"pi_trading_decision"})
    assert scheduler.scheduled_job_status("pi_trading_decision")["blocker"] == "job_paused"

    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", set())
    monkeypatch.setattr(
        scheduler,
        "_scheduler",
        _Scheduler(
            state=STATE_RUNNING,
            job=_Job(datetime.now(timezone.utc) - timedelta(minutes=1)),
        ),
    )
    assert scheduler.scheduled_job_status("pi_trading_decision")["blocker"] is None

    monkeypatch.setattr(
        scheduler,
        "_scheduler",
        _Scheduler(state=STATE_RUNNING, job=None),
    )
    assert scheduler.scheduled_job_status("pi_trading_decision")["blocker"] == "job_missing"

    monkeypatch.setattr(
        scheduler,
        "_scheduler",
        _Scheduler(
            state=STATE_PAUSED,
            job=_Job(datetime.now(timezone.utc) + timedelta(minutes=1)),
        ),
    )
    paused = scheduler.scheduled_job_status("pi_trading_decision")
    assert paused["registered"] is True
    assert paused["paused"] is True
    assert paused["blocker"] == "scheduler_paused"
