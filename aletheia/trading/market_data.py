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
from aletheia.trading.instruments import observed_instruments_from_config, xstocks_instruments_from_config
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

    instruments = observed_instruments_from_config(cfg)
    xstocks = xstocks_instruments_from_config(cfg)

    from aletheia.data.ccxt_provider import CCXTProvider, get_market_data_provider
    provider = get_market_data_provider()

    collected = []
    errors = []

    async def _fetch_and_store(instrument, prov):
        ticker = await prov.get_ticker(instrument)
        snap = MarketSnapshot(
            symbol=instrument.symbol,
            market_type=instrument.market_type,
            exchange=instrument.exchange,
            price=ticker.last_price,
            volume_24h=ticker.volume_24h,
            change_pct_24h=ticker.change_pct_24h,
            high_24h=ticker.high_24h,
            low_24h=ticker.low_24h,
            provider=ticker.provider or None,
        )
        insert_market_snapshot(snap)
        append_jsonl("market_snapshots.jsonl", snap.model_dump())
        return str(instrument)

    try:
        for instrument in instruments:
            try:
                collected.append(await _fetch_and_store(instrument, provider))
            except Exception as e:
                errors.append(f"{instrument}: {e}")
                logger.warning("Failed to fetch ticker %s: %s", instrument, e)
    finally:
        await provider.close()

    # xStocks: separate Bitget provider (no API key needed, ON-series tokens).
    # Deliberate exception: NOT routed through get_market_data_provider(). ON-series
    # xStocks only list on Bitget, so both primary AND fallback must be bitget — the
    # factory would force the config fallback (bybit), which doesn't list these tokens.
    if xstocks:
        xprovider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
        try:
            for instrument in xstocks:
                try:
                    collected.append(await _fetch_and_store(instrument, xprovider))
                except Exception as e:
                    errors.append(f"{instrument}: {e}")
                    logger.warning("Failed to fetch xStocks ticker %s: %s", instrument, e)
        finally:
            await xprovider.close()

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
