"""AT-02 scheduler separation and restart-safe measurement checks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


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

    durable_due = datetime.now(timezone.utc) + timedelta(minutes=17)
    monkeypatch.setattr(scheduler, "load_config", lambda: _cfg())
    monkeypatch.setattr(scheduler, "_recover_interrupted_serenity_run", lambda: [])
    monkeypatch.setattr(
        scheduler,
        "_initial_learning_evaluator_run_time",
        lambda _interval: durable_due,
    )
    scheduler._scheduler = None
    started = scheduler.init_scheduler()
    try:
        job_ids = [job.id for job in started.get_jobs()]
        evaluator = started.get_job("learning_contribution_evaluator")
        controller = started.get_job("learning_lifecycle_controller")
        audit_reconciler = started.get_job("learning_lifecycle_audit_reconciler")
        universe_audit_reconciler = started.get_job(
            "universe_scanner_audit_reconciler"
        )
        assert evaluator is not None and controller is not None and audit_reconciler is not None
        assert universe_audit_reconciler is not None
        assert job_ids.count("learning_contribution_evaluator") == 1
        assert "learning_verdict_runner" not in job_ids
        assert evaluator.max_instances == controller.max_instances == 1
        assert audit_reconciler.max_instances == 1
        assert universe_audit_reconciler.max_instances == 1
        assert universe_audit_reconciler.coalesce is True
        assert evaluator.coalesce is True and controller.coalesce is True
        assert evaluator.next_run_time == durable_due
        assert "learning_lifecycle_audit_reconciler" in scheduler._ESSENTIAL_JOBS
        assert "universe_scanner_audit_reconciler" in scheduler._ESSENTIAL_JOBS
    finally:
        scheduler.stop_scheduler()


def test_learning_evaluator_restart_preserves_future_durable_deadline(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control

    now = datetime(2026, 8, 7, 20, 49, tzinfo=timezone.utc)
    durable_due = now + timedelta(minutes=17)
    monkeypatch.setattr(
        control,
        "get_learning_job_health",
        lambda _job_id: SimpleNamespace(next_due_at=durable_due.isoformat()),
    )

    initial_run = scheduler._initial_learning_evaluator_run_time(  # type: ignore[attr-defined]
        60,
        now=now,
    )

    assert initial_run == durable_due


def test_learning_evaluator_restart_recovers_overdue_deadline_before_legacy_catchup(
    monkeypatch,
):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control

    now = datetime(2026, 8, 7, 21, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(
        control,
        "get_learning_job_health",
        lambda _job_id: SimpleNamespace(
            next_due_at=(now - timedelta(minutes=4)).isoformat()
        ),
    )

    initial_run = scheduler._initial_learning_evaluator_run_time(60, now=now)

    assert initial_run == now + timedelta(
        seconds=scheduler._LEARNING_EVALUATOR_RESTART_DELAY_SEC
    )
    assert initial_run < now + timedelta(minutes=2)


def test_reschedule_learning_jobs_updates_both_live_intervals(monkeypatch):
    from aletheia.agent_runtime import scheduler

    calls = []

    class FakeJob:
        def __init__(self, next_run_time):
            self.trigger = "old-trigger"
            self.next_run_time = next_run_time

    jobs = {
        "learning_contribution_evaluator": FakeJob(datetime.now(timezone.utc)),
        "learning_lifecycle_controller": FakeJob(None),
    }

    class FakeScheduler:
        timezone = timezone.utc

        def get_job(self, job_id):
            return jobs.get(job_id)

        def modify_job(self, job_id, **kwargs):
            calls.append((job_id, kwargs))
            jobs[job_id].trigger = kwargs["trigger"]
            jobs[job_id].next_run_time = kwargs["next_run_time"]

    monkeypatch.setattr(scheduler, "_scheduler", FakeScheduler())

    assert scheduler.reschedule_learning_jobs(90) is True
    assert [job_id for job_id, _kwargs in calls] == [
        "learning_contribution_evaluator",
        "learning_lifecycle_controller",
    ]
    assert all(call[1]["trigger"].interval.total_seconds() == 90 * 60 for call in calls)
    assert calls[0][1]["next_run_time"] is not None
    assert calls[1][1]["next_run_time"] is None


def test_reschedule_learning_jobs_rolls_back_partial_failure(monkeypatch):
    from aletheia.agent_runtime import scheduler

    class FakeJob:
        trigger = "old-trigger"
        next_run_time = datetime.now(timezone.utc)

    jobs = {
        "learning_contribution_evaluator": FakeJob(),
        "learning_lifecycle_controller": FakeJob(),
    }

    class FakeScheduler:
        timezone = timezone.utc

        def get_job(self, job_id):
            return jobs[job_id]

        def modify_job(self, job_id, **kwargs):
            if job_id == "learning_lifecycle_controller":
                raise RuntimeError("simulated controller reschedule failure")
            jobs[job_id].trigger = kwargs["trigger"]
            jobs[job_id].next_run_time = kwargs["next_run_time"]

    monkeypatch.setattr(scheduler, "_scheduler", FakeScheduler())

    assert scheduler.reschedule_learning_jobs(90) is False
    assert jobs["learning_contribution_evaluator"].trigger == "old-trigger"


def test_learning_settings_reschedules_live_jobs_after_save(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.files import jsonl
    from aletheia.server.routes import settings

    cfg = _cfg()
    saved = []
    rescheduled = []
    monkeypatch.setattr(settings, "load_config", lambda: cfg)
    monkeypatch.setattr(settings, "save_config", lambda updated: saved.append(updated))
    monkeypatch.setattr(jsonl, "write_audit", lambda _event: None)
    monkeypatch.setattr(
        scheduler,
        "reschedule_learning_jobs",
        lambda interval: rescheduled.append(interval) or True,
    )

    result = asyncio.run(settings.update_learning_config(
        settings.LearningConfigUpdate(evaluation_interval_minutes=90),
    ))

    assert saved == [cfg]
    assert rescheduled == [90]
    assert result["rescheduled"] is True
    assert result["learning"]["evaluation_interval_minutes"] == 90


def test_audit_reconciler_outer_failure_is_durable(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control

    monkeypatch.setattr(
        scheduler,
        "_run_sync_with_timeout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("test")),
    )

    scheduler._run_learning_lifecycle_audit_reconciler()

    health = control.get_learning_job_health("learning_lifecycle_audit_reconciler")
    assert health.status == "failed"
    assert health.last_failure_reason == "audit_reconciler_internal_failure"


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


def test_measurement_jobs_share_a_dedicated_serial_executor():
    """Restart recovery must not queue behind the saturated general IO lane."""
    import ast
    import inspect
    import textwrap

    from aletheia.agent_runtime import scheduler

    assert scheduler._EXECUTOR_WORKERS["learning_measurement"] == 1
    tree = ast.parse(textwrap.dedent(inspect.getsource(scheduler.init_scheduler)))
    executors: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "add_job":
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = keywords.get("id")
        executor = keywords.get("executor")
        if (
            isinstance(job_id, ast.Constant)
            and isinstance(job_id.value, str)
            and isinstance(executor, ast.Constant)
            and isinstance(executor.value, str)
        ):
            executors[job_id.value] = executor.value

    assert executors["learning_contribution_evaluator"] == "learning_measurement"
    assert executors["learning_measurement_startup_catchup"] == "learning_measurement"


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
