"""Tradable instrument definitions and timeframes (guide §3.2, §22.1)."""

from aletheia.data.symbols import InstrumentId, to_canonical, MARKET_SPOT

# MVP tradable symbols — Bitget execution format (used by risk engine + order router)
TRADABLE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Market types
MARKET_USDT_FUTURES = "usdt_futures"

# Canonical timeframes (used by data layer and agent reasoning)
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

# Bitget-specific granularity strings — execution-context only.
# New code should use canonical timeframes ('1h', '4h', '1d') via data.symbols.
BITGET_GRANULARITY = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


def tradable_symbols_from_config(cfg) -> list[str]:
    """Return the configured tradable symbols (Bitget format), falling back to defaults."""
    return cfg.exchange.tradable_symbols or TRADABLE_SYMBOLS


def observed_symbols_from_config(cfg) -> list[str]:
    """Return the market data observation list (canonical BTC/USDT format).

    If observed_instruments is configured, derives symbol list from it.
    Otherwise returns observed_symbols directly (legacy path).
    """
    if cfg.market_data.observed_instruments:
        seen: dict[str, None] = {}
        for s in cfg.market_data.observed_instruments:
            try:
                iid = InstrumentId.parse(s)
                seen[iid.symbol] = None
            except (ValueError, Exception):
                pass
        return list(seen)
    return cfg.market_data.observed_symbols


def xstocks_instruments_from_config(cfg) -> list[InstrumentId]:
    """Return Bitget tokenized xStocks as InstrumentId list.

    These use exchange="bitget" so CCXTProvider fetches from Bitget (the only
    exchange listing ON-series tokens like AAPLON/USDT, TSLAON/USDT).
    asset_class is handled by the caller — always "equity", never traded.
    """
    symbols = getattr(cfg.market_data, "observed_xstocks", []) or []
    result = []
    for s in symbols:
        try:
            result.append(InstrumentId.from_parts("bitget", s, MARKET_SPOT))
        except (ValueError, Exception):
            pass
    return result


def observed_instruments_from_config(cfg) -> list[InstrumentId]:
    """Return the full InstrumentId list for market data collection.

    If observed_instruments is configured (non-empty), parses and returns them.
    Otherwise derives from observed_symbols + ccxt_exchange + "spot" (legacy path).

    Invalid entries are silently skipped — callers get a best-effort list.
    """
    if cfg.market_data.observed_instruments:
        result = []
        for s in cfg.market_data.observed_instruments:
            try:
                result.append(InstrumentId.parse(s))
            except (ValueError, Exception):
                pass
        return result
    # Legacy path: symbols + exchange from config, all spot
    exchange = cfg.market_data.ccxt_exchange or "binance"
    return [
        InstrumentId.from_parts(exchange, sym, MARKET_SPOT)
        for sym in cfg.market_data.observed_symbols
    ]
