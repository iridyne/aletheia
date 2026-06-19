"""Market-data collection loop — fetches tickers and stores snapshots.

Runs without credentials (public market endpoints via CCXT).
Degrades gracefully if the exchange is unreachable.
"""

import logging

from aletheia.config.loader import load_config
from aletheia.data.symbols import InstrumentId, MARKET_SPOT, to_canonical, to_bitget
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot
from aletheia.trading.instruments import observed_instruments_from_config, xstocks_instruments_from_config
from aletheia.files.jsonl import append_jsonl

logger = logging.getLogger("aletheia.market_data")

_MAX_DYNAMIC_INSTRUMENTS = 60


def _build_dynamic_instruments(cfg) -> list[InstrumentId]:
    """Build deduplicated instrument list from config + universe + open positions.

    Sources (first occurrence wins for dedup):
      1. Config core symbols (always included, even if universe is empty)
      2. Active universe members (dynamic — the main altcoin source)
      3. Open position symbols (so uPnL stays fresh after symbol leaves universe)

    Total capped at _MAX_DYNAMIC_INSTRUMENTS to keep collection within 90s timeout.
    """
    exchange = cfg.market_data.ccxt_exchange or "binance"

    seen: dict[str, InstrumentId] = {}
    for iid in observed_instruments_from_config(cfg):
        seen[iid.symbol] = iid
    n_config = len(seen)

    try:
        from aletheia.storage import repositories as repo
        members = repo.get_active_universe_members(limit=cfg.universe.max_candidates)
        for m in members:
            sym = m.get("symbol")
            if not sym or sym in seen:
                continue
            try:
                seen[sym] = InstrumentId.from_parts(exchange, sym, MARKET_SPOT)
            except (ValueError, Exception):
                pass
    except Exception:
        pass

    try:
        from aletheia.storage import repositories as repo
        for pos in repo.list_positions(status="open"):
            sym = pos.get("symbol")
            if not sym or sym in seen:
                continue
            try:
                seen[sym] = InstrumentId.from_parts(exchange, sym, MARKET_SPOT)
            except (ValueError, Exception):
                pass
    except Exception:
        pass

    instruments = list(seen.values())[:_MAX_DYNAMIC_INSTRUMENTS]
    logger.debug("market_snapshots: collecting %d instruments (config=%d dynamic=%d)",
                 len(instruments), n_config, len(instruments) - n_config)
    return instruments


async def collect_market_snapshots() -> dict:
    """Fetch a ticker for each observed instrument and persist a market snapshot.

    Dynamically includes universe members and open-position symbols so that
    altcoin candidates have fresh price data for paper trading.
    """
    # Migrations run once at gateway startup (aletheia/server/app.py:44); scheduler
    # jobs MUST NOT call run_migrations() — concurrent executescript() across threads
    # was the dominant `database is locked` source.
    cfg = load_config()

    instruments = _build_dynamic_instruments(cfg)
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
