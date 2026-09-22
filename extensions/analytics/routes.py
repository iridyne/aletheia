"""Optional outcome-derived Analytics report endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/confidence-calibration")
def get_confidence_calibration(execution_mode: str | None = None):
    """Conviction calibration report — actual vs expected win rate per conviction bucket.
    Scoped to the resolved (or explicit) ``execution_mode`` (#1939).
    """
    from extensions.analytics.confidence_calibration import get_latest_calibration_report, build_calibration_report
    from aletheia.analytics.execution_mode import (
        execution_mode_identity,
        resolve_selected_execution_mode,
    )
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    mode = (execution_mode or resolve_selected_execution_mode())
    if not mode:
        return {
            "status": "unavailable",
            "unavailable_reason": "execution_mode_unresolved",
            "buckets": [],
            "ece": None,
            "ece_grade": "insufficient_data",
            **execution_mode_identity(None),
        }
    report = get_latest_calibration_report(execution_mode=mode)
    if not report:
        report = build_calibration_report(execution_mode=mode)
    result = report or {"status": "no_data", "buckets": [], "ece": None, "ece_grade": "insufficient_data"}
    return {
        **result,
        **execution_mode_identity(mode),
        **build_outcome_cohort_summary(execution_mode=mode),
    }


@router.get("/reasoning-feedback")
def get_reasoning_feedback(execution_mode: str | None = None):
    """Reasoning quality feedback — proposal rejection patterns and approval win rates.
    Scoped to the resolved (or explicit) ``execution_mode`` (#1939).
    """
    from aletheia.analytics.execution_mode import (
        execution_mode_identity,
        resolve_selected_execution_mode,
    )
    from extensions.analytics.reasoning_feedback import get_latest_reasoning_feedback
    mode = (execution_mode or resolve_selected_execution_mode())
    if not mode:
        return {
            "status": "unavailable",
            "unavailable_reason": "execution_mode_unresolved",
            **execution_mode_identity(None),
        }
    fb = get_latest_reasoning_feedback(execution_mode=mode)
    if fb:
        return fb
    return {"status": "no_data", **execution_mode_identity(mode)}
