"""Executable-venue order-size rules.

This is deliberately part of the execution boundary: market-data metadata is not
authoritative for an order.  Every exchange-bound proposal obtains its rules from
the venue that will receive the order.  A failed refresh is a rejection, never a
reason to guess at an order size.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, InvalidOperation
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from collections.abc import Awaitable, Callable

from aletheia.config.exchanges import EXECUTION_MODE_VENUES

_VENUES = EXECUTION_MODE_VENUES
_CACHE_TTL_SECONDS = 300
_cache: dict[tuple[str, str, str], tuple["OrderConstraints", float]] = {}
_cache_lock = threading.Lock()


class ConstraintUnavailable(RuntimeError):
    """The authoritative venue rules cannot safely be used for this order."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


@dataclass(frozen=True)
class OrderConstraints:
    venue: str
    symbol: str
    market_type: str
    min_quantity: Decimal
    max_quantity: Decimal | None
    amount_step: Decimal
    min_notional: Decimal | None


# (execution_mode, venue, symbol, market_type) -> constraints. The mode is part
# of the contract because a demo venue is a different listing set from its live
# counterpart: reading live rules for a demo order lets a symbol that the demo
# venue does not list pass pre-trade validation and fail at submission instead.
_constraints_reader: Callable[[str, str, str, str], Awaitable[OrderConstraints]] | None = None


def configure_order_constraints_reader(reader: Callable[[str, str, str, str], Awaitable[OrderConstraints]] | None) -> None:
    """Composition binds external acquisition; cached rules never survive unbinding."""
    global _constraints_reader
    with _cache_lock:
        _constraints_reader = reader
        _cache.clear()


def venue_for_execution_mode(execution_mode: str) -> str | None:
    """Return the venue which will actually receive this order."""
    return _VENUES.get(execution_mode)


def _decimal(value: Any, field: str, *, required: bool = False) -> Decimal | None:
    if value is None:
        if required:
            raise ConstraintUnavailable(f"exchange rule missing {field}")
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ConstraintUnavailable(f"exchange rule has invalid {field}") from exc
    if parsed <= 0:
        if required:
            raise ConstraintUnavailable(f"exchange rule has non-positive {field}")
        return None
    return parsed


def normalize_quantity(quantity: float, price: float, constraints: OrderConstraints, *, allow_increase: bool = True) -> float:
    """Round an opening size *up* to the venue step and all published minima.

    Bounded EntryPlan quantities instead round down and may not cross minima.
    The caller must send the returned value through Risk Engine before submission.
    """
    qty = _decimal(quantity, "quantity", required=True)
    step = constraints.amount_step
    minimum = constraints.min_quantity
    if constraints.min_notional is not None:
        quoted_price = _decimal(price, "price", required=True)
        minimum = max(minimum, constraints.min_notional / quoted_price)
    # Restore at most one ULP lost by the Plan's max_notional / price division.
    required = max(qty, minimum) if allow_increase else Decimal(str(math.nextafter(quantity, math.inf)))
    rounding = ROUND_CEILING if allow_increase else ROUND_FLOOR
    adjusted = (required / step).to_integral_value(rounding=rounding) * step
    if adjusted <= 0 or adjusted < minimum:
        raise ConstraintUnavailable("bounded order quantity is below exchange minimum")
    if constraints.max_quantity is not None and adjusted > constraints.max_quantity:
        raise ConstraintUnavailable("order quantity exceeds exchange maximum")
    return float(adjusted)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Gateway functions are synchronous and can be called by an async route.  The
    # cold refresh must not freeze that route's event loop.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def get_order_constraints(execution_mode: str, symbol: str, market_type: str) -> OrderConstraints:
    """Read-through, short-lived cache of rules for the actual order venue.

    A stale cache is never used after a failed refresh: callers receive a clear
    rejection and no exchange order is submitted.
    """
    venue = venue_for_execution_mode(execution_mode)
    if venue is None:
        raise ConstraintUnavailable(f"no executable venue rules for mode {execution_mode}")
    reader = _constraints_reader
    if reader is None:
        raise ConstraintUnavailable("executable venue rules unavailable")
    # Keyed by execution_mode, not venue: bitget_demo and live bitget list
    # different symbols, so one shared entry would serve live rules to a demo
    # order (that is how RENDER passed validation and then died at submission
    # with contract_size_unavailable).
    key = (execution_mode, symbol.upper(), market_type)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
    constraints = _run(reader(execution_mode, venue, symbol, market_type))
    with _cache_lock:
        _cache[key] = (constraints, time.monotonic())
    return constraints


def prepare_exchange_quantity(execution_mode: str, symbol: str, market_type: str, quantity: float, price: float, *, allow_increase: bool = True) -> float:
    """Return the legal quantity for a real exchange order, or raise fail-closed."""
    return normalize_quantity(quantity, price, get_order_constraints(execution_mode, symbol, market_type), allow_increase=allow_increase)
