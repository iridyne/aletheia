"""CCXT acquisition and conversion of executable-venue order rules."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aletheia.execution.order_constraints import ConstraintUnavailable, OrderConstraints, _decimal
from extensions.exchanges.ccxt.submission_errors import is_ambiguous_submission_error
from aletheia.uta.compat import instrument_from_local


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
    instrument = instrument_from_local(symbol, market_type)
    market = (exchange.markets or {}).get(instrument.venue_symbol)
    if market and market.get("active", True):
        if instrument.market_type == "spot" and market.get("spot"):
            return market
        if instrument.market_type == "usdt_futures" and market.get("swap") and not market.get("future"):
            return market
    raise ConstraintUnavailable(f"{symbol} is not an active {market_type} market on the executable venue")


def _listing_view(execution_mode: str) -> tuple[bool, dict[str, object]]:
    """The sandbox flag and extra options that define this mode's listing set.

    Resolved from the same profile class that submits the order, so pre-trade
    validation reads the venue the order will actually meet. Profile resolution
    failure is not permission to fall back to a different listing; it is treated
    as ConstraintUnavailable(temporary=True).
    """
    try:
        from aletheia.config.loader import load_config
        from extensions.exchanges.composition import _profile_spec

        *_rest, profile_type = _profile_spec(load_config(), execution_mode)
        return bool(getattr(profile_type, "sandbox", False)), dict(
            getattr(profile_type, "listing_options", {}) or {}
        )
    except Exception as exc:
        raise ConstraintUnavailable(
            f"execution mode '{execution_mode}' profile resolution failed",
            temporary=True,
        ) from exc


async def read_order_constraints(
    execution_mode: str, venue: str, symbol: str, market_type: str
) -> OrderConstraints:
    from aletheia.net import make_ccxt

    exchange_id = "binanceusdm" if venue == "binance" else venue
    default_type = "spot" if market_type == "spot" else "swap"
    # A demo venue lists a different symbol set than its live counterpart. Reading
    # the live listing for a demo order let RENDER pass validation and then fail
    # at submission with contract_size_unavailable, because the demo market map
    # had no entry for it at all.
    sandbox, extra = _listing_view(execution_mode)
    exchange, session = make_ccxt(
        exchange_id, options={"defaultType": default_type, **extra}
    )
    try:
        if sandbox:
            exchange.set_sandbox_mode(True)
        await exchange.load_markets()
        market = _find_market(exchange, symbol, market_type)
        return constraints_from_market(
            venue=venue, symbol=symbol, market_type=market_type, market=market,
            precision_mode=_precision_mode_name(exchange),
        )
    except ConstraintUnavailable:
        raise
    except Exception as exc:
        raise ConstraintUnavailable(
            f"could not refresh executable venue rules: {exc}",
            temporary=is_ambiguous_submission_error(exc),
        ) from exc
    finally:
        try:
            await exchange.close()
        finally:
            if session is not None:
                await session.close()
