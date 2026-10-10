"""Executable continuity contracts for autonomous Learning jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")

from tests.specs.spec_meta import SpecClaim, spec_claim


@pytest.fixture(autouse=True)
def _keep_spec_memory_db_open(monkeypatch):
    """Scheduler cleanup must not destroy pytest's process-local in-memory DB."""
    import aletheia.storage.sqlite as sqlite

    monkeypatch.setattr(sqlite, "close_db", lambda: None)


@spec_claim(SpecClaim(
    "LE-S03", "learning_evolution", "§10 effective enable gate", "L2a",
    "Every autonomous Learning producer, reviewer, and promoter uses one backend gate; "
    "measurement-only outcome settlement remains enabled.",
    status="implemented",
))
def test_autonomous_learning_jobs_share_one_effective_gate(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from aletheia.trust.learning_safety import AUTONOMOUS_LEARNING_JOBS

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=False),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    calls: list[str] = []

    scheduler._run_job_guarded("dreaming_daily", lambda: calls.append("producer"))
    scheduler._run_job_guarded("lesson_reviewer", lambda: calls.append("reviewer"))
    scheduler._run_job_guarded("guard_promotion", lambda: calls.append("promoter"))
    scheduler._run_job_guarded("outcome_settler", lambda: calls.append("measurement"))

    assert calls == ["measurement"]
    assert control.get_learning_job_health("dreaming_daily").status == "disabled"
    assert {
        "dreaming_daily", "lesson_reviewer", "insight_synthesizer",
        "confidence_calibration", "post_trade_reviewer",
        "guard_promotion", "filter_controller_observe", "persona_rollout",
    } <= AUTONOMOUS_LEARNING_JOBS
    assert {"outcome_settler", "shadow_resolver"}.isdisjoint(AUTONOMOUS_LEARNING_JOBS)


@spec_claim(SpecClaim(
    "LE-B11", "learning_evolution", "§10 restart catch-up", "L2b",
    "A missed daily Learning window is due until it succeeds, then the same durable "
    "date-window cannot run twice after another restart.",
    status="implemented",
))
def test_daily_learning_catchup_is_once_per_successful_window(monkeypatch, optional_evidence_jobs):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from aletheia.agents import _AGENTS
    from extensions.research.lesson_reviewer import run_lesson_reviewer

    monkeypatch.setitem(_AGENTS, "lesson_reviewer", run_lesson_reviewer)

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    now = datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc)  # 09:30 CST
    monkeypatch.setattr(control, "utc_now", lambda: now)
    calls: list[str] = []
    monkeypatch.setattr(
        scheduler,
        "_DAILY_LEARNING_CATCHUP_JOBS",
        (("lesson_reviewer", lambda: calls.append("review")),),
    )

    scheduler._run_learning_startup_catchup()
    scheduler._run_learning_startup_catchup()

    assert calls == ["review"]
    health = control.get_learning_job_health("lesson_reviewer")
    assert health.input_watermark == "lesson_reviewer:2026-07-12"
    assert health.status == "healthy"


def test_daily_learning_catchup_retries_only_failed_due_children(monkeypatch, optional_evidence_jobs):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from aletheia.agents import _AGENTS
    from extensions.research.lesson_reviewer import run_lesson_reviewer

    monkeypatch.setitem(_AGENTS, "lesson_reviewer", run_lesson_reviewer)

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    now = datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(control, "utc_now", lambda: now)

    class FakeScheduler:
        def __init__(self):
            self.jobs: dict[str, object] = {}

        def add_job(self, func, *args, **kwargs):
            self.jobs[kwargs["id"]] = func

        def get_job(self, job_id):
            return self.jobs.get(job_id)

        def remove_job(self, job_id):
            self.jobs.pop(job_id, None)

    fake_scheduler = FakeScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    monkeypatch.setattr(scheduler, "_synthesize_trading_memo", lambda: None)

    calls: list[str] = []

    def flaky_review():
        calls.append("review")
        if calls.count("review") == 1:
            raise RuntimeError("review provider unavailable")

    monkeypatch.setattr(
        scheduler,
        "_DAILY_LEARNING_CATCHUP_JOBS",
        (
            ("insight_synthesizer", lambda: calls.append("memo")),
            ("lesson_reviewer", flaky_review),
        ),
    )

    first = scheduler._run_learning_startup_catchup()

    assert calls == ["memo", "review"]
    assert first["remaining_due"] == ["lesson_reviewer"]
    retry = fake_scheduler.jobs[scheduler._LEARNING_STARTUP_CATCHUP_RETRY_JOB_ID]
    assert control.get_learning_job_health("insight_synthesizer").input_watermark == (
        "insight_synthesizer:2026-07-12"
    )
    failed = control.get_learning_job_health("lesson_reviewer")
    assert failed.failed_input_watermark == "lesson_reviewer:2026-07-12"

    retry()

    assert calls == ["memo", "review", "review"]
    assert fake_scheduler.jobs == {}
    recovered = control.get_learning_job_health("lesson_reviewer")
    assert recovered.input_watermark == "lesson_reviewer:2026-07-12"
    assert recovered.failed_input_watermark is None


