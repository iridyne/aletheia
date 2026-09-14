"""CCXT registry-catalog decoding; no configuration policy or persistence."""

from aletheia.data.market_catalog import MarketCatalogEntry

_LEVERAGED_SUFFIXES = ("3L", "3S", "5L", "5S", "2L", "2S", "UP", "DOWN", "BULL", "BEAR")
_EXCLUDED_BASES = {"BTTC", "LUNC", "LUNA", "UST", "FTT"}


def _precision_to_decimals(p) -> int | None:
    """CCXT market precision → integer decimal places.

    CCXT reports precision in one of two modes: DECIMAL_PLACES (an int like 3) or
    TICK_SIZE (a float like 0.001). int(0.001) == 0 silently destroyed tick-size
    precision, so downstream orders rounded to whole units and got rejected.
    Convert a sub-1 tick size to its decimal-place count.
    """
    if p is None:
        return None
    try:
        if isinstance(p, float) and 0 < p < 1:
            import math
            return max(0, int(round(-math.log10(p))))
        return int(p)
    except (ValueError, OverflowError):
        return None


def _is_excluded(symbol_info: dict) -> bool:
    """Filter out unsupported asset classes, leveraged tokens, and test symbols."""
    base = (symbol_info.get("base") or "").upper()
    symbol_id = (symbol_info.get("id") or "").upper()

    if any(base.endswith(s) for s in _LEVERAGED_SUFFIXES):
        return True
    if base in _EXCLUDED_BASES:
        return True
    # US-15: "_" excludes exchange-native underscore ids (e.g. dated futures
    # "BTC_USDT_240628"). On venues whose SPOT ids use "_" (e.g. KuCoin "BTC_USDT")
    # this would over-exclude everything — revisit if adding such a venue. Current
    # venues (OKX/Bitget) use "-"/no separator, so this is safe today.
    if "TEST" in symbol_id or "_" in symbol_id:
        return True
    return False


async def load_registry_catalog(venue: str, fallback_venue: str, *, decode_market) -> list[MarketCatalogEntry]:
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider(
        exchange=venue, native_ccxt=True, fallback_exchange=fallback_venue,
    )
    try:
        exchange = provider._ensure_primary()
        await exchange.load_markets()
        markets = exchange.markets
        if not isinstance(markets, dict) or not markets:
            raise RuntimeError("universe_market_catalog_unavailable")
        entries = []
        for symbol_key, info in markets.items():
            if not info.get("active", True):
                continue
            quote = (info.get("quote") or "").upper()
            base = (info.get("base") or "").upper()
            if quote != "USDT" or not base or info.get("option"):
                continue
            market_type = "perp" if info.get("swap") or info.get("future") else "spot"
            background, venue_excluded = decode_market(
                symbol=str(info.get("symbol") or symbol_key), venue=venue,
                market_type=market_type, market_info=info,
            )
            limits = info.get("limits") or {}
            precision = info.get("precision") or {}
            entries.append(MarketCatalogEntry(
                symbol=f"{base}/{quote}", base=base, quote=quote,
                market_type=market_type, background=background,
                excluded_crypto=venue_excluded or _is_excluded(info),
                min_notional=(limits.get("cost") or {}).get("min"),
                price_precision=_precision_to_decimals(precision.get("price")),
                amount_precision=_precision_to_decimals(precision.get("amount")),
            ))
        return entries
    finally:
        await provider.close()
