"""Durable daily scheduling for the Serenity research pipeline."""

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.storage.models import SerenityResearchRun
from aletheia.storage import repositories as repo


@pytest.fixture(autouse=True)
def configured_processor(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.serenity import research_worker

    monkeypatch.setattr(scheduler, "_research_processor", research_worker._process_research_run)
    monkeypatch.setattr(scheduler, "_research_crash_handler", research_worker.handle_research_crash)


def test_missing_processor_creates_no_thread_run_or_retry(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler

    def forbidden(*args, **kwargs):
        raise AssertionError("unavailable processor must not start work")

    monkeypatch.setattr(scheduler, "_research_processor", None)
    monkeypatch.setattr(scheduler.threading, "Thread", forbidden)
    monkeypatch.setattr(scheduler, "_current_scheduler",
                        lambda: SimpleNamespace(running=True, add_job=forbidden))
    with pytest.raises(RuntimeError, match="research_processor_unavailable"):
        scheduler.enqueue_research_run("missing")
    assert scheduler._enqueue_serenity_research_if_due()["status"] == "unavailable"
    scheduler.schedule_serenity_retry("missing", {}, 0, "2026-09-14T01:02:03+00:00")
    assert repo.list_serenity_research_runs(limit=10) == []


def test_serenity_boundary_uses_latest_0200_cst():
    from aletheia.agent_runtime.scheduler import _serenity_schedule_boundary

    after = _serenity_schedule_boundary(datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))
    before = _serenity_schedule_boundary(datetime(2026, 7, 10, 17, 0, tzinfo=timezone.utc))

    assert after == datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)
    assert before == datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)


def test_research_reuses_owned_loop_and_scheduler_stop_drains_it(monkeypatch):
    import asyncio
    import threading
    from aletheia.agent_runtime import scheduler

    lane = scheduler._PersistentAsyncTimeoutWorker("research-lifecycle-test")
    started, finished = threading.Event(), threading.Event()
    threads, failures = [], []

    async def process(run_id):
        threads.append(threading.get_ident())
        if run_id == "blocked":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()
        else:
            started.set()

    monkeypatch.setattr(scheduler, "_research_worker", lane)
    monkeypatch.setattr(scheduler, "_ensure_research_worker_ready", lambda: None)
    monkeypatch.setattr(scheduler, "_research_processor", process)
    monkeypatch.setattr(scheduler, "_research_crash_handler", lambda run_id, exc: failures.append((run_id, str(exc))))
    try:
        scheduler.enqueue_research_run("first")
        assert started.wait(2)
        started.clear()
        scheduler.enqueue_research_run("blocked")
        assert started.wait(2)
        assert len(set(threads)) == 1
        scheduler.stop_scheduler()
        assert finished.is_set()
        assert lane.closed and not lane.thread_alive and lane.pending == 0
        assert failures == [("blocked", "research_scheduler_stopped")]
    finally:
        lane.close()