@spec_claim(SpecClaim(
    "LE-B12", "learning_evolution", "§10 reviewer backlog health", "L2b",
    "Reviewer success persists proposed-lesson backlog count and age; failure keeps the "
    "window unclaimed so a later catch-up can retry rather than reporting false health.",
    status="implemented",
))
def test_reviewer_backlog_and_failure_are_durable(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from extensions.learning.lessons import LessonStore

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    clock = {"now": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc)}
    monkeypatch.setattr(control, "utc_now", lambda: clock["now"])
    LessonStore().create(
        summary="unreviewed", source_type="test", source_id="case-1",
        evidence={"samples": 1},
    )

    scheduler._run_job_guarded("lesson_reviewer", lambda: None)
    healthy = control.get_learning_job_health("lesson_reviewer")
    assert healthy.backlog_count == 1
    assert healthy.oldest_backlog_at is not None

    # A later window fails: preserve the last successful watermark, but leave
    # the new date due so restart catch-up can retry it.
    clock["now"] = datetime(2026, 7, 13, 1, 30, tzinfo=timezone.utc)

    def fail():
        raise RuntimeError("review provider unavailable")

    scheduler._run_job_guarded("lesson_reviewer", fail)
    failed = control.get_learning_job_health("lesson_reviewer")
    assert failed.status == "failed"
    assert failed.consecutive_failures == 1
    assert failed.last_error == "review provider unavailable"
    assert failed.last_failed_at == clock["now"].isoformat()
    assert failed.last_failure_reason == "review provider unavailable"
    assert failed.failed_input_watermark == "lesson_reviewer:2026-07-13"
    assert failed.input_watermark == "lesson_reviewer:2026-07-12"
    assert control.daily_catchup_due("lesson_reviewer")
    assert control.promotion_refreeze_reason() == "reviewer_failed"

    from aletheia.storage.sqlite import get_db
    db = get_db()
    was_in_tx = db.in_transaction
    db.execute("UPDATE ps_lessons SET created_at='2026-07-10T00:00:00+00:00'")
    if not was_in_tx:
        db.commit()
    control.record_learning_job_success("lesson_reviewer")
    recovered = control.get_learning_job_health("lesson_reviewer")
    assert recovered.last_failed_at == failed.last_failed_at
    assert recovered.last_failure_reason == failed.last_failure_reason
    assert recovered.failed_input_watermark is None
    assert control.promotion_refreeze_reason() == "reviewer_backlog_stale"


def test_newer_learning_success_does_not_erase_prior_failed_window(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    clock = {"now": datetime(2026, 7, 12, 1, 30, tzinfo=timezone.utc)}
    monkeypatch.setattr(control, "utc_now", lambda: clock["now"])

    scheduler._run_job_guarded(
        "dreaming_daily",
        lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    failed = control.get_learning_job_health("dreaming_daily")
    assert failed.failed_input_watermark == "dreaming_daily:2026-07-12"

    clock["now"] = datetime(2026, 7, 13, 1, 30, tzinfo=timezone.utc)
    scheduler._run_job_guarded("dreaming_daily", lambda: None)

    recovered = control.get_learning_job_health("dreaming_daily")
    assert recovered.input_watermark == "dreaming_daily:2026-07-13"
    assert recovered.failed_input_watermark == "dreaming_daily:2026-07-12"


def test_lesson_reviewer_attempt_history_links_retry(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from extensions.learning.ledger import list_records

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    now = datetime(2026, 8, 10, 1, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(control, "utc_now", lambda: now)

    scheduler._run_job_guarded(
        "lesson_reviewer",
        lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    scheduler._run_job_guarded("lesson_reviewer", lambda: None)

    attempts = list_records("learning_lesson_reviewer_attempts", limit=2)
    assert [attempt["outcome"] for attempt in attempts] == ["succeeded", "failed"]
    assert attempts[0]["retry_of_attempt_id"] == attempts[1]["attempt_id"]
    assert attempts[1]["error_class"] == "RuntimeError"
    assert attempts[0]["input_watermark"] == "lesson_reviewer:2026-08-10"


def test_lesson_reviewer_restart_and_boundary_keep_attempt_truth(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from extensions.learning import control
    from extensions.learning.ledger import list_records

    monkeypatch.setattr(
        control,
        "load_config",
        lambda: SimpleNamespace(
            agent=SimpleNamespace(autonomous_learning=True),
            runtime=SimpleNamespace(paused=False),
        ),
    )
    clock = {"now": datetime(2026, 8, 10, 18, 59, tzinfo=timezone.utc)}
    monkeypatch.setattr(control, "utc_now", lambda: clock["now"])

    orphan_id = control.record_learning_job_started("lesson_reviewer")

    def cross_schedule_boundary():
        clock["now"] = datetime(2026, 8, 10, 19, 1, tzinfo=timezone.utc)

    scheduler._run_job_guarded("lesson_reviewer", cross_schedule_boundary)

    attempts = list_records("learning_lesson_reviewer_attempts", limit=2)
    assert attempts[1]["attempt_id"] == orphan_id
    assert attempts[1]["outcome"] == "failed"
    assert attempts[1]["error_class"] == "InterruptedError"
    assert attempts[0]["retry_of_attempt_id"] == orphan_id
    assert attempts[0]["input_watermark"] == "lesson_reviewer:2026-08-10"
    assert control.get_learning_job_health("lesson_reviewer").input_watermark == (
        "lesson_reviewer:2026-08-10"
    )
