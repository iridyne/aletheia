"""Executable-venue order-size rules.

This is deliberately part of the execution boundary: market-data metadata is not
authoritative for an order.  Every exchange-bound proposal obtains its rules from
the venue that will receive the order.  A failed refresh is a rejection, never a
reason to guess at an order size.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, InvalidOperation
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any


_VENUES = {
    "bitget_demo": "bitget",
    "bitget_live_limited": "bitget",
    "okx_demo": "okx",
    "okx_live_limited": "okx",
    "binance_testnet": "binance",
    "binance_live_limited": "binance",
}
_CACHE_TTL_SECONDS = 300
_cache: dict[tuple[str, str, str], tuple["OrderConstraints", float]] = {}
_cache_lock = threading.Lock()


class ConstraintUnavailable(RuntimeError):
    """The authoritative venue rules cannot safely be used for this order."""


@dataclass(frozen=True)
class OrderConstraints:
    venue: str
    symbol: str
    market_type: str
    min_quantity: Decimal
    max_quantity: Decimal | None
    amount_step: Decimal
    min_notional: Decimal | None


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
    # #1481: non-finite floats (NaN, inf) that survive float→Decimal conversion
    # must be rejected before they corrupt order sizing arithmetic.
    try:
        if not parsed.is_finite():
            raise ConstraintUnavailable(f"exchange rule has non-finite {field}: {value!r}")
    except InvalidOperation as exc:
        raise ConstraintUnavailable(f"exchange rule has non-finite {field}") from exc
    if parsed <= 0:
        if required:
            raise ConstraintUnavailable(f"exchange rule has non-positive {field}")
        return None
    return parsed


def _amount_step(precision: Any, precision_mode: str) -> Decimal:
    if precision_mode == "significant_digits":
        # This has no fixed increment.  Guessing a step would allow an invalid order.
        raise ConstraintUnavailable("exchange uses unsupported amount precision")
    if precision_mode == "decimal_places":
        try:
            places = int(precision)
        except (TypeError, ValueError) as exc:
            raise ConstraintUnavailable("exchange rule missing amount precision") from exc
        if places < 0:
            raise ConstraintUnavailable("exchange rule has invalid amount precision")
        return Decimal(1).scaleb(-places)
    return _decimal(precision, "amount precision", required=True)  # tick-size mode


def constraints_from_market(
    *, venue: str, symbol: str, market_type: str, market: dict[str, Any],
    precision_mode: str,
) -> OrderConstraints:
    """Convert CCXT market metadata into Aletheia's canonical base-coin units."""
    limits = market.get("limits") or {}
    amount_limits = limits.get("amount") or {}
    contract_size = Decimal("1")
    if market_type != "spot":
        contract_size = _decimal(market.get("contractSize") or 1, "contract size", required=True)

    # Bitget spot may correctly publish minTradeAmount=0 while enforcing its
    # positive minTradeUSDT instead.  Zero is therefore not silently treated as
    # a valid minimum quantity, but is valid when a positive notional minimum is
    # present (as Bitget's official symbol configuration specifies).
    min_amount = _decimal(amount_limits.get("min"), "minimum amount") or Decimal("0")
    max_amount = _decimal(amount_limits.get("max"), "maximum amount")
    step = _amount_step((market.get("precision") or {}).get("amount"), precision_mode)
    min_cost = _decimal((limits.get("cost") or {}).get("min"), "minimum notional")
    if min_amount <= 0 and min_cost is None:
        raise ConstraintUnavailable("exchange rule has no positive minimum amount or notional")
    return OrderConstraints(
        venue=venue, symbol=symbol, market_type=market_type,
        min_quantity=min_amount * contract_size,
        max_quantity=max_amount * contract_size if max_amount else None,
        amount_step=step * contract_size,
        min_notional=min_cost,
    )


def normalize_quantity(quantity: float, price: float, constraints: OrderConstraints) -> float:
    """Round an opening size *up* to the venue step and all published minima.

    The caller must send the returned value through Risk Engine before submission.
    """
    qty = _decimal(quantity, "quantity", required=True)
    step = constraints.amount_step
    required = max(qty, constraints.min_quantity)
    if constraints.min_notional is not None:
        quoted_price = _decimal(price, "price", required=True)
        required = max(required, constraints.min_notional / quoted_price)
    adjusted = (required / step).to_integral_value(rounding=ROUND_CEILING) * step
    if constraints.max_quantity is not None and adjusted > constraints.max_quantity:
        raise ConstraintUnavailable("order quantity exceeds exchange maximum")
    return float(adjusted)


def _precision_mode_name(exchange: Any) -> str:
    # CCXT exports integer constants; accepting the names keeps this converter
    # straightforward to test without a network-bound exchange object.
    mode = getattr(exchange, "precisionMode", None)
    try:
        import ccxt
        if mode == ccxt.DECIMAL_PLACES:
            return "decimal_places"
        if mode == ccxt.SIGNIFICANT_DIGITS:
            return "significant_digits"
        if mode == ccxt.TICK_SIZE:
            return "tick_size"
    except ImportError:  # pragma: no cover - ccxt is a project dependency
        pass
    raise ConstraintUnavailable("exchange reported an unknown amount precision mode")


def _find_market(exchange: Any, symbol: str, market_type: str) -> dict[str, Any]:
    base, quote = symbol.upper().split("/", 1)
    wants_spot = market_type == "spot"
    for market in (exchange.markets or {}).values():
        if not market.get("active", True):
            continue
        if str(market.get("base") or "").upper() != base or str(market.get("quote") or "").upper() != quote:
            continue
        if wants_spot and market.get("spot"):
            return market
        if not wants_spot and (market.get("swap") or market.get("future")):
            return market
    raise ConstraintUnavailable(f"{symbol} is not an active {market_type} market on the executable venue")


async def _fetch_constraints(venue: str, symbol: str, market_type: str) -> OrderConstraints:
    from aletheia.net import make_ccxt

    exchange_id = "binanceusdm" if venue == "binance" else venue
    default_type = "spot" if market_type == "spot" else "swap"
    exchange, session = make_ccxt(exchange_id, options={"defaultType": default_type})
    try:
        await exchange.load_markets()
        market = _find_market(exchange, symbol, market_type)
        return constraints_from_market(
            venue=venue, symbol=symbol, market_type=market_type, market=market,
            precision_mode=_precision_mode_name(exchange),
        )
    except ConstraintUnavailable:
        raise
    except Exception as exc:
        raise ConstraintUnavailable(f"could not refresh executable venue rules: {exc}") from exc
    finally:
        try:
            await exchange.close()
        finally:
            if session is not None:
                await session.close()


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
    key = (venue, symbol.upper(), market_type)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]
    constraints = _run(_fetch_constraints(venue, symbol, market_type))
    with _cache_lock:
        _cache[key] = (constraints, time.monotonic())
    return constraints


def prepare_exchange_quantity(execution_mode: str, symbol: str, market_type: str, quantity: float, price: float) -> float:
    """Return the legal quantity for a real exchange order, or raise fail-closed."""
    return normalize_quantity(quantity, price, get_order_constraints(execution_mode, symbol, market_type))
