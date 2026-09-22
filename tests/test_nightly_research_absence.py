"""Optional nightly producers cannot leave queued work or false catch-up health."""

import asyncio
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")


@pytest.fixture
def absent(monkeypatch):
    from aletheia import agents
    from aletheia.agent_runtime import scheduler

    monkeypatch.setattr(scheduler, "_extract_research_lessons", None)
    monkeypatch.setattr(scheduler, "_enqueue_research_run", None)
    monkeypatch.delitem(agents._AGENTS, "dreaming_agent", raising=False)
    monkeypatch.delitem(agents._AGENTS, "lesson_reviewer", raising=False)
    return scheduler


def test_absent_cron_and_startup_jobs_are_omitted(absent, monkeypatch):
    from aletheia.config.schema import AletheiaConfig

    monkeypatch.setattr(absent, "load_config", AletheiaConfig)
    monkeypatch.setattr(absent, "_synthesize_trading_memo", lambda: None)
    monkeypatch.setattr(absent, "_scheduler", None)
    started = absent.init_scheduler()
    try:
        jobs = {job.id for job in started.get_jobs()}
        assert jobs.isdisjoint({"dreaming_daily", "lesson_reviewer", "serenity_startup_catchup"})
        assert {"insight_synthesizer", "learning_startup_catchup", "portfolio_snapshot"} <= jobs
    finally:
        absent.stop_scheduler()


def test_absent_catchup_skips_before_guard_without_watermark_or_retry(absent, monkeypatch, optional_evidence_jobs):
    from extensions.learning import control

    calls = []
    monkeypatch.setattr(control, "daily_catchup_due", lambda job_id: True)
    monkeypatch.setattr(absent, "_DAILY_LEARNING_CATCHUP_JOBS", (
        ("dreaming_daily", lambda: None), ("lesson_reviewer", lambda: None),
    ))
    monkeypatch.setattr(absent, "_run_job_guarded", lambda *args: calls.append(args))
    monkeypatch.setattr(absent, "_scheduler", None)
    result = absent._run_learning_startup_catchup()
    assert calls == []
    assert result == {
        "children": [
            {"job_id": "dreaming_daily", "status": "unavailable"},
            {"job_id": "lesson_reviewer", "status": "unavailable"},
        ],
        "remaining_due": [], "retry_scheduled": False,
    }
    for job_id in ("dreaming_daily", "lesson_reviewer"):
        health = control.get_learning_job_health(job_id)
        assert health.input_watermark is None
        assert health.failed_input_watermark is None


def test_composed_nightly_jobs_keep_schedule(absent, monkeypatch):
    from aletheia.agents import _AGENTS
    from aletheia.config.schema import AletheiaConfig
    from extensions.research.dreaming_agent import _extract_lessons
    from extensions.research.lesson_reviewer import run_lesson_reviewer

    monkeypatch.setattr(absent, "_extract_research_lessons", _extract_lessons)
    monkeypatch.setattr(absent, "_enqueue_research_run", lambda _: None)
    monkeypatch.setitem(_AGENTS, "lesson_reviewer", run_lesson_reviewer)
    monkeypatch.setattr(absent, "load_config", AletheiaConfig)
    monkeypatch.setattr(absent, "_scheduler", None)
    started = absent.init_scheduler()
    try:
        for job_id, hour in (("dreaming_daily", "2"), ("lesson_reviewer", "3")):
            job = started.get_job(job_id)
            fields = {field.name: str(field) for field in job.trigger.fields}
            assert fields["hour"] == hour and fields["minute"] == "0"
            assert str(job.trigger.timezone) == "Asia/Shanghai"
            assert job.executor == "agents" and job.misfire_grace_time == 300
        catchup = started.get_job("serenity_startup_catchup")
        assert catchup.executor == "agents" and catchup.misfire_grace_time is None
    finally:
        absent.stop_scheduler()


@pytest.mark.parametrize("producer", ["extract", "enqueue"])
def test_nightly_subcapabilities_remain_independent(absent, monkeypatch, producer):
    calls = []

    async def extract(run_id, outputs, errors):
        calls.append(("extract", run_id))

    monkeypatch.setattr(absent, "load_config", lambda: SimpleNamespace(
        runtime=SimpleNamespace(paused=False), serenity=SimpleNamespace(enabled=True),
    ))
    monkeypatch.setattr(absent, "_run_async_with_timeout", lambda _, coroutine: asyncio.run(coroutine))
    if producer == "extract":
        monkeypatch.setattr(absent, "_extract_research_lessons", extract)
    else:
        monkeypatch.setattr(absent, "_enqueue_research_run", lambda _: None)
        monkeypatch.setattr(absent, "_enqueue_serenity_research_if_due", lambda: (
            calls.append(("enqueue", None)) or {"status": "enqueued"}
        ))
    assert absent._nightly_research_available("dreaming_daily")
    absent._run_dreaming_agent()
    assert calls == [(producer, "dreaming_daily_sched" if producer == "extract" else None)]


def test_absent_scheduler_enqueue_preserves_no_run(absent):
    from aletheia.storage import repositories as repo

    with pytest.raises(ValueError, match="producer unavailable"):
        absent._enqueue_serenity_research_if_due()
    assert repo.list_serenity_research_runs() == []


def test_absent_producer_still_recovers_interrupted_runs(absent):
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityResearchRun

    interrupted = SerenityResearchRun(status="running")
    cancelling = SerenityResearchRun(status="cancel_requested")
    for run in (interrupted, cancelling):
        repo.insert_serenity_research_run(run)
    absent._recover_interrupted_serenity_run()
    assert repo.get_serenity_research_run(interrupted.id)["status"] == "failed"
    assert repo.get_serenity_research_run(cancelling.id)["status"] == "cancelled"
