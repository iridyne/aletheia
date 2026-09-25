"""Shared cost, temporal and clean-cohort Outcome interpretation."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Mapping

from aletheia.utils.dt import parse_utc
from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO
from aletheia.analytics.outcome_integrity import OutcomeAssessment, assess_reportable_outcome

MISSED_ALPHA = "missed_alpha_suspect"
POSITIVE_GROSS_ONLY = "positive_gross_move_only"
PROTECTIVE = "protective"
INSUFFICIENT = "insufficient_data"
_ECONOMICS_TOLERANCE = 1e-8
_SUPPORTED_MARKET_TYPES = frozenset({
    "spot", "perp", "futures", "swap", "usdt_futures",
})
_SETTLEMENT_HORIZON_SECONDS = {
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "24h": 24 * 60 * 60,
}
_OHLCV_SETTLEMENT_SOURCES = frozenset({
    "horizon_ohlcv", "horizon_ohlcv_local",
})


def valid_settlement_price_provenance(row: dict[str, Any]) -> bool:
    """Return whether persisted price evidence can authorize net economics."""
    source = row.get("settlement_price_source")
    lag = _to_float(row.get("settlement_lag_sec"))
    horizon_seconds = _SETTLEMENT_HORIZON_SECONDS.get(
        str(row.get("horizon") or "")
    )
    if (
        not isinstance(source, str)
        or lag is None
        or lag < 0
        or horizon_seconds is None
    ):
        return False
    if source in _OHLCV_SETTLEMENT_SOURCES:
        return True
    return source == "near_due_ticker" and lag <= horizon_seconds * 0.10


def validated_outcome_economics(row: dict[str, Any]) -> dict[str, float] | None:
    """Return one reconcilable persisted cost tuple, otherwise fail closed."""
    venue = row.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        return None
    cost_model = row.get("cost_model_version")
    if not isinstance(cost_model, str) or not cost_model.strip():
        return None
    if not valid_settlement_price_provenance(row):
        return None
    fields = (
        "directional_return_pct",
        "estimated_fee_pct",
        "estimated_spread_slippage_pct",
        "estimated_funding_pct",
        "net_return_pct",
        "min_tradable_edge_pct",
    )
    values = {field: _to_float(row.get(field)) for field in fields}
    if any(value is None for value in values.values()):
        return None
    economics = {field: float(value) for field, value in values.items()}
    cost_floor = _to_float(row.get("min_cost_covering_edge_pct"))
    if cost_floor is not None:
        economics["min_cost_covering_edge_pct"] = cost_floor
    direction = str(row.get("direction") or "").lower()
    entry = _to_float(row.get("entry_price_usd"))
    exit_price = _to_float(row.get("exit_price_usd"))
    if (
        direction not in {"long", "short"}
        or entry is None or entry <= 0
        or exit_price is None or exit_price <= 0
    ):
        return None
    expected_directional = (exit_price - entry) / entry * 100.0
    if direction == "short":
        expected_directional = -expected_directional
    if not math.isclose(
        economics["directional_return_pct"], expected_directional,
        rel_tol=0.0, abs_tol=_ECONOMICS_TOLERANCE,
    ):
        return None
    fee = economics["estimated_fee_pct"]
    spread = economics["estimated_spread_slippage_pct"]
    if fee < 0 or spread < 0 or economics["min_tradable_edge_pct"] < 0:
        return None
    market_type = str(row.get("market_type") or "").lower()
    if market_type not in _SUPPORTED_MARKET_TYPES:
        return None
    if market_type == "spot" and economics["estimated_funding_pct"] != 0.0:
        return None
    expected_net = (
        economics["directional_return_pct"]
        - fee
        - spread
        - economics["estimated_funding_pct"]
    )
    if not math.isclose(
        economics["net_return_pct"], expected_net,
        rel_tol=0.0, abs_tol=_ECONOMICS_TOLERANCE,
    ):
        return None
    return economics


def classify_counterfactual(
    net_return_pct: float | None,
    directional_return_pct: float | None,
    min_tradable_edge_pct: float | None,
) -> str:
    """Classify a rejected opportunity without promoting gross movement to alpha."""
    net = _to_float(net_return_pct)
    directional = _to_float(directional_return_pct)
    edge = _to_float(min_tradable_edge_pct)
    complete_net = net is not None and edge is not None and edge >= 0
    if complete_net and net > edge:
        return MISSED_ALPHA
    if directional is not None and directional > 0:
        return POSITIVE_GROSS_ONLY
    if complete_net:
        return PROTECTIVE
    return INSUFFICIENT


def outcome_values(row: dict[str, Any], direction: str | None = None) -> dict[str, float | None]:
    """Read new outcome semantics while exposing a legacy gross directional view.

    Legacy rows may have only ``forward_return_pct``. They remain useful for
    explaining price movement but intentionally return ``net_return_pct=None``.
    """
    raw = _to_float(row.get("gross_return_pct"))
    if raw is None:
        raw = _to_float(row.get("forward_return_pct"))
    directional = _to_float(row.get("directional_return_pct"))
    side = str(direction or row.get("direction") or "").lower()
    if directional is None and raw is not None:
        if side == "long":
            directional = raw
        elif side == "short":
            directional = -raw
    economics = validated_outcome_economics(row)
    return {
        "gross_return_pct": raw,
        "directional_return_pct": directional,
        "net_return_pct": (
            economics["net_return_pct"] if economics is not None else None
        ),
        "min_tradable_edge_pct": _to_float(row.get("min_tradable_edge_pct")),
    }


def classify_outcome_row(row: dict[str, Any], direction: str | None = None) -> str:
    values = outcome_values(row, direction)
    return classify_counterfactual(
        values["net_return_pct"],
        values["directional_return_pct"],
        values["min_tradable_edge_pct"],
    )


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


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
    cutoff = parse_utc(as_of_at)
    if cutoff is None:
        raise ValueError("as_of_at must be a valid timestamp")
    if str(row.get("status") or "").lower() != "settled":
        return "unsettled"
    entry_at = parse_utc(row.get("entry_ts"))
    floor = parse_utc(LEARNING_DATA_FLOOR_ISO)
    if entry_at is None or floor is None or entry_at < floor:
        return "dirty_or_missing_entry_time"
    due_value = row.get("due_at")
    if not due_value:
        return "missing_due_at"
    due_at = parse_utc(due_value)
    if due_at is None:
        return "malformed_due_at"
    settled_value = row.get("settled_at")
    if not settled_value:
        return "missing_settled_at"
    settled_at = parse_utc(settled_value)
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
    return assess_reportable_outcome(row)


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
    dt = parse_utc(value)
    floor = parse_utc(LEARNING_DATA_FLOOR_ISO)
    return bool(dt and floor and dt >= floor)
