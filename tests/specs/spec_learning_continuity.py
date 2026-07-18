"""Executable continuity contracts for autonomous Learning jobs."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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
    from aletheia.learning import control

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
        "confidence_calibration", "post_trade_reviewer", "risk_critic",
        "guard_promotion", "filter_controller_observe", "persona_rollout",
    } <= control.AUTONOMOUS_LEARNING_JOBS
    assert {"outcome_settler", "shadow_resolver"}.isdisjoint(control.AUTONOMOUS_LEARNING_JOBS)


@spec_claim(SpecClaim(
    "LE-B11", "learning_evolution", "§10 restart catch-up", "L2b",
    "A missed daily Learning window is due until it succeeds, then the same durable "
    "date-window cannot run twice after another restart.",
    status="implemented",
))
def test_daily_learning_catchup_is_once_per_successful_window(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control

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


@spec_claim(SpecClaim(
    "LE-B12", "learning_evolution", "§10 reviewer backlog health", "L2b",
    "Reviewer success persists proposed-lesson backlog count and age; failure keeps the "
    "window unclaimed so a later catch-up can retry rather than reporting false health.",
    status="implemented",
))
def test_reviewer_backlog_and_failure_are_durable(monkeypatch):
    from aletheia.agent_runtime import scheduler
    from aletheia.learning import control
    from aletheia.learning.lessons import LessonStore

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
    assert control.promotion_refreeze_reason() == "reviewer_backlog_stale"
