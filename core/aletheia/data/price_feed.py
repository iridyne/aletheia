"""Fast in-memory price cache for positions, pending candidates, and Universe.

Refreshed every 30s via scheduler (batch CCXT fetch_tickers).
position_manager and pnl reads check this cache first, falling back to
SQLite market_snapshots only if the cache is stale or empty.

Design:
- Legacy symbol-only cache remains the display/PnL surface. Protective closes
  use a separate exact ``(exchange, canonical symbol, physical market type)``
  cache.
- Module-level dicts + threading.Lock (no SQLite writes on the fast path).
- Staleness threshold: 90s (3x refresh interval). Beyond that, callers
  fall back to the 5-min SQLite snapshots.
- Display/PnL remains fail-open to SQLite. Protective close pricing is fail-closed:
  its typed SQLite fallback must be no older than 120 seconds.
- Pending candidates refresh every tick; ordinary Universe members are tick-gated
  every 3rd tick (nominally 90s) to stay
  within the scheduler's 45s timeout budget at large symbol counts.
"""

from aletheia.config.exchange_alignment import market_data_exchange
import logging
import math
import threading
from datetime import datetime, timezone

logger = logging.getLogger("aletheia.price_feed")

_price_cache: dict[str, dict] = {}
# Protective closes need exact symbol + market identity.  Keep this separate from
# the legacy symbol-only cache consumed by dashboard/display PnL so that adding
# lifecycle safety does not change that accessor's existing behavior.
_instrument_price_cache: dict[tuple[str, str, str], dict] = {}
_cache_lock = threading.Lock()

# Flipped once `refresh_watched_prices()` has run in this process. Read-only
# CLI processes never call that function, so their empty `_instrument_price_cache`
# is not authoritative evidence that the Gateway lacks a fresh mark.
_price_cache_owner_process: bool = False
# Exact instruments this process has actually attempted to refresh (success OR
# failure). Cache authority is per-instrument, not per-process: a candidate
# admitted mid-cycle is still unobservable until its first refresh attempt.
_instrument_refresh_attempted: set[tuple[str, str, str]] = set()


def process_owns_price_cache() -> bool:
    """True in the process that populates the exact fast mark cache."""
    return _price_cache_owner_process


def instrument_refresh_attempted(
    exchange: str, symbol: str, market_type: str,
) -> bool:
    """True after this exact instrument has been through one refresh attempt."""
    return (exchange, symbol, market_type) in _instrument_refresh_attempted

_STALE_THRESHOLD_SEC = 90.0
# Universe alerts compare adjacent sampling rounds within a declared four-cycle
# event horizon.  This is a semantic boundary, not a scheduler liveness bound:
# longer gaps deliberately begin a new segment even if the scheduler stayed up.
_UNIVERSE_EVENT_CONTINUITY_MAX_GAP_SEC = 360.0
_PROTECTIVE_DB_MAX_AGE_SEC = 120.0
_UNIVERSE_TICK_EVERY = 3  # universe members refresh every N ticks
_tick_counter = 0
_universe_round = 0
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


def _protective_market_type(market_type: str | None) -> str | None:
    """Map Position vocabulary to the exact physical cache/snapshot market."""
    if market_type == "usdt_futures":
        return "perp"
    if market_type in {"spot", "perp", "futures"}:
        return market_type
    return None


def protective_price_venue(execution_mode: str | None) -> str | None:
    """Resolve the venue encoded by a supported external execution mode."""
    from aletheia.config.exchanges import execution_mode_venue

    return execution_mode_venue(execution_mode)


def _valid_fresh_mark(
    entry: dict | None,
    *,
    max_age_sec: float,
    not_before: datetime | None,
    source: str,
    venue: str,
    symbol: str,
    market_type: str,
) -> dict | None:
    if not entry:
        return None
    try:
        price = float(entry["price"])
        ts = entry["ts"]
        age = (datetime.now(timezone.utc) - ts).total_seconds()
    except (KeyError, TypeError, ValueError):
        return None
    if (
        not math.isfinite(price)
        or price <= 0
        or age < 0
        or age > max_age_sec
        or (not_before is not None and ts < not_before)
    ):
        return None
    return {
        "price": price,
        "observed_at": ts.isoformat(),
        "age_seconds": round(age, 3),
        "source": source,
        "venue": venue,
        "symbol": symbol,
        "market_type": market_type,
    }


