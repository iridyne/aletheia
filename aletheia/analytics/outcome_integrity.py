"""Dependency-neutral position-closure semantics and explicit read cohorts.

`positions.status='closed'` is a lifecycle fact, not proof of a strategy exit.
This module is the single source of truth shared by analytics, Learning, and later
Operator/Risk adapters.  It deliberately imports no Learning, Risk, Execution, or
exchange module.
"""

from __future__ import annotations

from dataclasses import dataclass
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

    if not any(key in row for key in _STRUCTURED_KEYS):
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
        if assess_position_closure(row).eligible
    ]


def administrative_closures(**filters) -> list[dict]:
    """Read administrative and maintenance closures for audit/diagnosis."""
    return [
        row for row in raw_account_events(**filters)
        if classify_closure(row).category in {"administrative", "maintenance"}
    ]


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
