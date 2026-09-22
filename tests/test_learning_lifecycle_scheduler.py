"""Lab-only scheduling and durable shared job health."""

import builtins

import pytest

@pytest.mark.parametrize("authority", ["legacy", "lab", "missing"])
def test_scheduler_never_registers_retired_learning_jobs(monkeypatch, authority):
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.sqlite import get_db

    db = get_db()
    if authority == "missing":
        db.execute("DELETE FROM observation_authority_state")
    else:
        db.execute("UPDATE observation_authority_state SET authority_source=?", (authority,))
    db.commit()
    monkeypatch.setattr(scheduler, "load_config", AletheiaConfig)
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: [])
    monkeypatch.setattr(scheduler, "_scheduler", None)
    started = scheduler.init_scheduler()
    try:
        jobs = {job.id for job in started.get_jobs()}
        assert jobs.isdisjoint({
            "learning_contribution_evaluator", "learning_measurement_startup_catchup",
            "learning_lifecycle_controller", "learning_lifecycle_audit_reconciler",
        })
        assert "observation_lab_bridge" in jobs
        assert "funding_audit_reconciler" in jobs
        assert "universe_scanner_audit_reconciler" in jobs
    finally:
        scheduler.stop_scheduler()


def test_scheduler_starts_when_observation_package_is_missing(monkeypatch):
    """Optional Observation imports cannot leave a half-initialized scheduler."""
    from aletheia.agent_runtime import scheduler
    from aletheia.config.schema import AletheiaConfig

    monkeypatch.setattr(scheduler, "load_config", AletheiaConfig)
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: [])
    monkeypatch.setattr(scheduler, "_scheduler", None)
    real_import = builtins.__import__

    def missing_owner(name, *args, **kwargs):
        if name == "extensions.observation_lab.execution_owner":
            raise ModuleNotFoundError(name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_owner)
    started = scheduler.init_scheduler()
    try:
        job_ids = {job.id for job in started.get_jobs()}
        assert "observation_lab_bridge" in job_ids
        assert "filter_controller_observe" not in job_ids
    finally:
        scheduler.stop_scheduler()


def _health(**updates):
    from extensions.learning.models import LearningJobHealth

    return LearningJobHealth(**{
        "job_id": "learning_contribution_evaluator",
        "schedule_key": "interval:60m",
        "status": "healthy",
        **updates,
    })


def test_job_health_view_derives_overdue_from_next_due_at():
    """A job that stops firing keeps its stored status, so overdue must be derived."""
    from datetime import datetime, timezone

    from extensions.learning.control import job_health_view

    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    late = job_health_view(
        _health(next_due_at="2026-07-15T11:00:00+00:00"), now=now,
    )

    assert late["overdue"] is True
    assert late["overdue_seconds"] == 3600
    assert late["status"] == "overdue"


def test_job_health_view_tolerates_scheduler_jitter_and_missing_due_time():
    from datetime import datetime, timedelta, timezone

    from extensions.learning.control import JOB_OVERDUE_GRACE_SECONDS, job_health_view

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

    from extensions.learning.control import job_health_view

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
