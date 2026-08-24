"""Conservative shared outcome eligibility for Learning analytics.

Phase 0 routes contribution and guard reporting through this adapter. Further
consumers migrate in later phases; unknown economics always fail closed.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO
from aletheia.analytics.outcome_integrity import (
    OutcomeAssessment,
    assess_position_closure,
)
from aletheia.analytics.outcome_semantics import validated_outcome_economics

CANDIDATE_OUTCOME_TEMPORAL_REASONS = (
    "unsettled",
    "dirty_or_missing_entry_time",
    "missing_due_at",
    "malformed_due_at",
    "missing_settled_at",
    "malformed_settled_at",
    "due_before_entry",
    "settled_before_entry",
    "settled_before_due",
    "settlement_after_as_of",
)


def candidate_outcome_temporal_reason(
    row: Mapping[str, Any],
    *,
    as_of_at: str | datetime,
) -> str | None:
    cutoff = _parse_dt(as_of_at)
    if cutoff is None:
        raise ValueError("as_of_at must be a valid timestamp")
    if str(row.get("status") or "").lower() != "settled":
        return "unsettled"
    entry_at = _parse_dt(row.get("entry_ts"))
    floor = _parse_dt(LEARNING_DATA_FLOOR_ISO)
    if entry_at is None or floor is None or entry_at < floor:
        return "dirty_or_missing_entry_time"
    due_value = row.get("due_at")
    if not due_value:
        return "missing_due_at"
    due_at = _parse_dt(due_value)
    if due_at is None:
        return "malformed_due_at"
    settled_value = row.get("settled_at")
    if not settled_value:
        return "missing_settled_at"
    settled_at = _parse_dt(settled_value)
    if settled_at is None:
        return "malformed_settled_at"
    if due_at < entry_at:
        return "due_before_entry"
    if settled_at < entry_at:
        return "settled_before_entry"
    if settled_at < due_at:
        return "settled_before_due"
    if settled_at > cutoff:
        return "settlement_after_as_of"
    return None


def assess_realized_position(row: dict[str, Any]) -> OutcomeAssessment:
    closed_at = row.get("closed_at")
    if not _at_or_after_floor(closed_at):
        return OutcomeAssessment(False, "dirty_or_missing_close_time", source="realized_position")
    return assess_position_closure(row)


def assess_candidate_outcome(
    row: Mapping[str, Any],
    *,
    as_of_at: str | datetime | None = None,
) -> OutcomeAssessment:
    if as_of_at is not None:
        temporal_reason = candidate_outcome_temporal_reason(row, as_of_at=as_of_at)
        if temporal_reason:
            return OutcomeAssessment(
                False,
                temporal_reason,
                category="candidate",
                source="candidate_outcome",
            )
    venue = row.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return OutcomeAssessment(False, "missing_venue", category="candidate", source="candidate_outcome")
    if str(row.get("direction") or "").lower() not in {"long", "short"}:
        return OutcomeAssessment(False, "missing_direction", category="candidate", source="candidate_outcome")
    net = _to_float(row.get("net_return_pct"))
    if net is None:
        return OutcomeAssessment(False, "net_economics_unavailable", category="candidate", source="candidate_outcome")
    edge = _to_float(row.get("min_tradable_edge_pct"))
    if edge is None or edge < 0:
        return OutcomeAssessment(False, "tradable_edge_unavailable", category="candidate", source="candidate_outcome")
    cost_model = row.get("cost_model_version")
    if not isinstance(cost_model, str) or not cost_model.strip():
        return OutcomeAssessment(False, "missing_cost_model_version", category="candidate", source="candidate_outcome")
    economics = validated_outcome_economics(row)
    if economics is None:
        return OutcomeAssessment(False, "cost_economics_incomplete", category="candidate", source="candidate_outcome")
    key = row.get("outcome_candidate_key")
    horizon = row.get("horizon")
    if not key or not horizon:
        return OutcomeAssessment(False, "missing_outcome_identity", category="candidate", source="candidate_outcome")
    return OutcomeAssessment(
        True,
        "eligible",
        category="candidate",
        outcome_key=f"candidate:{key}",
        horizon=str(horizon),
        net_return_pct=economics["net_return_pct"],
        source="candidate_outcome",
    )


def _at_or_after_floor(value: Any) -> bool:
    dt = _parse_dt(value)
    floor = _parse_dt(LEARNING_DATA_FLOOR_ISO)
    return bool(dt and floor and dt >= floor)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None