def get_fresh_mark_for_protective_close(
    symbol: str,
    market_type: str,
    *,
    venue: str,
    not_before: datetime | None = None,
) -> dict | None:
    """Return a fresh exact mark with bounded, non-secret provenance.

    ``not_before`` adds a causal lower bound for completed-bar exits. Ordinary
    callers omit it and retain the existing maximum-age behavior.
    """
    from aletheia.data.symbols import to_canonical

    canonical = to_canonical(symbol)
    physical_market = _protective_market_type(market_type)
    exact_venue = str(venue or "").strip().lower()
    if physical_market is None or not exact_venue:
        return None

    with _cache_lock:
        cache_entry = _instrument_price_cache.get(
            (exact_venue, canonical, physical_market)
        )
        cached = _valid_fresh_mark(
            cache_entry,
            max_age_sec=_STALE_THRESHOLD_SEC,
            not_before=not_before,
            source="instrument_price_cache",
            venue=exact_venue,
            symbol=canonical,
            market_type=physical_market,
        )
    if cached is not None:
        return cached

    row = get_latest_exact_market_snapshot(canonical, market_type, exact_venue)
    if row is None:
        return None
    raw_ts = row.get("snapshot_at")
    if not raw_ts:
        return None
    try:
        observed_at = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return _valid_fresh_mark(
        {"price": row.get("price"), "ts": observed_at},
        max_age_sec=_PROTECTIVE_DB_MAX_AGE_SEC,
        not_before=not_before,
        source="market_snapshot",
        venue=exact_venue,
        symbol=canonical,
        market_type=physical_market,
    )


def get_latest_exact_market_snapshot(
    symbol: str,
    market_type: str,
    venue: str,
) -> dict | None:
    """Return the newest stored observation for one exact physical instrument."""
    from aletheia.data.symbols import to_bitget, to_canonical
    from aletheia.storage.repositories import list_market_snapshots

    canonical = to_canonical(symbol)
    physical_market = _protective_market_type(market_type)
    exact_venue = str(venue or "").strip().lower()
    if physical_market is None or not exact_venue:
        return None
    symbols = [canonical]
    legacy_symbol = to_bitget(canonical)
    if legacy_symbol != canonical:
        symbols.append(legacy_symbol)
    markets = [physical_market]
    if market_type == "usdt_futures":
        markets.append("usdt_futures")
    rows = [
        row
        for stored_symbol in symbols
        for stored_market in markets
        for row in list_market_snapshots(
            symbol=stored_symbol,
            exchange=exact_venue,
            market_type=stored_market,
            limit=1,
        )
    ]
    return max(rows, key=lambda item: str(item.get("snapshot_at") or "")) if rows else None


def get_fresh_price_for_protective_close(
    symbol: str,
    market_type: str,
    *,
    venue: str,
) -> float | None:
    """Return a fresh, exact-instrument mark suitable for creating a close Fill.

    The in-memory mark must be younger than the normal 90-second cache limit.
    The SQLite fallback is deliberately stricter than the display/PnL accessor:
    it never falls back across venue or spot/perp and rejects rows older than
    120 seconds.
    """
    mark = get_fresh_mark_for_protective_close(
        symbol,
        market_type,
        venue=venue,
    )
    return float(mark["price"]) if mark is not None else None