def test_research_admission_failure_is_reported_before_reraise(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler
    failures = []

    def reject(coroutine):
        coroutine.close()
        raise RuntimeError("scheduler async timeout queue is full")

    monkeypatch.setattr(scheduler, "_ensure_research_worker_ready", lambda: None)
    monkeypatch.setattr(scheduler, "_research_worker", SimpleNamespace(submit=reject))
    monkeypatch.setattr(scheduler, "_research_crash_handler", lambda run_id, exc: failures.append((run_id, str(exc))))
    with pytest.raises(RuntimeError, match="queue is full"):
        scheduler.enqueue_research_run("rejected")
    assert failures == [("rejected", "scheduler async timeout queue is full")]


def test_research_cancelled_before_first_step_reports_shutdown(monkeypatch):
    from concurrent.futures import Future
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler
    failures = []

    def cancel(coroutine):
        coroutine.close()
        future = Future()
        future.cancel()
        return future

    monkeypatch.setattr(scheduler, "_ensure_research_worker_ready", lambda: None)
    monkeypatch.setattr(scheduler, "_research_worker", SimpleNamespace(submit=cancel))
    monkeypatch.setattr(scheduler, "_research_crash_handler", lambda run_id, exc: failures.append((run_id, str(exc))))
    scheduler.enqueue_research_run("not-started")
    assert failures == [("not-started", "research_scheduler_stopped")]


def test_research_draining_worker_cannot_be_replaced(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler
    draining = SimpleNamespace(closed=True, stopping=True, thread_alive=True)
    monkeypatch.setattr(scheduler, "_research_worker", draining)
    with pytest.raises(RuntimeError, match="research worker shutdown incomplete"):
        scheduler._ensure_research_worker_ready()
    scheduler._ensure_timeout_workers_ready()
    assert scheduler._research_worker is draining


def test_retry_job_registration_and_run_creation_have_one_scheduler_owner(monkeypatch):
    from types import SimpleNamespace
    from aletheia.agent_runtime import scheduler
    from extensions.serenity import research_worker
    from aletheia.storage.models import SerenityResearchRun

    parent = SerenityResearchRun(status="rate_limited", trigger_type="manual", retry_count=0)
    repo.insert_serenity_research_run(parent)
    jobs, enqueued = [], []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)
    deadline = "2026-09-14T01:02:03+00:00"
    for unavailable in (None, SimpleNamespace(running=False)):
        monkeypatch.setattr(scheduler, "_current_scheduler", lambda: unavailable)
        scheduler.schedule_serenity_retry(parent.id, parent.model_dump(), 0, deadline)
    assert len(repo.list_serenity_research_runs(limit=10)) == 1

    def add_job(callback, trigger, **kwargs):
        jobs.append((callback, trigger, kwargs))
    monkeypatch.setattr(scheduler, "_current_scheduler",
                        lambda: SimpleNamespace(running=True, add_job=add_job))
    scheduler.schedule_serenity_retry(parent.id, parent.model_dump(), 0, deadline)
    assert len(jobs) == 1
    callback, trigger, options = jobs[0]
    assert trigger == "date"
    assert options == dict(run_date=datetime.fromisoformat(deadline),
                           id=f"serenity_retry_{parent.id}", misfire_grace_time=120,
                           replace_existing=True)
    processor = scheduler._research_processor
    monkeypatch.setattr(scheduler, "_research_processor", None)
    callback()
    assert len(repo.list_serenity_research_runs(limit=10)) == 1
    assert enqueued == []
    monkeypatch.setattr(scheduler, "_research_processor", processor)
    callback()
    callback()
    children = [row for row in repo.list_serenity_research_runs(limit=10)
                if row["parent_run_id"] == parent.id]
    assert len(children) == 1 and children[0]["retry_count"] == 1
    assert enqueued == [children[0]["id"]]
    monkeypatch.setattr(scheduler, "_research_processor", None)
    callback()
    assert enqueued == [children[0]["id"]]
    assert not hasattr(research_worker, "_schedule_auto_retry")


def test_due_batch_enqueues_once(monkeypatch):
    import extensions.serenity.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    enqueued: list[str] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)
    now = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)

    first = _enqueue_serenity_research_if_due(now)
    second = _enqueue_serenity_research_if_due(now)

    rows = repo.list_serenity_research_runs(limit=10)
    assert first["status"] == "enqueued"
    assert second["status"] == "already_attempted"
    assert len(rows) == 1
    assert rows[0]["idempotency_key"] == "serenity:scheduled:2026-07-11"
    assert enqueued == [rows[0]["id"]]


def test_success_after_boundary_suppresses_catchup(monkeypatch):
    import extensions.serenity.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    run = SerenityResearchRun(
        trigger_type="manual",
        status="completed",
        finished_at="2026-07-11T03:00:00+00:00",
        created_at="2026-07-11T02:00:00+00:00",
    )
    repo.insert_serenity_research_run(run)
    enqueued: list[str] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)

    result = _enqueue_serenity_research_if_due(
        datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    )

    assert result["status"] == "fresh"
    assert enqueued == []
    assert len(repo.list_serenity_research_runs(limit=10)) == 1


def test_future_success_is_not_fresh_or_healthy(monkeypatch):
    import extensions.serenity.research_worker as worker
    import aletheia.config.loader as loader
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due
    from aletheia.config.schema import SerenityConfig
    from aletheia.diagnostics.doctor import Report, _probe_serenity_pipeline

    now = datetime.now(timezone.utc)
    repo.insert_serenity_research_run(SerenityResearchRun(
        trigger_type="manual",
        status="completed",
        finished_at=(now.replace(microsecond=0) + timedelta(days=1)).isoformat(),
    ))
    enqueued: list[str] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)

    first = _enqueue_serenity_research_if_due(now)
    second = _enqueue_serenity_research_if_due(now)

    assert first["status"] == "enqueued"
    assert second["status"] == "already_attempted"
    assert len(enqueued) == 1
    assert repo.get_serenity_research_run(first["run_id"])["metadata"] == (
        '{"schedule_reason": "invalid_future_success"}'
    )

    load_config = loader.load_config
    monkeypatch.setattr(
        loader,
        "load_config",
        lambda: load_config().model_copy(update={"serenity": SerenityConfig(enabled=True)}),
    )
    report = Report()
    _probe_serenity_pipeline(report)

    check = next(item for item in report.checks if item.name == "data.serenity_pipeline")
    assert check.severity == "warn"
    assert "future" in check.detail


