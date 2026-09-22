"""One-Cancels-the-Other (OCO) resolution for proactive limit exits (exit-intent M3.5).

When the Operator places a proactive reduce-only limit exit, it coexists on the
exchange with the position's protective stop. Exactly one should ultimately execute —
whichever fills first, the other must be cancelled (and a partial limit fill shrinks
the stop to the remaining size so the two never over-close).

This module holds the PURE decision logic: given the observed states of the two legs,
decide what to cancel and whether the position is closed. The exchange-side adapter
(placing the reduce-only limit, polling leg states, issuing the cancel) is intentionally
kept OUT of here so the decision is unit-testable without a live exchange — that adapter
is the needs-demo stub filled per broker (see resolve_oco callers + M3.5 notes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

LegStatus = Literal["open", "partial", "filled", "cancelled", "absent"]
LegKind = Literal["limit_exit", "protective_stop"]


@dataclass
class OcoLeg:
    kind: LegKind
    status: LegStatus
    filled_qty: float = 0.0
    total_qty: float = 0.0

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.total_qty - self.filled_qty)


@dataclass
class OcoResolution:
    cancel: list[LegKind] = field(default_factory=list)   # legs to cancel
    position_closed: bool = False                          # fully closed by a fill
    reduce_stop_to_qty: float | None = None                # partial limit fill → shrink stop
    reason: str = ""


def resolve_oco(limit: OcoLeg, stop: OcoLeg) -> OcoResolution:
    """Decide the OCO action for a (limit_exit, protective_stop) pair. Pure.

    Precedence:
      • both legs filled (race) — position closed; reduce-only caps the second leg so
        there is nothing to cancel, but flag it for audit.
      • either leg fully filled — position closed; cancel the other.
      • limit partially filled — position still open; shrink the stop to the remaining
        quantity and keep both legs (the stop must not still cover the filled portion).
      • otherwise — no action.
    """
    limit_filled = limit.status == "filled"
    stop_filled = stop.status == "filled"

    if limit_filled and stop_filled:
        return OcoResolution(position_closed=True, reason="both_filled_race")
    if limit_filled:
        return OcoResolution(cancel=["protective_stop"], position_closed=True, reason="limit_filled")
    if stop_filled:
        return OcoResolution(cancel=["limit_exit"], position_closed=True, reason="stop_filled")

    if limit.status == "partial":
        # The limit ate part of the position; the stop must now cover only the rest.
        return OcoResolution(
            reduce_stop_to_qty=limit.remaining_qty,
            reason="limit_partial",
        )
    if stop.status == "partial":
        return OcoResolution(reduce_stop_to_qty=None, reason="stop_partial")

    return OcoResolution(reason="both_open")