def get_watched_instruments(
    *,
    _always_on_identities: set[tuple[str, str, str]] | None = None,
    _universe_round_by_identity: dict[tuple[str, str, str], int] | None = None,
):
    """Return exact instruments for positions, pending candidates, and Universe."""
    from aletheia.config.loader import load_config
    from aletheia.data.symbols import InstrumentId, MARKET_PERP, MARKET_SPOT

    seen: set[tuple[str, str, str]] = set()
    always_on: set[tuple[str, str, str]] = set()
    universe_rounds: dict[tuple[str, str, str], int] = {}
    always_on_sources_complete = True
    result: list[InstrumentId] = []
    cfg = load_config()
    default_exchange = market_data_exchange(cfg)

    def append_instrument(
        raw_symbol: str,
        market_type: str | None,
        venue: str | None,
        *,
        is_always_on: bool = False,
        universe_round: int | None = None,
    ) -> None:
        if not raw_symbol:
            return
        normalized_market = MARKET_PERP if market_type == "usdt_futures" else market_type
        try:
            instrument = InstrumentId.from_parts(
                venue or default_exchange,
                raw_symbol,
                normalized_market or MARKET_SPOT,
            )
        except ValueError:
            return
        # Protective lifecycle pricing is exact by symbol + physical market, so
        # same-symbol spot and perp Positions must both remain watched.
        identity = (
            instrument.exchange, instrument.symbol, instrument.market_type,
        )
        if is_always_on:
            always_on.add(identity)
        if universe_round is not None:
            universe_rounds[identity] = universe_round
        if identity not in seen:
            seen.add(identity)
            result.append(instrument)

    # Always watch open position symbols
    try:
        from aletheia.storage.repositories import (
            list_all_open_positions,
        )
        for p in list_all_open_positions():
            position_venue = protective_price_venue(p.get('execution_mode'))
            if position_venue is None:
                logger.warning(
                    "price_feed: unsupported position execution mode id=%s mode=%s",
                    p.get("id"),
                    p.get("execution_mode"),
                )
                continue
            append_instrument(
                p.get("symbol", ""),
                p.get("market_type"),
                position_venue,
                is_always_on=True,
            )
    except Exception as e:
        always_on_sources_complete = False
        logger.warning("price_feed: failed to get position symbols: %s", e)

    # Current planning demand is high priority, independent of the Universe tick gate.
    try:
        from aletheia.storage.repositories import list_candidate_planning_refresh_assets

        for asset in list_candidate_planning_refresh_assets():
            append_instrument(
                asset["symbol"], asset["market_type"], asset["venue"],
                is_always_on=True,
            )
    except Exception as e:
        always_on_sources_complete = False
        logger.warning("price_feed: planning expansion failed: %s", type(e).__name__)

    # Universe members — tick-gated
    try:
        if cfg.event_triggers.enabled and cfg.event_triggers.watch_universe_members:
            with _tick_lock:
                global _tick_counter, _universe_round
                _tick_counter += 1
                do_universe = (_tick_counter % _UNIVERSE_TICK_EVERY == 0)
                current_universe_round = None
                if do_universe and _universe_round_by_identity is not None:
                    _universe_round += 1
                    current_universe_round = _universe_round

            if do_universe:
                from aletheia.storage.repositories import get_active_universe_members
                from aletheia.storage.repositories import get_universe_asset
                members = get_active_universe_members(limit=50)
                for m in members:
                    if not m.get("execution_eligible", True):
                        continue
                    asset = get_universe_asset(m.get("asset_id")) or {}
                    append_instrument(
                        m.get("symbol", ""),
                        m.get("market_type") or asset.get("market_type"),
                        asset.get("venue"),
                        universe_round=current_universe_round,
                    )
    except Exception as e:
        logger.debug("price_feed: universe expansion failed: %s", e)

    if _always_on_identities is not None:
        _always_on_identities.update(always_on if always_on_sources_complete else seen)
    if _universe_round_by_identity is not None:
        _universe_round_by_identity.update(universe_rounds)
    return result


def get_watched_symbols() -> list[str]:
    """Backward-compatible canonical-symbol view of the watched instruments."""
    symbols: list[str] = []
    seen: set[str] = set()
    for instrument in get_watched_instruments():
        if instrument.symbol not in seen:
            seen.add(instrument.symbol)
            symbols.append(instrument.symbol)
    return symbols


