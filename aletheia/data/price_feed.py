"""Fast in-memory price cache for open-position symbols.

Refreshed every 30s via scheduler (batch CCXT fetch_tickers).
position_manager and pnl reads check this cache first, falling back to
SQLite market_snapshots only if the cache is stale or empty.

Design:
- Module-level dict + threading.Lock (no SQLite writes on the fast path).
- Staleness threshold: 90s (3x refresh interval). Beyond that, callers
  fall back to the 5-min SQLite snapshots.
- fail-open: if the refresh fails, stale entries remain until they expire
  and callers transparently fall back to SQLite.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.price_feed")

_price_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

_STALE_THRESHOLD_SEC = 90.0


def get_cached_price(symbol: str) -> float | None:
    """Return cached price if fresh (< 90s old), else None."""
    from aletheia.data.symbols import to_canonical

    key = to_canonical(symbol)
    with _cache_lock:
        entry = _price_cache.get(key)
        if not entry:
            return None
        age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
        if age > _STALE_THRESHOLD_SEC:
            return None
        return entry["price"]


def get_watched_symbols() -> list[str]:
    """Return canonical symbols for all currently open positions."""
    try:
        from aletheia.storage.repositories import list_positions
        from aletheia.data.symbols import to_canonical

        positions = list_positions(status="open")
        seen: set[str] = set()
        result: list[str] = []
        for p in positions:
            sym = to_canonical(p.get("symbol", ""))
            if sym and sym not in seen:
                seen.add(sym)
                result.append(sym)
        return result
    except Exception as e:
        logger.warning("price_feed: failed to get watched symbols: %s", e)
        return []


async def refresh_watched_prices() -> dict:
    """Batch-fetch tickers for open-position symbols and update in-memory cache.

    Uses a fresh CCXTProvider per call (CCXT binds aiohttp to the creating loop).
    Single network round-trip where exchange supports batch fetch_tickers.
    Never raises — errors are logged and cache entries left stale.
    """
    symbols = get_watched_symbols()
    if not symbols:
        return {"refreshed": 0, "symbols": [], "errors": []}

    refreshed: list[str] = []
    errors: list[str] = []
    now = datetime.now(timezone.utc)

    try:
        from aletheia.data.ccxt_provider import get_market_data_provider
        from aletheia.data.symbols import InstrumentId, MARKET_SPOT
        from aletheia.config.loader import load_config

        cfg = load_config()
        exchange = cfg.market_data.ccxt_exchange or "binance"
        provider = get_market_data_provider()

        try:
            for sym in symbols:
                try:
                    iid = InstrumentId.from_parts(exchange, sym, MARKET_SPOT)
                    ticker = await provider.get_ticker(iid)
                    if ticker and ticker.last_price and ticker.last_price > 0:
                        with _cache_lock:
                            _price_cache[sym] = {
                                "price": ticker.last_price,
                                "ts": now,
                            }
                        refreshed.append(sym)
                except Exception as e:
                    errors.append(f"{sym}: {e}")
        finally:
            await provider.close()
    except Exception as e:
        logger.error("price_feed: refresh failed globally: %s", e)
        errors.append(f"global: {e}")

    if refreshed:
        logger.debug("price_feed: refreshed %d symbols", len(refreshed))
    if errors:
        logger.warning("price_feed: %d errors: %s", len(errors), errors[:3])

    return {"refreshed": len(refreshed), "symbols": refreshed, "errors": errors}


def cache_stats() -> dict:
    """Return diagnostic info about the cache (for health/debug endpoints)."""
    now = datetime.now(timezone.utc)
    with _cache_lock:
        entries = len(_price_cache)
        if not _price_cache:
            return {"entries": 0, "oldest_sec": None, "newest_sec": None}
        ages = [(now - v["ts"]).total_seconds() for v in _price_cache.values()]
        return {
            "entries": entries,
            "oldest_sec": round(max(ages), 1),
            "newest_sec": round(min(ages), 1),
        }
