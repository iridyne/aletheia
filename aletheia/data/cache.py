"""In-memory TTL cache for OHLCV candle sets — a process-global singleton.

Keyed by ``(exchange, symbol, market_type, timeframe, limit)``. TTL is read from
``market_data.ohlcv_cache_ttl_min`` (default 10 min) at cache-creation time.

Time is read via a module-level ``_now()`` indirection so tests can patch it; we
deliberately do NOT use ``Math.random``-style entropy. This cache is best-effort:
on any config-read failure it falls back to the 10-minute default rather than
raising, because a market-data fetch must never crash on a cold cache.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

_DEFAULT_TTL_MIN = 10
_DEFAULT_MAX_ENTRIES = 256


def _now() -> float:
    return time.monotonic()


def _ttl_seconds() -> float:
    try:
        from aletheia.config.loader import load_config

        return float(load_config().market_data.ohlcv_cache_ttl_min) * 60.0
    except Exception:
        return _DEFAULT_TTL_MIN * 60.0


class OHLCVCache:
    """Thread-safe TTL cache. Values are opaque (lists of Candle objects)."""

    def __init__(self, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._store: OrderedDict[tuple, tuple[float, object]] = OrderedDict()
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0
        self._expired = 0
        self._evicted = 0

        self._lock = threading.Lock()

    @staticmethod
    def _key(
        symbol: str, timeframe: str, limit: int,
        market_type: str = "spot", exchange: str = "",
    ) -> tuple:
        return (exchange, symbol, market_type, timeframe, limit)

    def get(
        self, symbol: str, timeframe: str, limit: int,
        market_type: str = "spot", exchange: str = "",
    ):
        """Return the cached value or None if absent / expired."""
        key = self._key(symbol, timeframe, limit, market_type, exchange)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            stored_at, value = entry
            if _now() - stored_at > _ttl_seconds():
                self._store.pop(key, None)
                self._misses += 1
                self._expired += 1
                return None
            self._hits += 1
            self._store.move_to_end(key)
            return value

    def set(
        self, symbol: str, timeframe: str, limit: int, value,
        market_type: str = "spot", exchange: str = "",
    ) -> None:
        key = self._key(symbol, timeframe, limit, market_type, exchange)
        with self._lock:
            self._store[key] = (_now(), value)
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                self._store.popitem(last=False)
                self._evicted += 1

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._store),
                "max_entries": self._max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "expired": self._expired,
                "evicted": self._evicted,
            }


    def invalidate(self) -> None:
        """Clear all entries (used by tests to reset shared state)."""
        with self._lock:
            self._store.clear()


_cache: OHLCVCache | None = None
_cache_lock = threading.Lock()


def get_cache() -> OHLCVCache:
    """Return the process-global OHLCV cache singleton."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = OHLCVCache()
    return _cache
