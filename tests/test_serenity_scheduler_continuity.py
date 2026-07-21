"""Durable daily scheduling for the Serenity research pipeline."""

from datetime import datetime, timezone

import pytest

from aletheia.storage.models import SerenityResearchRun
from aletheia.storage import repositories as repo


def test_serenity_boundary_uses_latest_0200_cst():
    from aletheia.agent_runtime.scheduler import _serenity_schedule_boundary

    after = _serenity_schedule_boundary(datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc))
    before = _serenity_schedule_boundary(datetime(2026, 7, 10, 17, 0, tzinfo=timezone.utc))

    assert after == datetime(2026, 7, 10, 18, 0, tzinfo=timezone.utc)
    assert before == datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc)


def test_due_batch_enqueues_once(monkeypatch):
    import aletheia.agents.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    enqueued: list[str] = []
    monkeypatch.setattr(worker, "enqueue_research_run", enqueued.append)
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
    import aletheia.agents.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    run = SerenityResearchRun(
        trigger_type="manual",
        status="completed",
        finished_at="2026-07-11T03:00:00+00:00",
        created_at="2026-07-11T02:00:00+00:00",
    )
    repo.insert_serenity_research_run(run)
    enqueued: list[str] = []
    monkeypatch.setattr(worker, "enqueue_research_run", enqueued.append)

    result = _enqueue_serenity_research_if_due(
        datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)
    )

    assert result["status"] == "fresh"
    assert enqueued == []
    assert len(repo.list_serenity_research_runs(limit=10)) == 1


def test_unrelated_active_run_suppresses_scheduled_batch(monkeypatch):
    import aletheia.agents.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    active = SerenityResearchRun(trigger_type="manual", status="queued")
    repo.insert_serenity_research_run(active)
    enqueued: list[str] = []
    monkeypatch.setattr(worker, "enqueue_research_run", enqueued.append)

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
    import aletheia.agents.research_worker as worker
    from aletheia.agent_runtime.scheduler import _enqueue_serenity_research_if_due

    def fail_enqueue(_run_id):
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(worker, "enqueue_research_run", fail_enqueue)
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
    repo.insert_serenity_research_run(queued)
    repo.insert_serenity_research_run(running)

    recovered = _recover_interrupted_serenity_run()

    assert set(recovered) == {queued.id, running.id}
    assert repo.get_active_serenity_research_run() is None
    assert repo.get_serenity_research_run(queued.id)["status"] == "failed"
    assert repo.get_serenity_research_run(running.id)["status"] == "failed"


def test_doctor_does_not_treat_recent_failure_as_fresh():
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.serenity.enabled = True
    save_config(cfg)
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
