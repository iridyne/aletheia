"""Tradable instrument definitions and timeframes (guide §3.2, §22.1)."""

# MVP tradable symbols (guide §3.2)
TRADABLE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Market types
MARKET_SPOT = "spot"
MARKET_USDT_FUTURES = "usdt_futures"

# Timeframes the agent may choose from (guide §22.1)
TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]

# Bitget granularity mapping for OHLCV requests
BITGET_GRANULARITY = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1day",
}


def tradable_symbols_from_config(cfg) -> list[str]:
    """Return the configured tradable symbols, falling back to MVP defaults."""
    return cfg.exchange.tradable_symbols or TRADABLE_SYMBOLS
