"""Fast in-memory price cache for open-position symbols + universe watchlist.

Refreshed every 30s via scheduler (batch CCXT fetch_tickers).
position_manager and pnl reads check this cache first, falling back to
SQLite market_snapshots only if the cache is stale or empty.

Design:
- Module-level dict + threading.Lock (no SQLite writes on the fast path).
- Staleness threshold: 90s (3x refresh interval). Beyond that, callers
  fall back to the 5-min SQLite snapshots.
- fail-open: if the refresh fails, stale entries remain until they expire
  and callers transparently fall back to SQLite.
- Universe members tick-gated: refreshed every 3rd tick (90s) to stay
  within the scheduler's 45s timeout budget at large symbol counts.
"""

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.price_feed")

_price_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()

_STALE_THRESHOLD_SEC = 90.0
_UNIVERSE_TICK_EVERY = 3  # universe members refresh every N ticks
_tick_counter = 0
_tick_lock = threading.Lock()


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


def get_cached_price_with_meta(symbol: str) -> dict | None:
    """Return full cache entry {price, ts, provider, volume_24h} if fresh, else None."""
    from aletheia.data.symbols import to_canonical

    key = to_canonical(symbol)
    with _cache_lock:
        entry = _price_cache.get(key)
        if not entry:
            return None
        age = (datetime.now(timezone.utc) - entry["ts"]).total_seconds()
        if age > _STALE_THRESHOLD_SEC:
            return None
        return dict(entry)


def get_watched_symbols() -> list[str]:
    """Return canonical symbols for open positions (always) + universe members (tick-gated)."""
    from aletheia.data.symbols import to_canonical

    seen: set[str] = set()
    result: list[str] = []

    # Always watch open position symbols
    try:
        from aletheia.storage.repositories import list_positions
        for p in list_positions(status="open"):
            sym = to_canonical(p.get("symbol", ""))
            if sym and sym not in seen:
                seen.add(sym)
                result.append(sym)
    except Exception as e:
        logger.warning("price_feed: failed to get position symbols: %s", e)

    # Universe members — tick-gated
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        if cfg.event_triggers.enabled and cfg.event_triggers.watch_universe_members:
            with _tick_lock:
                global _tick_counter
                _tick_counter += 1
                do_universe = (_tick_counter % _UNIVERSE_TICK_EVERY == 0)

            if do_universe:
                from aletheia.storage.repositories import get_active_universe_members
                members = get_active_universe_members(limit=50)
                for m in members:
                    sym = to_canonical(m.get("symbol", ""))
                    if sym and sym not in seen:
                        seen.add(sym)
                        result.append(sym)
    except Exception as e:
        logger.debug("price_feed: universe expansion failed: %s", e)

    return result