async def refresh_watched_prices() -> dict:
    """Fetch tickers for watched symbols (SERIALLY) and update in-memory cache.

    Uses a fresh exact-venue provider per group, closed on the same event loop.
    NOTE: fetches one symbol at a time — one network round-trip each, NOT a batch
    fetch_tickers call (I1 docstring-honesty fix: the prior docstring claimed a
    single batch round-trip, which the per-symbol loop below never did). The
    watched set is small so serial is acceptable; switch to a provider batch call
    if it grows. Never raises — errors are logged and cache entries left stale.
    """
    global _price_cache_owner_process
    _price_cache_owner_process = True

    always_on_identities: set[tuple[str, str, str]] = set()
    universe_round_by_identity: dict[tuple[str, str, str], int] = {}
    instruments = get_watched_instruments(
        _always_on_identities=always_on_identities,
        _universe_round_by_identity=universe_round_by_identity,
    )
    if not instruments:
        return {"refreshed": 0, "symbols": [], "errors": []}

    refreshed: list[str] = []
    errors: list[str] = []
    try:
        from collections import defaultdict

        from aletheia.data.providers import (
            get_market_data_provider,
            require_exact_ticker_venue,
        )
        from aletheia.config.loader import load_config

        cfg = load_config()
        groups = defaultdict(list)
        for instrument in instruments:
            groups[instrument.exchange].append(instrument)

        for venue, group in groups.items():
            # Every instrument, including venue-exclusive tokens, stays on its venue.
            provider = get_market_data_provider(exchange=venue, exact_venue=True)
            try:
                for instrument in group:
                    sym = instrument.symbol
                    _instrument_refresh_attempted.add(
                        (instrument.exchange, sym, instrument.market_type),
                    )
                    try:
                        ticker = require_exact_ticker_venue(
                            await provider.get_ticker(instrument), venue,
                        )
                        if ticker and ticker.last_price and ticker.last_price > 0:
                            observed_at = datetime.now(timezone.utc)
                            with _cache_lock:
                                instrument_key = (
                                    instrument.exchange,
                                    sym,
                                    instrument.market_type,
                                )
                                prev_entry = _instrument_price_cache.get(instrument_key, {})
                                try:
                                    previous_age_sec = (
                                        observed_at - prev_entry["ts"]
                                    ).total_seconds()
                                except (KeyError, TypeError, ValueError):
                                    previous_age_sec = None
                                current_universe_round = universe_round_by_identity.get(
                                    instrument_key
                                )
                                timestamp_comparable = (
                                    previous_age_sec is not None and previous_age_sec >= 0
                                )
                                if instrument_key in always_on_identities:
                                    previous_comparable = (
                                        timestamp_comparable
                                        and previous_age_sec <= _STALE_THRESHOLD_SEC
                                    )
                                elif current_universe_round is not None:
                                    previous_comparable = (
                                        timestamp_comparable
                                        and previous_age_sec
                                        <= _UNIVERSE_EVENT_CONTINUITY_MAX_GAP_SEC
                                        and prev_entry.get("event_universe_round")
                                        == current_universe_round - 1
                                    )
                                else:
                                    # Unknown source authority must not inherit the
                                    # wider Universe-only event horizon.
                                    previous_comparable = (
                                        timestamp_comparable
                                        and previous_age_sec <= _STALE_THRESHOLD_SEC
                                    )
                                prev_price = (
                                    prev_entry.get("price")
                                    if previous_comparable
                                    else None
                                )
                                prev_volume_24h = (
                                    prev_entry.get("volume_24h")
                                    if previous_comparable
                                    else None
                                )
                                volume_24h = getattr(ticker, "volume_24h", None) or prev_volume_24h
                                entry = {
                                    "price": ticker.last_price,
                                    "ts": observed_at,
                                    "prev_price": prev_price,
                                    "volume_24h": volume_24h,
                                    "provider": getattr(ticker, "provider", None) or "unknown",
                                    "symbol": sym,
                                    "market_type": instrument.market_type,
                                    "exchange": instrument.exchange,
                                    "event_universe_round": (
                                        current_universe_round
                                        if instrument_key not in always_on_identities
                                        else None
                                    ),
                                }
                                _instrument_price_cache[instrument_key] = dict(entry)
                                _price_cache[sym] = entry

                            if prev_price and prev_price > 0 and cfg.event_triggers.enabled:
                                pct = (ticker.last_price - prev_price) / prev_price * 100
                                _check_event_triggers(sym, pct, volume_24h, prev_volume_24h, cfg)

                            refreshed.append(sym)
                        else:
                            errors.append(f"{instrument}: upstream_quote_unavailable")
                    except Exception as e:
                        errors.append(f"{instrument}: {e}")
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
            _notify(f"Price alert {symbol}: {direction}{abs_pct:.1f}%")

        # Volume surge notification
        if (volume_24h and prev_volume_24h and prev_volume_24h > 0
                and volume_24h > prev_volume_24h * et.volume_surge_multiplier):
            surge = volume_24h / prev_volume_24h
            _notify(f"Volume surge {symbol}: {surge:.1f}x 24h vol")

        # Wake System Operator on any significant price/volume event
        if abs_pct >= et.price_move_pct_threshold or (
            volume_24h and prev_volume_24h and prev_volume_24h > 0
            and volume_24h > prev_volume_24h * et.volume_surge_multiplier
        ):
            try:
                from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
                notify_pi_daemon(source=f"event_trigger:{symbol}")
            except Exception:
                logger.warning("price_feed: event trigger could not wake the pi daemon", exc_info=True)

    except Exception as e:
        logger.debug("_check_event_triggers: %s", e)


def _notify(message: str) -> None:
    """Best-effort operator notification."""
    try:
        from aletheia.notifications import notify_operator
        notify_operator("Price Feed", message)
    except Exception:
        logger.debug("price_feed: best-effort operator notification failed", exc_info=True)


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
