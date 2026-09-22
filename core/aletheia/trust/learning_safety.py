"""Read-only optional Learning safety evidence for Core consumers."""

from collections.abc import Callable


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
})


def is_autonomous_learning_job(job_id: str) -> bool:
    return job_id in AUTONOMOUS_LEARNING_JOBS


_influence_gate: Callable[[], str | None] | None = None
_refreeze_reason: Callable[[], str | None] | None = None


def configure_learning_safety(
    *,
    influence_gate: Callable[[], str | None] | None = None,
    refreeze_reason: Callable[[], str | None] | None = None,
) -> None:
    global _influence_gate, _refreeze_reason
    _influence_gate = influence_gate
    _refreeze_reason = refreeze_reason


def learning_influence_allowed() -> bool:
    if _influence_gate is None:
        return False
    try:
        return _influence_gate() is None
    except Exception:
        return False


def learning_refreeze_reason() -> str | None:
    if _refreeze_reason is not None:
        try:
            reason = _refreeze_reason()
            if reason is None or (isinstance(reason, str) and reason):
                return reason
        except Exception:
            pass
    return "learning_integrity_state_unavailable"
