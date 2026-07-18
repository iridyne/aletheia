"""One effective gate and durable continuity state for autonomous Learning."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aletheia.config.loader import load_config
from aletheia.learning.ledger import get_job_health, upsert_job_health
from aletheia.learning.models import LearningJobHealth
from aletheia.storage.sqlite import get_db


_CST = ZoneInfo("Asia/Shanghai")

# These scheduled jobs create, review, apply, promote, or automatically roll out
# learned state. Outcome settlement and shadow resolution are deliberately absent:
# they preserve measurement truth even while autonomous Learning is disabled.
AUTONOMOUS_LEARNING_JOBS: frozenset[str] = frozenset({
    "dreaming_daily",
    "lesson_reviewer",
    "insight_synthesizer",
    "confidence_calibration",
    "post_trade_reviewer",
    "risk_critic",
    "guard_promotion",
    "filter_controller_observe",
    "persona_rollout",
    "learning_startup_catchup",
    "learning_lifecycle_controller",
})

DAILY_LEARNING_SCHEDULES: dict[str, tuple[int, int]] = {
    "dreaming_daily": (2, 0),
    "insight_synthesizer": (2, 30),
    "lesson_reviewer": (3, 0),
}

_SCHEDULE_KEYS: dict[str, str] = {
    "dreaming_daily": "cron:02:00:Asia/Shanghai",
    "insight_synthesizer": "cron:02:30:Asia/Shanghai",
    "lesson_reviewer": "cron:03:00:Asia/Shanghai",
    "confidence_calibration": "interval:6h",
    "post_trade_reviewer": "interval:4h",
    "risk_critic": "interval:6h",
    "guard_promotion": "cron:04:00:Asia/Shanghai",
    "filter_controller_observe": "config:controller.observe_interval_hours",
    "persona_rollout": "interval:8h",
    "learning_startup_catchup": "startup:once",
    "learning_contribution_evaluator": "interval:config.learning.evaluation_interval_minutes",
    "learning_lifecycle_controller": "interval:config.learning.evaluation_interval_minutes",
}

_INTERVAL_HOURS = {
    "confidence_calibration": 6,
    "post_trade_reviewer": 4,
    "risk_critic": 6,
    "persona_rollout": 8,
}

_REVIEWER_STALE_AFTER = timedelta(hours=48)
_REVIEWER_BACKLOG_LIMIT = 160


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def is_autonomous_learning_job(job_id: str) -> bool:
    return job_id in AUTONOMOUS_LEARNING_JOBS


def global_learning_refreeze_reason() -> str | None:
    """Return the durable system-wide Learning freeze, independent of config.

    Read-only settlement and shadow measurement do not call this gate.  Every
    behavior-changing consumer does, so an integrity incident survives process
    restart and cannot be bypassed by an enabled config flag.
    """
    try:
        from aletheia.portfolio.reconciliation_integrity import (
            get_active_reconciliation_integrity_incident,
        )
        incident = get_active_reconciliation_integrity_incident()
    except Exception:
        return "reconciliation_integrity_state_unavailable"
    if incident is not None:
        return f"reconciliation_integrity_incident:{incident['id']}"
    return None


def learning_influence_gate_reason() -> str | None:
    """One effective gate for all behavior-changing Learning influence."""
    global_reason = global_learning_refreeze_reason()
    if global_reason is not None:
        return global_reason.split(":", 1)[0]
    try:
        cfg = load_config()
    except Exception:
        return "learning_config_unavailable"
    if not bool(cfg.agent.autonomous_learning):
        return "autonomous_learning_disabled"
    if bool(cfg.runtime.paused):
        return "runtime_paused"
    return None


def learning_influence_enabled() -> bool:
    return learning_influence_gate_reason() is None


def learning_job_gate_reason(job_id: str) -> str | None:
    """Return why an autonomous job must not run, or ``None`` when allowed."""
    if not is_autonomous_learning_job(job_id):
        return None
    global_reason = global_learning_refreeze_reason()
    if global_reason is not None:
        return global_reason.split(":", 1)[0]
    cfg = load_config()
    if not bool(cfg.agent.autonomous_learning):
        return "disabled"
    if bool(cfg.runtime.paused):
        return "paused"
    return None


def _schedule_key(job_id: str) -> str:
    return _SCHEDULE_KEYS.get(job_id, "scheduler:registered")


def get_learning_job_health(job_id: str) -> LearningJobHealth:
    row = get_job_health(job_id)
    if row:
        return LearningJobHealth(**row)
    return LearningJobHealth(job_id=job_id, schedule_key=_schedule_key(job_id))


def _next_due(job_id: str, now: datetime) -> str | None:
    if job_id in {"learning_contribution_evaluator", "learning_lifecycle_controller"}:
        try:
            minutes = max(15, int(load_config().learning.evaluation_interval_minutes))
        except Exception:
            minutes = 60
        return (now + timedelta(minutes=minutes)).isoformat()
    if job_id in DAILY_LEARNING_SCHEDULES:
        hour, minute = DAILY_LEARNING_SCHEDULES[job_id]
        local_now = now.astimezone(_CST)
        due = datetime.combine(local_now.date(), time(hour, minute), tzinfo=_CST)
        if due <= local_now:
            due += timedelta(days=1)
        return due.astimezone(timezone.utc).isoformat()
    hours = _INTERVAL_HOURS.get(job_id)
    if hours:
        return (now + timedelta(hours=hours)).isoformat()
    return None


def current_daily_window(job_id: str, *, now: datetime | None = None) -> str | None:
    schedule = DAILY_LEARNING_SCHEDULES.get(job_id)
    if schedule is None:
        return None
    current = (now or utc_now()).astimezone(_CST)
    hour, minute = schedule
    due = datetime.combine(current.date(), time(hour, minute), tzinfo=_CST)
    window_date = current.date() if current >= due else current.date() - timedelta(days=1)
    return f"{job_id}:{window_date.isoformat()}"


def current_measurement_window(job_id: str, *, now: datetime | None = None) -> str:
    """One bounded UTC-hour window for restart recovery of measurement jobs."""
    current = (now or utc_now()).astimezone(timezone.utc)
    return f"{job_id}:{current.strftime('%Y-%m-%dT%H')}"


def measurement_catchup_due(job_id: str, *, now: datetime | None = None) -> bool:
    """Measurement ignores behavior gates but respects its explicit enable flag."""
    try:
        cfg = load_config()
        if not bool(cfg.learning.contribution_evaluation_enabled):
            return False
    except Exception:
        return False
    return get_learning_job_health(job_id).input_watermark != current_measurement_window(job_id, now=now)


def daily_catchup_due(job_id: str, *, now: datetime | None = None) -> bool:
    """True until the latest due daily window has a successful watermark."""
    if job_id not in DAILY_LEARNING_SCHEDULES:
        return False
    if learning_job_gate_reason(job_id) is not None:
        return False
    return get_learning_job_health(job_id).input_watermark != current_daily_window(job_id, now=now)


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def promotion_refreeze_reason(*, now: datetime | None = None) -> str | None:
    """Fail closed for automatic promotion when the Learning reviewer is unhealthy.

    Existing static behavior stays in place; only new automatic promotion is
    refrozen. Manual guard review remains a separate owner action.
    """
    gate_reason = learning_job_gate_reason("guard_promotion")
    if gate_reason is not None:
        return f"learning_{gate_reason}"
    current = now or utc_now()
    reviewer = get_learning_job_health("lesson_reviewer")
    if reviewer.last_succeeded_at is None:
        return "reviewer_never_succeeded"
    if reviewer.status == "failed" or reviewer.consecutive_failures > 0:
        return "reviewer_failed"
    last_success = _parse_timestamp(reviewer.last_succeeded_at)
    if last_success is None or current - last_success > _REVIEWER_STALE_AFTER:
        return "reviewer_stale"
    oldest = _parse_timestamp(reviewer.oldest_backlog_at)
    if oldest is not None and current - oldest > _REVIEWER_STALE_AFTER:
        return "reviewer_backlog_stale"
    if reviewer.backlog_count > _REVIEWER_BACKLOG_LIMIT:
        return "reviewer_backlog_over_capacity"
    return None


def _lesson_backlog() -> tuple[int, str | None]:
    row = get_db().execute(
        "SELECT COUNT(*), MIN(created_at) FROM ps_lessons WHERE status='proposed'"
    ).fetchone()
    return int(row[0] or 0), row[1]


def record_learning_job_started(job_id: str) -> None:
    if not is_autonomous_learning_job(job_id):
        return
    now = utc_now()
    health = get_learning_job_health(job_id).model_copy(update={
        "last_started_at": now.isoformat(),
        "status": "running",
        "last_error": None,
        "updated_at": now.isoformat(),
    })
    upsert_job_health(health)


def record_learning_job_success(job_id: str) -> None:
    if not is_autonomous_learning_job(job_id):
        return
    now = utc_now()
    health = get_learning_job_health(job_id)
    updates: dict = {
        "last_succeeded_at": now.isoformat(),
        "next_due_at": _next_due(job_id, now),
        "consecutive_failures": 0,
        "status": "healthy",
        "last_error": None,
        "updated_at": now.isoformat(),
    }
    window = current_daily_window(job_id, now=now)
    if window is not None:
        updates["input_watermark"] = window
    if job_id in {"dreaming_daily", "lesson_reviewer"}:
        count, oldest = _lesson_backlog()
        updates["backlog_count"] = count
        updates["oldest_backlog_at"] = oldest
    upsert_job_health(health.model_copy(update=updates))


def record_learning_job_failure(job_id: str, error: str) -> None:
    if not is_autonomous_learning_job(job_id):
        return
    now = utc_now()
    health = get_learning_job_health(job_id)
    count, oldest = (health.backlog_count, health.oldest_backlog_at)
    if job_id in {"dreaming_daily", "lesson_reviewer"}:
        count, oldest = _lesson_backlog()
    upsert_job_health(health.model_copy(update={
        "backlog_count": count,
        "oldest_backlog_at": oldest,
        "consecutive_failures": health.consecutive_failures + 1,
        "status": "failed",
        "last_error": str(error)[:1000],
        "last_failed_at": now.isoformat(),
        "last_failure_reason": str(error)[:1000],
        "updated_at": now.isoformat(),
    }))


def record_learning_job_skipped(job_id: str, reason: str) -> None:
    if not is_autonomous_learning_job(job_id):
        return
    now = utc_now()
    health = get_learning_job_health(job_id)
    upsert_job_health(health.model_copy(update={
        "status": reason,
        "last_error": None,
        "updated_at": now.isoformat(),
    }))


def record_measurement_job_started(job_id: str) -> None:
    """Persist health for a read-only measurement job outside autonomous gates."""
    now = utc_now()
    health = get_learning_job_health(job_id)
    upsert_job_health(health.model_copy(update={
        "last_started_at": now.isoformat(), "status": "running", "last_error": None,
        "updated_at": now.isoformat(),
    }))


def record_measurement_job_success(job_id: str) -> None:
    now = utc_now()
    health = get_learning_job_health(job_id)
    upsert_job_health(health.model_copy(update={
        "last_succeeded_at": now.isoformat(),
        "next_due_at": _next_due(job_id, now),
        "input_watermark": current_measurement_window(job_id, now=now),
        "consecutive_failures": 0, "status": "healthy", "last_error": None,
        "updated_at": now.isoformat(),
    }))


def record_measurement_job_failure(job_id: str, error: str) -> None:
    now = utc_now()
    health = get_learning_job_health(job_id)
    upsert_job_health(health.model_copy(update={
        "consecutive_failures": health.consecutive_failures + 1,
        "status": "failed", "last_error": str(error)[:1000],
        "last_failed_at": now.isoformat(),
        "last_failure_reason": str(error)[:1000],
        "updated_at": now.isoformat(),
    }))


def record_measurement_job_skipped(job_id: str, reason: str) -> None:
    now = utc_now()
    health = get_learning_job_health(job_id)
    upsert_job_health(health.model_copy(update={
        "status": reason, "last_error": None, "updated_at": now.isoformat(),
    }))
