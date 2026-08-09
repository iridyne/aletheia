"""Process-local venue listing cache — canonical symbols currently active on each exchange.

Used as a pre-fetch whitelist so that market_data, price_feed, and equity_observer
only attempt to fetch instruments that are confirmed listed on their responsible venue.
This prevents repeated 404/unsupported-symbol errors for delisted tokens from
accumulating into high error counts and driving up DB write pressure.

Design:
- One frozenset[str] per venue, keyed by lowercase venue name.
- 6-hour TTL (internal constant — not exposed in Settings).
- Lazy async load on first call; synchronous invalidation on config change.
- fail-open: a None result (network failure, timeout) means "keep all instruments".
- xStock (Bitget ON/r-series) filter uses market.get("active") is not False
  instead of market.get("spot") is True, which correctly captures Bitget's
  tokenized equity spot tokens that ccxt does not mark with spot=True.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aletheia.data.symbols import InstrumentId

logger = logging.getLogger("aletheia.venue_listing_cache")

# Internal constant — raise when exchange listing cadence or memory budget changes.
_LISTING_TTL_SEC: float = 6 * 3600       # 6 hours
_LISTING_LOAD_TIMEOUT_SEC: float = 20.0  # per-venue load_markets wall-clock budget

# {venue: (frozenset[canonical_symbol], expire_monotonic)}
_CACHE: dict[str, tuple[frozenset[str], float]] = {}
_CACHE_LOCK = threading.Lock()

# Per-venue async locks so concurrent callers wait for one shared load instead of
# each spawning their own load_markets() call.
_LOAD_LOCKS: dict[str, asyncio.Lock] = {}
_LOAD_LOCKS_GUARD = threading.Lock()


def _async_lock_for(venue: str) -> asyncio.Lock:
    """Return (creating if needed) the per-venue asyncio.Lock for load serialisation."""
    with _LOAD_LOCKS_GUARD:
        if venue not in _LOAD_LOCKS:
            _LOAD_LOCKS[venue] = asyncio.Lock()
        return _LOAD_LOCKS[venue]


def invalidate_venue_listing(venue: str) -> None:
    """Immediately expire the cached listing for *venue*.

    Call this whenever the configured exchange changes so that the next
    data-collection cycle rebuilds the whitelist from a fresh load_markets().
    Safe to call from sync context (config save path).
    """
    key = venue.strip().lower()
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
    logger.debug("venue_listing_cache: invalidated %s", key)


def invalidate_all() -> None:
    """Expire all cached listings. Called on startup or full config reset."""
    with _CACHE_LOCK:
        _CACHE.clear()
    # Also drop locks for venues no longer needed so the lock dict doesn't
    # grow one permanent entry per unique venue name seen during the process
    # lifetime.
    with _LOAD_LOCKS_GUARD:
        _LOAD_LOCKS.clear()
    logger.debug("venue_listing_cache: all entries invalidated")


def _cached(venue: str) -> frozenset[str] | None:
    """Return the cached listing if still fresh, else None."""
    key = venue.strip().lower()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if entry is None:
        return None
    listing, expires_at = entry
    if time.monotonic() >= expires_at:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    return listing


def _store(venue: str, listing: frozenset[str]) -> None:
    key = venue.strip().lower()
    expires_at = time.monotonic() + _LISTING_TTL_SEC
    with _CACHE_LOCK:
        _CACHE[key] = (listing, expires_at)


async def _load_listing(venue: str) -> frozenset[str] | None:
    """Fetch active canonical symbols from *venue* via CCXT load_markets().

    Returns None on any network/timeout/parse failure (fail-open).
    Uses market.get("active") is not False as the liveness gate — this correctly
    captures Bitget tokenized equity tokens (ON-series, r-series) that ccxt does
    not mark with spot=True in their market dict.
    """
    from aletheia.data.symbols import to_canonical

    async def _fetch() -> frozenset[str]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider(
            exchange=venue,
            native_ccxt=True,
            fallback_exchange=venue,
        )
        try:
            exchange = provider._ensure_primary()
            # Don't set defaultType globally — load all market types and
            # let the active-flag filter do the work.
            markets = await exchange.load_markets()
            if not isinstance(markets, dict) or not markets:
                raise ValueError(f"load_markets returned empty/invalid result for {venue}")
            listed = frozenset(
                to_canonical(str(sym))
                for sym, info in markets.items()
                # ccxt market dicts use three-value active: True/False/None.
                # We want symbols that are NOT explicitly deactivated (False);
                # None (unknown) and True both count as listed.
                # != False is intentional: distinguishes False from None.
                if isinstance(info, dict) and info.get("active") != False  # noqa: E712
            )
            if not listed:
                raise ValueError(f"no active markets found for {venue}")
            return listed
        finally:
            await provider.close()

    try:
        task = asyncio.ensure_future(_fetch())
        done, _ = await asyncio.wait({task}, timeout=_LISTING_LOAD_TIMEOUT_SEC)
        if task not in done:
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            task.cancel()
            logger.warning(
                "venue_listing_cache: load_markets timed out for %s — fail-open", venue
            )
            return None
        return task.result()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — fail-open on any network/parse error
        logger.warning(
            "venue_listing_cache: failed to load listing for %s (%s) — fail-open",
            venue, exc,
        )
        return None


async def get_venue_listing(venue: str) -> frozenset[str] | None:
    """Return the active canonical symbol set for *venue*, or None if indeterminate.

    TTL: 6 hours. Expired or invalidated entries trigger a fresh load_markets()
    call. Concurrent callers share one load per venue (async lock).

    Returns None on network failure / timeout — callers must treat None as
    "keep all instruments" (fail-open) rather than "remove everything".
    """
    key = venue.strip().lower()
    cached = _cached(key)
    if cached is not None:
        return cached

    lock = _async_lock_for(key)
    async with lock:
        # Re-check after acquiring — another coroutine may have populated it.
        cached = _cached(key)
        if cached is not None:
            return cached

        listing = await _load_listing(key)
        if listing is not None:
            _store(key, listing)
            logger.info(
                "venue_listing_cache: loaded %d active symbols for %s (TTL 6h)",
                len(listing), key,
            )
        return listing


def get_cached_listing(venue: str) -> frozenset[str] | None:
    """Synchronous read of the in-memory cache only — no network call.

    Returns None when the cache is cold or expired.  Use this from sync
    callers (e.g. price_feed) that cannot await get_venue_listing().
    The async path (collect_market_snapshots) populates the cache on
    its regular 5-minute cadence, so sync callers benefit automatically.
    """
    return _cached(venue.strip().lower())


def filter_by_listing(
    instruments: list["InstrumentId"],
    *,
    listing: frozenset[str] | None,
    venue: str,
) -> list["InstrumentId"]:
    """Return only instruments whose symbol appears in *listing*.

    When listing is None (indeterminate), all instruments are returned unchanged
    (fail-open). Instruments from a different venue than *venue* are never dropped.

    Logs a single warning per call when symbols are removed so operators can
    see which delisted tokens were filtered without per-symbol log spam.
    """
    if listing is None:
        return instruments

    kept: list["InstrumentId"] = []
    dropped: list[str] = []
    for inst in instruments:
        # Only filter instruments that belong to the target venue.
        if inst.exchange.lower() != venue.lower():
            kept.append(inst)
            continue
        if inst.symbol in listing:
            kept.append(inst)
        else:
            dropped.append(inst.symbol)

    if dropped:
        logger.warning(
            "venue_listing_cache: filtered %d unlisted symbols from %s: %s",
            len(dropped), venue, dropped[:10],
        )
    return kept
