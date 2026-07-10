"""Shared interpretation for cost-aware Opportunity counterfactuals."""

from __future__ import annotations

from typing import Any


MISSED_ALPHA = "missed_alpha_suspect"
POSITIVE_GROSS_ONLY = "positive_gross_move_only"
PROTECTIVE = "protective"
INSUFFICIENT = "insufficient_data"


def classify_counterfactual(
    net_return_pct: float | None,
    directional_return_pct: float | None,
    min_tradable_edge_pct: float | None,
) -> str:
    """Classify a rejected opportunity without promoting gross movement to alpha."""
    edge = max(0.0, float(min_tradable_edge_pct or 0.0))
    if net_return_pct is not None and float(net_return_pct) > edge:
        return MISSED_ALPHA
    if directional_return_pct is not None and float(directional_return_pct) > 0:
        return POSITIVE_GROSS_ONLY
    if net_return_pct is not None:
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
    return {
        "gross_return_pct": raw,
        "directional_return_pct": directional,
        "net_return_pct": _to_float(row.get("net_return_pct")),
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
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
