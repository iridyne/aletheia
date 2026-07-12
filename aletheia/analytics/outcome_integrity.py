"""Dependency-neutral position-closure semantics and explicit read cohorts.

`positions.status='closed'` is a lifecycle fact, not proof of a strategy exit.
This module is the single source of truth shared by analytics, Learning, and later
Operator/Risk adapters.  It deliberately imports no Learning, Risk, Execution, or
exchange module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Literal


OUTCOME_SEMANTICS_VERSION = "oi-v1"

ClosureCategory = Literal["strategy", "administrative", "maintenance", "unknown"]

ADMINISTRATIVE_CLOSE_REASONS = frozenset({"reconciled_gone"})
MAINTENANCE_CLOSE_REASONS = frozenset({
    "demo_validation_cleanup",
    "units_bug_cleanup",
    "retry_after_fix",
})
SYNTHETIC_CLOSE_REASONS = ADMINISTRATIVE_CLOSE_REASONS | MAINTENANCE_CLOSE_REASONS

OUTCOME_EXCLUSION_REASONS = frozenset({
    "administrative_close",
    "maintenance_close",
    "not_marked_eligible",
    "unverified_exit_fill",
    "unverified_exit_price_source",
    "unknown_close_category",
    "unknown_semantics_version",
    "legacy_unverified_close",
    "position_not_closed",
    "missing_close_time",
    "missing_outcome_identity",
    "incomplete_realized_economics",
    "pre_clean_window",
    "incident_window",
    "missing_attribution",
})

_VERIFIED_EXIT_PRICE_SOURCES = frozenset({"broker_fill", "local_fill"})
_STRUCTURED_KEYS = frozenset({
    "close_category",
    "exit_price_source",
    "exit_fill_verified",
    "outcome_eligible",
    "outcome_exclusion_reason",
    "outcome_semantics_version",
})


@dataclass(frozen=True)
class ClosureSemantics:
    category: ClosureCategory
    eligible: bool
    exclusion_reason: str
    semantics_version: str | None
    exit_fill_verified: bool
    exit_price_source: str
    evidence_source: str


@dataclass(frozen=True)
class OutcomeAssessment:
    eligible: bool
    reason: str
    category: str = "unknown"
    semantics_version: str | None = None
    exit_fill_verified: bool = False
    exit_price_source: str = "unavailable"
    evidence_source: str = "unknown"
    outcome_key: str | None = None
    horizon: str | None = None
    net_return_pct: float | None = None
    source: str | None = None


def classify_closure(row: dict[str, Any]) -> ClosureSemantics:
    """Classify one position close without trusting PnL as exit evidence."""
    reason = _normalize(row.get("close_reason"))
    if reason in ADMINISTRATIVE_CLOSE_REASONS:
        return _semantics(
            "administrative", False, "administrative_close", row,
            evidence_source="close_reason",
        )
    if reason in MAINTENANCE_CLOSE_REASONS:
        return _semantics(
            "maintenance", False, "maintenance_close", row,
            evidence_source="close_reason",
        )

    if row.get("status") not in (None, "closed"):
        return _semantics("unknown", False, "position_not_closed", row)

    structured_values = {key: row.get(key) for key in _STRUCTURED_KEYS}
    has_structured_evidence = any(
        value not in (None, "", False, "unknown", "unavailable", "unclassified")
        for value in structured_values.values()
    )
    if not has_structured_evidence:
        return _semantics(
            "unknown", False, "legacy_unverified_close", row,
            evidence_source="legacy_reason",
        )

    version = _normalize(row.get("outcome_semantics_version")) or None
    category = _category(row.get("close_category"))
    verified = _to_bool(row.get("exit_fill_verified"))
    price_source = _normalize(row.get("exit_price_source")) or "unavailable"

    if version != OUTCOME_SEMANTICS_VERSION:
        return _semantics(category, False, "unknown_semantics_version", row)
    if category in {"administrative", "maintenance"}:
        return _semantics(category, False, f"{category}_close", row)
    if category != "strategy":
        return _semantics(category, False, "unknown_close_category", row)
    if not _to_bool(row.get("outcome_eligible")):
        exclusion = _normalize(row.get("outcome_exclusion_reason"))
        if exclusion not in OUTCOME_EXCLUSION_REASONS:
            exclusion = "not_marked_eligible"
        return _semantics(category, False, exclusion, row)
    if not verified:
        return _semantics(category, False, "unverified_exit_fill", row)
    if price_source not in _VERIFIED_EXIT_PRICE_SOURCES:
        return _semantics(category, False, "unverified_exit_price_source", row)
    return _semantics(category, True, "eligible", row)


def assess_position_closure(row: dict[str, Any]) -> OutcomeAssessment:
    """Return closure eligibility plus complete realized economics."""
    closure = classify_closure(row)
    if not closure.eligible:
        return _assessment_from_closure(closure)

    if not row.get("closed_at"):
        return _assessment_from_closure(closure, reason="missing_close_time")
    key = row.get("position_id") or row.get("id")
    if not key:
        return _assessment_from_closure(closure, reason="missing_outcome_identity")
    pnl = _to_float(row.get("realized_pnl"))
    entry = _to_float(row.get("entry_price"))
    quantity = _to_float(row.get("quantity"))
    if pnl is None or entry is None or quantity is None or entry <= 0 or quantity <= 0:
        return _assessment_from_closure(
            closure, reason="incomplete_realized_economics"
        )
    return _assessment_from_closure(
        closure,
        outcome_key=f"position:{key}",
        horizon="realized_close",
        net_return_pct=pnl / (entry * quantity) * 100.0,
    )


def assess_reportable_outcome(row: dict[str, Any]) -> OutcomeAssessment:
    """Add the versioned clean-window gate to economic close eligibility."""
    assessment = assess_position_closure(row)
    if not assessment.eligible:
        return assessment
    from aletheia.analytics.learning_window import assess_outcome_window

    window_reason = assess_outcome_window(row)
    if window_reason != "eligible":
        return replace(assessment, eligible=False, reason=window_reason)
    return assessment


def raw_account_events(
    *,
    execution_mode: str | None = None,
    closed_after: str | None = None,
    closed_before: str | None = None,
    limit: int = 10_000,
) -> list[dict]:
    """Read immutable closed-position events without implying outcome quality."""
    if limit < 1 or limit > 10_000:
        raise ValueError("limit must be between 1 and 10000")
    from aletheia.storage.sqlite import get_db

    conditions = ["status = 'closed'"]
    params: list[object] = []
    if execution_mode is not None:
        conditions.append("execution_mode = ?")
        params.append(execution_mode)
    if closed_after is not None:
        conditions.append("datetime(closed_at) >= datetime(?)")
        params.append(closed_after)
    if closed_before is not None:
        conditions.append("datetime(closed_at) < datetime(?)")
        params.append(closed_before)
    params.append(limit)
    rows = get_db().execute(
        "SELECT * FROM positions WHERE " + " AND ".join(conditions)
        + " ORDER BY datetime(closed_at) ASC, id ASC LIMIT ?",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def eligible_strategy_outcomes(**filters) -> list[dict]:
    """Read only closures proven eligible by the current central semantics."""
    return [
        row for row in raw_account_events(**filters)
        if assess_reportable_outcome(row).eligible
    ]


def administrative_closures(**filters) -> list[dict]:
    """Read administrative and maintenance closures for audit/diagnosis."""
    return [
        row for row in raw_account_events(**filters)
        if classify_closure(row).category in {"administrative", "maintenance"}
    ]


def build_outcome_cohort_summary(**filters) -> dict[str, Any]:
    """Describe raw close events and the verified judgement cohort side by side."""
    from aletheia.analytics.learning_window import outcome_window_metadata
    try:
        rows = raw_account_events(**filters)
    except Exception:
        return {
            "raw_event_count": None,
            "eligible_outcome_count": None,
            "excluded_outcome_count": None,
            "outcome_exclusion_reasons": {"account_event_read_failed": 1},
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "cohort_state": "unavailable",
            "raw_recorded_realized_pnl": None,
            "raw_economics_unavailable_count": None,
            "eligible_realized_pnl": None,
            **outcome_window_metadata(),
        }

    eligible_count = 0
    eligible_pnl = 0.0
    raw_recorded_pnl = 0.0
    raw_recorded_count = 0
    exclusion_reasons: dict[str, int] = {}
    for row in rows:
        raw_pnl = _to_float(row.get("realized_pnl"))
        if raw_pnl is not None:
            raw_recorded_pnl += raw_pnl
            raw_recorded_count += 1
        assessment = assess_reportable_outcome(row)
        if assessment.eligible:
            eligible_count += 1
            eligible_pnl += float(raw_pnl)
        else:
            exclusion_reasons[assessment.reason] = exclusion_reasons.get(assessment.reason, 0) + 1

    raw_count = len(rows)
    return {
        "raw_event_count": raw_count,
        "eligible_outcome_count": eligible_count,
        "excluded_outcome_count": raw_count - eligible_count,
        "outcome_exclusion_reasons": dict(sorted(exclusion_reasons.items())),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "cohort_state": "available" if eligible_count else "insufficient",
        "raw_recorded_realized_pnl": round(raw_recorded_pnl, 8) if raw_recorded_count else None,
        "raw_economics_unavailable_count": raw_count - raw_recorded_count,
        "eligible_realized_pnl": round(eligible_pnl, 8) if eligible_count else None,
        **outcome_window_metadata(),
    }


def is_current_outcome_artifact(
    artifact: dict[str, Any],
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> bool:
    """Admit a derived report only when its semantics and freshness are provable."""
    if artifact.get("outcome_semantics_version") != OUTCOME_SEMANTICS_VERSION:
        return False
    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ID
    if artifact.get("clean_window_anchor_id") != CLEAN_WINDOW_ANCHOR_ID:
        return False
    generated_at = artifact.get("generated_at")
    if not generated_at:
        return False
    try:
        generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current - generated.astimezone(timezone.utc)
    return timedelta(0) <= age <= max_age


def is_synthetic_close(reason: str | None) -> bool:
    return _normalize(reason) in SYNTHETIC_CLOSE_REASONS


def _semantics(
    category: ClosureCategory,
    eligible: bool,
    exclusion_reason: str,
    row: dict[str, Any],
    *,
    evidence_source: str = "structured",
) -> ClosureSemantics:
    return ClosureSemantics(
        category=category,
        eligible=eligible,
        exclusion_reason=exclusion_reason,
        semantics_version=_normalize(row.get("outcome_semantics_version")) or None,
        exit_fill_verified=_to_bool(row.get("exit_fill_verified")),
        exit_price_source=_normalize(row.get("exit_price_source")) or "unavailable",
        evidence_source=evidence_source,
    )


def _assessment_from_closure(
    closure: ClosureSemantics,
    *,
    reason: str | None = None,
    outcome_key: str | None = None,
    horizon: str | None = None,
    net_return_pct: float | None = None,
) -> OutcomeAssessment:
    eligible = closure.eligible and reason is None
    return OutcomeAssessment(
        eligible=eligible,
        reason=reason or closure.exclusion_reason,
        category=closure.category,
        semantics_version=closure.semantics_version,
        exit_fill_verified=closure.exit_fill_verified,
        exit_price_source=closure.exit_price_source,
        evidence_source=closure.evidence_source,
        outcome_key=outcome_key if eligible else None,
        horizon=horizon if eligible else None,
        net_return_pct=net_return_pct if eligible else None,
        source="realized_position",
    )


def _category(value: Any) -> ClosureCategory:
    normalized = _normalize(value)
    if normalized in {"strategy", "administrative", "maintenance"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return _normalize(value) in {"1", "true", "yes"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
