"""Conservative shared outcome eligibility for Learning analytics.

Phase 0 routes contribution and guard reporting through this adapter. Further
consumers migrate in later phases; unknown economics always fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any

from aletheia.analytics.close_reasons import is_synthetic_close
from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO


@dataclass(frozen=True)
class OutcomeAssessment:
    eligible: bool
    reason: str
    outcome_key: str | None = None
    horizon: str | None = None
    net_return_pct: float | None = None
    source: str | None = None


def assess_realized_position(row: dict[str, Any]) -> OutcomeAssessment:
    closed_at = row.get("closed_at")
    if not _at_or_after_floor(closed_at):
        return OutcomeAssessment(False, "dirty_or_missing_close_time", source="realized_position")
    if is_synthetic_close(row.get("close_reason")):
        return OutcomeAssessment(False, "synthetic_close", source="realized_position")
    pnl = _to_float(row.get("realized_pnl"))
    entry = _to_float(row.get("entry_price"))
    quantity = _to_float(row.get("quantity"))
    if pnl is None or entry is None or quantity is None or entry <= 0 or quantity <= 0:
        return OutcomeAssessment(False, "incomplete_realized_economics", source="realized_position")
    key = row.get("position_id") or row.get("id")
    if not key:
        return OutcomeAssessment(False, "missing_outcome_identity", source="realized_position")
    return OutcomeAssessment(
        True,
        "eligible",
        outcome_key=f"position:{key}",
        horizon="realized_close",
        net_return_pct=pnl / (entry * quantity) * 100.0,
        source="realized_position",
    )


def assess_candidate_outcome(row: dict[str, Any]) -> OutcomeAssessment:
    if str(row.get("status") or "").lower() != "settled":
        return OutcomeAssessment(False, "unsettled", source="candidate_outcome")
    if not _at_or_after_floor(row.get("entry_ts")):
        return OutcomeAssessment(False, "dirty_or_missing_entry_time", source="candidate_outcome")
    if str(row.get("direction") or "").lower() not in {"long", "short"}:
        return OutcomeAssessment(False, "missing_direction", source="candidate_outcome")
    net = _to_float(row.get("net_return_pct"))
    if net is None:
        return OutcomeAssessment(False, "net_economics_unavailable", source="candidate_outcome")
    if not row.get("cost_model_version"):
        return OutcomeAssessment(False, "missing_cost_model_version", source="candidate_outcome")
    key = row.get("outcome_candidate_key")
    horizon = row.get("horizon")
    if not key or not horizon:
        return OutcomeAssessment(False, "missing_outcome_identity", source="candidate_outcome")
    return OutcomeAssessment(
        True,
        "eligible",
        outcome_key=f"candidate:{key}",
        horizon=str(horizon),
        net_return_pct=net,
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
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None
