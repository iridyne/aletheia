"""AT-02 scheduler separation and restart-safe measurement checks."""

from __future__ import annotations

def _cfg(*, autonomous: bool = False, lifecycle: bool = False, paper: bool = False):
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.runtime.scheduler_enabled = True
    cfg.runtime.paused = False
    cfg.agent.autonomous_learning = autonomous
    cfg.learning.lifecycle_automation_enabled = lifecycle
    cfg.learning.paper_influence_enabled = paper
    cfg.controller.shadow_enabled = False
    cfg.historical_data.nightly_backfill_enabled = False
    cfg.historical_data.enabled = False
    cfg.historical_data.active_universe_backfill_enabled = False
    return cfg


def test_measurement_job_runs_when_autonomous_learning_is_disabled(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(control, "load_config", lambda: _cfg(autonomous=False))
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    calls: list[str] = []
    scheduler._run_job_guarded("learning_contribution_evaluator", lambda: calls.append("measure"))
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))

    assert calls == ["measure"]
    assert "learning_lifecycle_controller" in control.AUTONOMOUS_LEARNING_JOBS
    assert "learning_contribution_evaluator" not in control.AUTONOMOUS_LEARNING_JOBS


def test_controller_is_skipped_for_global_integrity_or_runtime_pause(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    import aletheia.storage.sqlite as sqlite

    cfg = _cfg(autonomous=True, lifecycle=True, paper=True)
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    calls: list[str] = []
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: "incident")
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))
    cfg.runtime.paused = True
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)
    scheduler._run_job_guarded("learning_lifecycle_controller", lambda: calls.append("control"))

    assert calls == []


def test_controller_partial_cycle_is_degraded_then_recovers(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    import aletheia.storage.sqlite as sqlite

    cfg = _cfg(autonomous=True, lifecycle=True, paper=True)
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    monkeypatch.setattr(control, "global_learning_refreeze_reason", lambda: None)
    monkeypatch.setattr(sqlite, "close_db", lambda: None)
    scheduler._consecutive_failures["learning_lifecycle_controller"] = 2

    partial = scheduler._run_job_guarded(
        "learning_lifecycle_controller",
        lambda: {"experiments": 2, "failed_experiments": 1},
    )

    assert partial == {"experiments": 2, "failed_experiments": 1}
    degraded = control.get_learning_job_health("learning_lifecycle_controller")
    assert degraded.status == "degraded"
    assert degraded.consecutive_failures == 1
    assert degraded.last_succeeded_at is None
    assert degraded.last_failure_reason == "experiment_lifecycle_failed:1"
    assert degraded.next_due_at is not None
    assert degraded.input_watermark is None
    assert scheduler._consecutive_failures["learning_lifecycle_controller"] == 0

    scheduler._run_job_guarded(
        "risk_critic",
        lambda: {"failed_experiments": 2},
    )
    assert control.get_learning_job_health("risk_critic").status == "healthy"

    recovered = scheduler._run_job_guarded(
        "learning_lifecycle_controller",
        lambda: {"experiments": 3, "failed_experiments": 0},
    )

    assert recovered == {"experiments": 3, "failed_experiments": 0}
    healthy = control.get_learning_job_health("learning_lifecycle_controller")
    assert healthy.status == "healthy"
    assert healthy.consecutive_failures == 0
    assert healthy.last_succeeded_at is not None
    assert healthy.last_failure_reason == "experiment_lifecycle_failed:1"
    assert healthy.last_failed_at == degraded.last_failed_at


def test_scheduler_registers_separate_single_instance_learning_jobs(monkeypatch):
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: [])
    scheduler._scheduler = None
    started = scheduler.init_scheduler()
    try:
        job_ids = [job.id for job in started.get_jobs()]
        evaluator = started.get_job("learning_contribution_evaluator")
        controller = started.get_job("learning_lifecycle_controller")
        assert evaluator is not None and controller is not None
        assert job_ids.count("learning_contribution_evaluator") == 1
        assert "learning_verdict_runner" not in job_ids
        assert evaluator.max_instances == controller.max_instances == 1
        assert evaluator.coalesce is True and controller.coalesce is True
    finally:
        scheduler.stop_scheduler()


def test_measurement_startup_catchup_only_claims_one_current_window(monkeypatch):
    from aletheia.learning import control

    cfg = _cfg()
    monkeypatch.setattr(control, "load_config", lambda: cfg)
    calls: list[str] = []
    monkeypatch.setattr(control, "utc_now", lambda: control.datetime(2026, 7, 15, 12, tzinfo=control.timezone.utc))

    assert control.measurement_catchup_due("learning_contribution_evaluator")
    control.record_measurement_job_success("learning_contribution_evaluator")
    assert not control.measurement_catchup_due("learning_contribution_evaluator")
    assert calls == []


def _health(**updates):
    from aletheia.learning.models import LearningJobHealth

    return LearningJobHealth(**{
        "job_id": "learning_contribution_evaluator",
        "schedule_key": "interval:60m",
        "status": "healthy",
        **updates,
    })


def test_job_health_view_derives_overdue_from_next_due_at():
    """A job that stops firing keeps its stored status, so overdue must be derived."""
    from datetime import datetime, timezone

    from aletheia.learning.control import job_health_view

    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    late = job_health_view(
        _health(next_due_at="2026-07-15T11:00:00+00:00"), now=now,
    )

    assert late["overdue"] is True
    assert late["overdue_seconds"] == 3600
    assert late["status"] == "overdue"


def test_job_health_view_tolerates_scheduler_jitter_and_missing_due_time():
    from datetime import datetime, timedelta, timezone

    from aletheia.learning.control import JOB_OVERDUE_GRACE_SECONDS, job_health_view

    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    jitter = (now - timedelta(seconds=JOB_OVERDUE_GRACE_SECONDS - 5)).isoformat()

    within_grace = job_health_view(_health(next_due_at=jitter), now=now)
    unscheduled = job_health_view(_health(next_due_at=None), now=now)
    unparsable = job_health_view(_health(next_due_at="not-a-timestamp"), now=now)

    for view in (within_grace, unscheduled, unparsable):
        assert view["overdue"] is False
        assert view["overdue_seconds"] is None
        assert view["status"] == "healthy"


def test_job_health_view_never_downgrades_a_stored_failure_to_overdue():
    """``failed`` is more severe than ``overdue`` and must survive the projection."""
    from datetime import datetime, timezone

    from aletheia.learning.control import job_health_view

    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    view = job_health_view(
        _health(
            status="failed",
            consecutive_failures=3,
            next_due_at="2026-07-15T10:00:00+00:00",
        ),
        now=now,
    )

    assert view["status"] == "failed"
    assert view["overdue"] is True
    assert view["overdue_seconds"] == 7200
