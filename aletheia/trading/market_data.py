"""Market-data collection loop — fetches tickers and stores snapshots.

Runs without credentials (public market endpoints via CCXT).
Degrades gracefully if the exchange is unreachable.
"""

import logging

from aletheia.config.loader import load_config
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot
from aletheia.data.symbols import to_canonical, to_bitget
from aletheia.trading.instruments import observed_instruments_from_config
from aletheia.files.jsonl import append_jsonl

logger = logging.getLogger("aletheia.market_data")


async def collect_market_snapshots() -> dict:
    """Fetch a ticker for each observed instrument and persist a market snapshot.

    Uses observed_instruments_from_config() which returns InstrumentId objects —
    each uniquely identifies exchange + symbol + market_type so snapshots from
    different exchanges for the same symbol can coexist in the DB.
    """
    run_migrations()
    cfg = load_config()

    md_cfg = cfg.market_data
    instruments = observed_instruments_from_config(cfg)

    from aletheia.data.ccxt_provider import CCXTProvider
    provider = CCXTProvider(
        exchange=md_cfg.ccxt_exchange,
        fallback_exchange=md_cfg.ccxt_fallback,
    )

    collected = []
    errors = []

    try:
        for instrument in instruments:
            try:
                ticker = await provider.get_ticker(instrument)
                snap = MarketSnapshot(
                    symbol=instrument.symbol,        # canonical: BTC/USDT
                    market_type=instrument.market_type,
                    exchange=instrument.exchange,    # e.g. "binance"
                    price=ticker.last_price,
                    volume_24h=ticker.volume_24h,
                    change_pct_24h=ticker.change_pct_24h,
                    high_24h=ticker.high_24h,
                    low_24h=ticker.low_24h,
                    provider=ticker.provider or None,
                )
                insert_market_snapshot(snap)
                append_jsonl("market_snapshots.jsonl", snap.model_dump())
                collected.append(str(instrument))
            except Exception as e:
                errors.append(f"{instrument}: {e}")
                logger.warning("Failed to fetch ticker %s: %s", instrument, e)
    finally:
        await provider.close()

    return {"collected": collected, "errors": errors}


def latest_snapshot(symbol: str) -> dict | None:
    """Return the most recent stored snapshot for a symbol.

    Accepts any symbol format (canonical, bitget, yfinance).
    Tries canonical first, then bitget format for backwards compatibility
    with snapshots written before the CCXT migration.
    """
    from aletheia.storage.repositories import list_market_snapshots
    canon = to_canonical(symbol)
    rows = list_market_snapshots(symbol=canon, limit=1)
    if rows:
        return rows[0]
    # Fallback: old snapshots stored with Bitget format (BTCUSDT)
    bitget_sym = to_bitget(canon)
    if bitget_sym != canon:
        rows = list_market_snapshots(symbol=bitget_sym, limit=1)
        return rows[0] if rows else None
    return None
