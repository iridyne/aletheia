"""Shared protective-stop helpers for execution brokers.

The three real-exchange brokers (okx/binance/bitget) are structural clones of one
another. A protective stop must (a) be persisted on the Position row at open and
(b) tighten — never loosen — when averaging into an existing position. Keeping that
logic here gives all brokers ONE source of truth so the historical "stop dropped on
the floor at open" bug cannot re-appear via copy-paste. Mirrors paper_broker.
"""

from __future__ import annotations


def merge_protective_stop(
    direction: str,
    new_stop: float | None,
    old_stop: float | None,
) -> float | None:
    """Return the more protective of two stops when averaging into a position.

    For a long, the higher stop is tighter (closer to price → less downside);
    for a short, the lower stop is tighter. If either side is None, prefer the
    one that exists (a stop beats no stop). Returns None only when both are None.
    """
    if new_stop is None:
        return old_stop
    if old_stop is None:
        return new_stop
    if direction in ("long", "buy"):
        return max(new_stop, old_stop)
    return min(new_stop, old_stop)
