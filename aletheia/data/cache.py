"""In-memory TTL cache for OHLCV candle sets — a process-global singleton.

Keyed by the composite ``(symbol, timeframe, limit)`` tuple. TTL is read from
``market_data.ohlcv_cache_ttl_min`` (default 10 min) at cache-creation time.

Time is read via a module-level ``_now()`` indirection so tests can patch it; we
deliberately do NOT use ``Math.random``-style entropy. This cache is best-effort:
on any config-read failure it falls back to the 10-minute default rather than
raising, because a market-data fetch must never crash on a cold cache.
"""

from __future__ import annotations

import threading
import time

_DEFAULT_TTL_MIN = 10


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

    def __init__(self) -> None:
        self._store: dict[tuple, tuple[float, object]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(symbol: str, timeframe: str, limit: int) -> tuple:
        return (symbol, timeframe, limit)

    def get(self, symbol: str, timeframe: str, limit: int):
        """Return the cached value or None if absent / expired."""
        key = self._key(symbol, timeframe, limit)
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            stored_at, value = entry
            if _now() - stored_at > _ttl_seconds():
                # Expired — drop it so the store doesn't grow unbounded.
                self._store.pop(key, None)
                return None
            return value

    def set(self, symbol: str, timeframe: str, limit: int, value) -> None:
        key = self._key(symbol, timeframe, limit)
        with self._lock:
            self._store[key] = (_now(), value)

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