async def refresh_watched_prices() -> dict:
    """Fetch tickers for watched symbols (SERIALLY) and update in-memory cache.

    Uses a fresh CCXTProvider per call (CCXT binds aiohttp to the creating loop).
    NOTE: fetches one symbol at a time — one network round-trip each, NOT a batch
    fetch_tickers call (I1 docstring-honesty fix: the prior docstring claimed a
    single batch round-trip, which the per-symbol loop below never did). The
    watched set is small so serial is acceptable; switch to a provider batch call
    if it grows. Never raises — errors are logged and cache entries left stale.
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
                            prev_entry = _price_cache.get(sym, {})
                            prev_price = prev_entry.get("price")
                            volume_24h = getattr(ticker, "volume_24h", None) or prev_entry.get("volume_24h")
                            _price_cache[sym] = {
                                "price": ticker.last_price,
                                "ts": now,
                                "prev_price": prev_price,
                                "volume_24h": volume_24h,
                                "provider": getattr(ticker, "provider", None) or "unknown",
                            }

                        if prev_price and prev_price > 0 and cfg.event_triggers.enabled:
                            pct = (ticker.last_price - prev_price) / prev_price * 100
                            _check_event_triggers(sym, pct, volume_24h, prev_entry.get("volume_24h"), cfg)

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


def _check_event_triggers(
    symbol: str,
    price_change_pct: float,
    volume_24h: float | None,
    prev_volume_24h: float | None,
    cfg,
) -> None:
    """Fire notifications and on-demand strategy evaluation on price/volume events."""
    try:
        et = cfg.event_triggers
        abs_pct = abs(price_change_pct)
        direction = "+" if price_change_pct >= 0 else "-"

        # Price move notification
        if abs_pct >= et.price_move_pct_threshold:
            _notify(f"Price alert {symbol}: {direction}{abs_pct:.1f}% (30s)")

        # Volume surge notification
        if (volume_24h and prev_volume_24h and prev_volume_24h > 0
                and volume_24h > prev_volume_24h * et.volume_surge_multiplier):
            surge = volume_24h / prev_volume_24h
            _notify(f"Volume surge {symbol}: {surge:.1f}x 24h vol")

        # Trigger strategy designer on significant move
        if et.trigger_strategy_design and abs_pct >= et.strategy_design_min_move_pct:
            try:
                from aletheia.agent_runtime.scheduler import trigger_strategy_designer_on_demand
                trigger_strategy_designer_on_demand()
                logger.info("event_trigger: designer on-demand fired for %s (%.1f%%)", symbol, price_change_pct)
            except Exception as e:
                logger.debug("event_trigger: designer trigger failed: %s", e)

        # Wake PI Agent on any significant price/volume event
        if abs_pct >= et.price_move_pct_threshold or (
            volume_24h and prev_volume_24h and prev_volume_24h > 0
            and volume_24h > prev_volume_24h * et.volume_surge_multiplier
        ):
            try:
                from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                notify_pi_daemon(source=f"event_trigger:{symbol}")
            except Exception:
                pass

    except Exception as e:
        logger.debug("_check_event_triggers: %s", e)


def _notify(message: str) -> None:
    """Best-effort operator notification."""
    try:
        from aletheia.notifications import notify_operator
        notify_operator(message)
    except Exception:
        pass


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


# ---------------------------------------------------------------------------
# Semantic price accessors (Phase 3)
# ---------------------------------------------------------------------------

_PRIMARY_EXCHANGE_PROVIDERS = frozenset({"binance", "bybit", "bitget", "okx"})


def get_price_for_pnl(symbol: str) -> float | None:
    """Venue last-trade price for PnL calculation.

    Prefers CCXT-sourced cache entry. Falls back to any cached price (with
    warning log when from fallback source), then SQLite snapshot.
    """
    meta = get_cached_price_with_meta(symbol)
    if meta is not None:
        provider = meta.get("provider", "unknown")
        if provider not in _PRIMARY_EXCHANGE_PROVIDERS:
            logger.warning(
                "pnl price for %s sourced from fallback (%s) — may differ from venue",
                symbol, provider,
            )
        return meta["price"]
    from aletheia.trading.market_data import latest_snapshot
    snap = latest_snapshot(symbol)
    return float(snap["price"]) if snap else None


def get_price_for_risk(symbol: str) -> float | None:
    """Conservative price for risk checks.

    When the cache entry came from a fallback source, also checks the SQLite
    snapshot (5-min cadence, possibly CCXT-sourced). Returns whichever is more
    conservative (higher value — worst-case for position sizing).
    """
    meta = get_cached_price_with_meta(symbol)
    cache_price = meta["price"] if meta else None
    cache_provider = (meta.get("provider") or "unknown") if meta else "unknown"

    if cache_price is None:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot(symbol)
        return float(snap["price"]) if snap else None

    if cache_provider in _PRIMARY_EXCHANGE_PROVIDERS:
        return cache_price

    # Fallback source — cross-check with SQLite snapshot for conservatism.
    from aletheia.trading.market_data import latest_snapshot
    snap = latest_snapshot(symbol)
    snap_price = float(snap["price"]) if snap else None
    if snap_price is None:
        return cache_price
    return max(cache_price, snap_price)


def get_price_for_liquidation(symbol: str) -> float | None:
    """Exchange mark price for liquidation estimation.

    Currently delegates to get_price_for_pnl(). When exchange mark price
    endpoint becomes available (futures), this will use that instead.
    """
    return get_price_for_pnl(symbol)
