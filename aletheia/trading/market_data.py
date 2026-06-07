"""Market-data collection loop — fetches tickers from Bitget and stores snapshots.

Runs without credentials (public market endpoints). Degrades gracefully if the
exchange is unreachable so the scheduler keeps running.
"""

import logging

from aletheia.config.loader import load_config
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot
from aletheia.trading.instruments import tradable_symbols_from_config
from aletheia.files.jsonl import append_jsonl

logger = logging.getLogger("aletheia.market_data")


async def collect_market_snapshots() -> dict:
    """Fetch a ticker for each tradable symbol and persist a market snapshot."""
    run_migrations()
    cfg = load_config()
    from aletheia.exchanges.bitget.client import BitgetClient
    client = BitgetClient(mode=cfg.exchange.mode)

    symbols = tradable_symbols_from_config(cfg)
    collected = []
    errors = []

    market_types = ["spot", "usdt_futures"]
    for symbol in symbols:
        for market_type in market_types:
            try:
                ticker = await client.get_ticker(symbol, market_type=market_type)
                snap = MarketSnapshot(
                    symbol=symbol,
                    market_type=market_type,
                    price=ticker.last_price,
                    volume_24h=ticker.volume_24h,
                    change_pct_24h=ticker.change_pct_24h,
                )
                insert_market_snapshot(snap)
                append_jsonl("market_snapshots.jsonl", snap.model_dump())
                collected.append(f"{symbol}:{market_type}")
            except Exception as e:
                errors.append(f"{symbol}:{market_type}: {e}")
                logger.warning("Failed to fetch %s/%s: %s", symbol, market_type, e)

    return {"collected": collected, "errors": errors}


def latest_snapshot(symbol: str) -> dict | None:
    """Return the most recent stored snapshot for a symbol."""
    from aletheia.storage.repositories import list_market_snapshots
    rows = list_market_snapshots(symbol=symbol, limit=1)
    return rows[0] if rows else None
