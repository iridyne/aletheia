"""Narrow local-row to frozen UTA query compatibility helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from aletheia.data.symbols import to_canonical, to_ccxt, to_ccxt_perp
from aletheia.uta.models import InstrumentIdentity, QueryScope


def utc(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def instrument_from_local(symbol: str, market_type: str) -> InstrumentIdentity:
    canonical = to_canonical(symbol)
    base, separator, quote = canonical.partition("/")
    if not separator or not base or not quote:
        raise ValueError("local instrument symbol is invalid")
    normalized_type = "spot" if market_type == "spot" else "usdt_futures"
    venue_symbol = (
        to_ccxt(canonical)
        if normalized_type == "spot"
        else to_ccxt_perp(canonical)
    )
    return InstrumentIdentity(
        canonical_symbol=canonical,
        venue_symbol=venue_symbol,
        market_type=normalized_type,
        base_currency=base,
        quote_currency=quote,
        settlement_currency=quote,
    )


def scope_from_local(
    symbol: str,
    market_type: str,
    *,
    start_at=None,
    end_at=None,
) -> QueryScope:
    return QueryScope(
        instrument=instrument_from_local(symbol, market_type),
        start_at=utc(start_at),
        end_at=utc(end_at),
    )