def test_future_success_does_not_hide_valid_fresh_run(monkeypatch):
    import extensions.serenity.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    valid = SerenityResearchRun(
        trigger_type="manual",
        status="completed",
        finished_at="2026-07-11T03:00:00+00:00",
    )
    future = SerenityResearchRun(
        trigger_type="manual",
        status="completed",
        finished_at="2026-07-12T10:00:00+00:00",
    )
    repo.insert_serenity_research_run(valid)
    repo.insert_serenity_research_run(future)
    enqueued: list[str] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)

    result = _enqueue_serenity_research_if_due(
        datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    )

    assert result["status"] == "fresh"
    assert result["run_id"] == valid.id
    assert enqueued == []
    assert len(repo.list_serenity_research_runs(limit=10)) == 2


def test_unrelated_active_run_suppresses_scheduled_batch(monkeypatch):
    import extensions.serenity.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    active = SerenityResearchRun(trigger_type="manual", status="queued")
    repo.insert_serenity_research_run(active)
    enqueued: list[str] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", enqueued.append)

    result = _enqueue_serenity_research_if_due(
        datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    )

    assert result == {
        "status": "active",
        "run_id": active.id,
        "batch_key": "serenity:scheduled:2026-07-11",
    }
    assert enqueued == []


def test_enqueue_failure_is_persisted_and_not_retried_on_restart(monkeypatch):
    import extensions.serenity.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    def fail_enqueue(_run_id):
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr("aletheia.agent_runtime.scheduler.enqueue_research_run", fail_enqueue)
    now = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError, match="worker unavailable"):
        _enqueue_serenity_research_if_due(now)

    row = repo.list_serenity_research_runs(limit=1)[0]
    assert row["status"] == "failed"
    assert row["error_type"] == "enqueue_failed"
    assert _enqueue_serenity_research_if_due(now)["status"] == "already_attempted"


def test_gateway_startup_marks_interrupted_run_failed():
    from aletheia.agent_runtime.scheduler import _recover_interrupted_serenity_run

    run = SerenityResearchRun(status="running", stage="collect_evidence")
    repo.insert_serenity_research_run(run)

    recovered = _recover_interrupted_serenity_run()
    stored = repo.get_serenity_research_run(run.id)

    assert recovered == [run.id]
    assert stored["status"] == "failed"
    assert stored["error_type"] == "gateway_restart_interrupted"
    assert stored["finished_at"]
    events = repo.list_serenity_run_events(run.id)
    assert events[-1]["event_type"] == "run.interrupted"


def test_gateway_startup_recovers_all_interrupted_runs():
    from aletheia.agent_runtime.scheduler import _recover_interrupted_serenity_run

    queued = SerenityResearchRun(status="queued")
    running = SerenityResearchRun(status="running")
    cancelling = SerenityResearchRun(status="cancel_requested", stage="extract_claims")
    repo.insert_serenity_research_run(queued)
    repo.insert_serenity_research_run(running)
    repo.insert_serenity_research_run(cancelling)

    recovered = _recover_interrupted_serenity_run()

    assert set(recovered) == {queued.id, running.id, cancelling.id}
    assert repo.get_active_serenity_research_run() is None
    assert repo.get_serenity_research_run(queued.id)["status"] == "failed"
    assert repo.get_serenity_research_run(running.id)["status"] == "failed"
    cancelled = repo.get_serenity_research_run(cancelling.id)
    assert cancelled["status"] == "cancelled"
    assert cancelled["error_type"] is None
    assert repo.list_serenity_run_events(cancelling.id)[-1]["event_type"] == "run.cancelled"


def test_doctor_does_not_treat_recent_failure_as_fresh(monkeypatch):
    import aletheia.config.loader as _loader
    _orig = _loader.load_config

    def _enabled():
        from aletheia.config.schema import SerenityConfig
        cfg = _orig()
        return cfg.model_copy(update={"serenity": SerenityConfig(enabled=True)})

    monkeypatch.setattr(_loader, "load_config", _enabled)

    from aletheia.diagnostics.doctor import Report, _probe_serenity_pipeline

    repo.insert_serenity_research_run(SerenityResearchRun(
        status="completed",
        created_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:10:00+00:00",
    ))
    repo.insert_serenity_research_run(SerenityResearchRun(
        status="failed",
        error_type="provider_timeout",
        finished_at=datetime.now(timezone.utc).isoformat(),
    ))
    report = Report()

    _probe_serenity_pipeline(report)

    check = next(item for item in report.checks if item.name == "data.serenity_pipeline")
    assert check.severity == "warn"
    assert "last successful run" in check.detail
    assert "latest attempt=failed (provider_timeout)" in check.detail
