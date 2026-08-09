"""Market-data collection loop — fetches tickers and stores snapshots.

Runs without credentials (public market endpoints via CCXT).
Degrades gracefully if the exchange is unreachable.
"""

import asyncio
import logging
import math
from typing import Optional

from aletheia.config.loader import load_config
from aletheia.data.symbols import InstrumentId, MARKET_SPOT, MARKET_PERP, to_canonical, to_bitget
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot, insert_market_snapshot_batch
from aletheia.trading.instruments import (
    MARKET_USDT_FUTURES,
    observed_instruments_from_config,
    xstocks_instruments_from_config,
)

logger = logging.getLogger("aletheia.market_data")

_MAX_DYNAMIC_INSTRUMENTS = 60

# Snapshots are stored under the InstrumentId physical vocabulary (spot/perp/
# futures). Proposals / DB rows use the execution vocabulary (spot/usdt_futures).
# This maps the latter to the former so a typed snapshot lookup hits the right rows.
_SNAPSHOT_MARKET_TYPE = {
    MARKET_USDT_FUTURES: MARKET_PERP,  # "usdt_futures" -> "perp"
    MARKET_SPOT: MARKET_SPOT,
    MARKET_PERP: MARKET_PERP,
    "futures": "futures",
}


def _snapshot_market_type(mt: str | None) -> str | None:
    """Map a proposal/DB market_type to the snapshot-storage vocabulary.

    Returns None when mt is falsy or unmapped, so ``latest_snapshot`` falls back
    to the symbol-only lookup rather than querying for a value that can't exist.
    """
    if not mt:
        return None
    return _SNAPSHOT_MARKET_TYPE.get(mt)


def _instrument_market_from_row(mt: str | None) -> str:
    """Map a DB row's market_type to the InstrumentId physical vocabulary.

    Positions/universe members store the execution string "usdt_futures";
    ``InstrumentId.from_parts`` only accepts spot/perp/futures. Falls back to
    spot for None/unknown so callers keep the pre-#14 default.
    """
    if mt == MARKET_USDT_FUTURES:
        return MARKET_PERP
    if mt in (MARKET_SPOT, MARKET_PERP, "futures"):
        return mt
    return MARKET_SPOT

# Consecutive xStock fetch failure counter per symbol.
# Triggers infra_event after XSTOCK_FAILURE_THRESHOLD consecutive failures.
_xstock_failure_counts: dict[str, int] = {}
_XSTOCK_FAILURE_THRESHOLD = 3


def _prune_xstock_failure_counts(active_symbols: set[str]) -> None:
    """Remove failure-count entries for symbols no longer in the active set.

    Without this, a symbol that is removed from the xStock universe leaves a
    stale entry that never gets cleaned up (success and auto-remove paths only
    fire for symbols still being iterated).
    """
    stale = [s for s in _xstock_failure_counts if s not in active_symbols]
    for s in stale:
        _xstock_failure_counts.pop(s, None)


_XSTOCK_LISTING_TIMEOUT_SEC = 10.0


def _consume_detached_task(task: asyncio.Future) -> None:
    """Consume a deadline-detached task result so late failures are not leaked."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


async def _wait_without_cancel_drain(awaitable, *, timeout: float):
    """Enforce a wall-clock deadline without awaiting cancellation-resistant I/O."""
    task = asyncio.ensure_future(awaitable)
    try:
        done, _pending = await asyncio.wait({task}, timeout=timeout)
    except asyncio.CancelledError:
        task.add_done_callback(_consume_detached_task)
        task.cancel()
        raise
    if task not in done:
        task.add_done_callback(_consume_detached_task)
        task.cancel()
        raise asyncio.TimeoutError
    return task.result()


async def _listed_symbols_on_exchange(exchange_name: str) -> frozenset[str] | None:
    """Return active spot symbols, or None when listing status is indeterminate.

    The complete provider lifecycle runs in the deadline-owned task. If CCXT resists
    cancellation, the caller returns at the deadline while that task finishes cleanup.
    """

    async def _load_and_close() -> frozenset[str]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider(
            exchange=exchange_name,
            native_ccxt=True,
            fallback_exchange=exchange_name,
        )
        try:
            exchange = provider._ensure_primary()
            exchange.options["defaultType"] = "spot"
            markets = await exchange.load_markets()
            if not isinstance(markets, dict):
                raise TypeError("exchange market list is not a mapping")
            listed = frozenset(
                to_canonical(str(symbol))
                for symbol, market in markets.items()
                if isinstance(market, dict)
                and market.get("spot") is True
                and market.get("active") is not False
            )
            if not listed:
                raise ValueError("exchange returned no active spot markets")
            return listed
        finally:
            await provider.close()

    try:
        return await _wait_without_cancel_drain(
            _load_and_close(), timeout=_XSTOCK_LISTING_TIMEOUT_SEC
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(
            "Could not load listing status from %s (%s); keeping configured symbols",
            exchange_name,
            e,
        )
        return None


def _auto_remove_xstock(sym: str, exchange_name: str, consecutive: int) -> bool:
    """Remove a stock token from config after live market-list verification confirms it is absent.

    consecutive is the current failure count, used only for logging context.
    """
    try:
        from aletheia.config.loader import load_config, save_config
        from aletheia.infra.events import emit_infra_event

        cfg = load_config()
        canon = to_canonical(sym)
        tokens = list(cfg.market_data.observed_stock_tokens or [])
        legacy_tokens = list(cfg.market_data.observed_xstocks or [])
        filtered_tokens = [t for t in tokens if to_canonical(t) != canon]
        filtered_legacy = [t for t in legacy_tokens if to_canonical(t) != canon]
        if filtered_tokens == tokens and filtered_legacy == legacy_tokens:
            return False
        before = {
            "observed_stock_tokens": tokens,
            "observed_xstocks": legacy_tokens,
        }
        after = {
            "observed_stock_tokens": filtered_tokens,
            "observed_xstocks": filtered_legacy,
        }
        cfg.market_data.observed_stock_tokens = filtered_tokens
        cfg.market_data.observed_xstocks = filtered_legacy
        save_config(cfg)
        logger.warning(
            "Auto-removed xStock %s from config (confirmed not listed on exchange, "
            "failure count=%d)",
            sym, consecutive,
        )
        try:
            from aletheia.files.jsonl import write_audit

            write_audit({
                "actor": "system",
                "source": "market_data",
                "event_type": "xstock_observation_config_auto_removed",
                "entity_type": "market_data_config",
                "entity_id": canon,
                "before": before,
                "after": after,
                "exchange": exchange_name,
                "consecutive_failures": consecutive,
                "message": (
                    f"Removed {canon} from xStock observation config after "
                    f"{exchange_name} confirmed it absent"
                ),
            })
        except Exception:  # noqa: BLE001
            logger.error(
                "xStock %s config removal persisted without an audit record",
                canon,
                exc_info=True,
            )
        try:
            emit_infra_event(
                severity="warning",
                source="market_data",
                code=f"xstock_auto_removed:{canon}",
                message=(
                    f"xStock {canon} auto-removed from config after {exchange_name} "
                    f"confirmed it absent (failure count={consecutive})"
                ),
                action=(
                    "Review observed_stock_tokens and observed_xstocks in config "
                    "to confirm removal"
                ),
                dedupe_sec=0,
            )
        except Exception:  # noqa: BLE001
            logger.debug("Failed to emit xStock removal event for %s", canon, exc_info=True)
        return True
    except Exception:
        logger.debug("Failed to auto-remove xStock %s from config", sym, exc_info=True)
        return False


def _build_dynamic_instruments(cfg) -> list[InstrumentId]:
    """Build deduplicated instrument list from config + universe + open positions.

    Sources (first occurrence wins for dedup):
      1. Open position symbols (never capped; lifecycle pricing comes first)
      2. Config core symbols (always included when capacity remains)
      3. Active universe members (dynamic — the main altcoin source)

    Discovery sources are capped at _MAX_DYNAMIC_INSTRUMENTS to keep collection
    within the scheduler budget; the total expands when open Positions exceed it.
    """
    exchange = cfg.market_data.ccxt_exchange or "binance"

    seen: dict[tuple[str, str], InstrumentId] = {}
    try:
        from aletheia.storage import repositories as repo
        for pos in repo.list_all_open_positions():
            sym = pos.get("symbol")
            if not sym:
                continue
            try:
                mt = _instrument_market_from_row(pos.get("market_type"))
                iid = InstrumentId.from_parts(exchange, sym, mt)
                seen.setdefault((iid.symbol, iid.market_type), iid)
            except (ValueError, Exception):
                pass
    except Exception:
        pass
    n_open = len(seen)

    for iid in observed_instruments_from_config(cfg):
        seen.setdefault((iid.symbol, iid.market_type), iid)
    n_config = len(seen) - n_open

    try:
        from aletheia.storage import repositories as repo
        members = repo.get_active_universe_members(limit=cfg.universe.max_candidates)
        for m in members:
            sym = m.get("symbol")
            if not sym:
                continue
            try:
                # #14: honour the member's own market_type instead of forcing spot,
                # so a perp universe member gets its perp ticker collected.
                mt = _instrument_market_from_row(m.get("market_type"))
                iid = InstrumentId.from_parts(exchange, sym, mt)
                seen.setdefault((iid.symbol, iid.market_type), iid)
            except (ValueError, Exception):
                pass
    except Exception:
        pass

    # The cap protects discovery work, never an existing Position. When open
    # positions alone exceed it, expand the cycle rather than silently starving
    # a stop/PnL lifecycle of fresh prices.
    effective_cap = max(_MAX_DYNAMIC_INSTRUMENTS, n_open)
    instruments = list(seen.values())[:effective_cap]
    logger.debug(
        "market_snapshots: collecting %d instruments (open=%d config=%d dynamic=%d)",
        len(instruments),
        n_open,
        n_config,
        max(0, len(instruments) - n_open - n_config),
    )
    return instruments


async def collect_market_snapshots() -> dict:
    """Fetch a ticker for each observed instrument and persist a market snapshot.

    Dynamically includes universe members and open-position symbols so that
    altcoin candidates have fresh price data for paper trading.

    xStock instruments are pre-filtered against the Bitget venue listing cache
    (TTL 6h) before the fetch loop so that delisted tokens never enter the
    loop and never accumulate error counts or trigger DB writes.
    """
    # The gateway owner applies pending migrations before process start; scheduler
    # jobs MUST NOT apply migrations — schema ownership stays outside workers.
    # was the dominant `database is locked` source.
    cfg = load_config()

    instruments = _build_dynamic_instruments(cfg)
    _xstocks_raw = xstocks_instruments_from_config(cfg)
    # Pre-filter xStocks against the venue listing whitelist.  This resolves
    # the root cause of the 569-consecutive-failure loop: delisted ON-series
    # tokens (AAPLON/USDT etc.) are dropped here before any network call is made.
    # fail-open: if the cache cannot load (network down), all xStocks are kept.
    if _xstocks_raw:
        from aletheia.data.venue_listing_cache import get_venue_listing, filter_by_listing
        _bitget_listing = await get_venue_listing("bitget")
        xstocks = filter_by_listing(_xstocks_raw, listing=_bitget_listing, venue="bitget")
    else:
        xstocks = _xstocks_raw

    from aletheia.data.ccxt_provider import (
        CCXTProvider,
        get_market_data_provider,
        require_exact_ticker_venue,
    )
    primary_exchange = cfg.market_data.ccxt_exchange or "binance"
    provider = get_market_data_provider(
        exchange=primary_exchange,
        native_ccxt=True,
        fallback_exchange=primary_exchange,
    )

    collected = []
    errors = []
    # Batch accumulators — all network fetches complete first, then a single
    # batch write acquires the write lock exactly once per collection cycle.
    # Previously each _fetch_and_store call issued its own BEGIN/INSERT/COMMIT,
    # causing N write transactions per cycle (N = watched instrument count).
    _pending_snaps: list[MarketSnapshot] = []

    # Perf (Phase 5): concurrency bound for ticker fetches. Each fetch is one
    # CCXT network round-trip; a semaphore of 12 keeps the provider's rate limit
    # headroom intact while turning the O(N) serial loop into bounded-parallel
    # execution. Tuned below the typical CCXT-per-IP concurrency cap (~15-20).
    _fetch_sem = asyncio.Semaphore(12)

    async def _fetch_snap(instrument, prov) -> MarketSnapshot:
        """Fetch one ticker and return a MarketSnapshot; does NOT write to DB.

        Separating fetch from persist lets the caller accumulate all snapshots
        and flush them in a single batch transaction (one write-lock acquisition
        per cycle instead of one per ticker).
        """
        async with _fetch_sem:
            ticker = require_exact_ticker_venue(
                await prov.get_ticker(instrument), instrument.exchange,
            )
        # Reject non-positive prices: a 0.0 price used to be written as an
        # authoritative snapshot and then read back by valuation and
        # reconciliation as fact. Raising here routes the instrument into the
        # caller's existing per-instrument error list instead of persisting
        # a poisoned row.
        _price = ticker.last_price
        if (
            _price is None
            or isinstance(_price, bool)
            or not math.isfinite(float(_price))
            or float(_price) <= 0
        ):
            raise ValueError(
                f"refusing snapshot for {instrument}: non-positive price {_price!r} "
                f"from {ticker.provider or 'unknown'}"
            )
        # NOTE: the DB row (market_snapshots) is the canonical, queried record.
        # The former append_jsonl("market_snapshots.jsonl", ...) write was
        # write-only — no reader anywhere in the codebase — so it only grew
        # runtime-home logs/ unbounded (~11.6MB) with zero consumers. Removed 2026-06-28
        # (junk-data sweep B2). Re-add a rotation-bounded sink here if a real
        # offline consumer ever needs the raw stream.
        return MarketSnapshot(
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

    async def _fetch_snap_isolated(instrument, prov) -> None:
        """Fetch one ticker with per-instrument failure isolation.

        Appends the snapshot to _pending_snaps on success, or records the error
        on failure — one failure never aborts the whole batch."""
        try:
            snap = await _fetch_snap(instrument, prov)
            _pending_snaps.append(snap)
            collected.append(str(instrument))
        except Exception as e:
            errors.append(f"{instrument}: {e}")
            logger.warning("Failed to fetch ticker %s: %s", instrument, e)

    # Perf (Phase 5): bounded-parallel fetch via asyncio.gather instead of a
    # serial await loop. The semaphore inside _fetch_snap caps concurrency; the
    # try/except inside _fetch_snap_isolated preserves the original per-instrument
    # failure isolation (one failure does not abort others).
    try:
        await asyncio.gather(
            *(_fetch_snap_isolated(inst, provider) for inst in instruments),
            return_exceptions=True,
        )
    finally:
        await provider.close()

    # xStocks: separate Bitget provider (no API key needed, ON-series tokens).
    # Deliberate exception: NOT routed through get_market_data_provider(). ON-series
    # xStocks only list on Bitget, so both primary AND fallback must be bitget — the
    # factory would force the config fallback (bybit), which doesn't list these tokens.
    # Note: xstocks has already been filtered by the venue listing cache above;
    # the listing_cache below is the post-failure auto-remove path (kept as belt-and-suspenders).
    if xstocks:
        # Prune stale failure-count entries for symbols no longer configured.
        _prune_xstock_failure_counts({inst.symbol for inst in xstocks})
        xprovider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
        listing_cache: dict[str, Optional[frozenset[str]]] = {}

        async def _fetch_xstock_isolated(instrument) -> None:
            """Fetch one xStocks ticker with failure isolation + audit trail."""
            try:
                snap = await _fetch_snap(instrument, xprovider)
                _pending_snaps.append(snap)
                collected.append(str(instrument))
                _xstock_failure_counts[instrument.symbol] = 0  # reset on success
            except Exception as e:
                errors.append(f"{instrument}: {e}")
                logger.warning("Failed to fetch xStocks ticker %s: %s", instrument, e)
                sym = instrument.symbol
                _xstock_failure_counts[sym] = _xstock_failure_counts.get(sym, 0) + 1
                consecutive = _xstock_failure_counts[sym]
                # Emit once when this failure window crosses the threshold.
                # Per-cycle warning logs continue below/above the threshold;
                # repeating the durable audit forever only amplifies writes.
                if consecutive == _XSTOCK_FAILURE_THRESHOLD:
                    from aletheia.files.jsonl import write_audit
                    write_audit({
                        "actor": "system",
                        "source": "market_data",
                        "event_type": "infra_event",
                        "entity_id": sym,
                        "message": (
                            f"xStock {sym} fetch failed {consecutive} "
                            "consecutive times — provider may be degraded"
                        ),
                        "severity": "warning",
                        "consecutive_failures": consecutive,
                        "last_error": str(e)[:200],
                    })
                    # Load each responsible venue once per collection cycle. None means
                    # indeterminate and fails open by preserving configured intent.
                    exchange_name = instrument.exchange
                    if exchange_name not in listing_cache:
                        listing_cache[exchange_name] = await _listed_symbols_on_exchange(
                            exchange_name
                        )
                    listed = listing_cache[exchange_name]
                    if listed is not None and to_canonical(sym) not in listed:
                        if _auto_remove_xstock(sym, exchange_name, consecutive):
                            _xstock_failure_counts.pop(sym, None)

        try:
            # Perf (Phase 5): bounded-parallel xStocks fetch. The semaphore is
            # shared with the main loop (_fetch_snap), so total concurrency across
            # both instrument sets stays bounded at 12.
            await asyncio.gather(
                *(_fetch_xstock_isolated(inst) for inst in xstocks),
                return_exceptions=True,
            )
        finally:
            await xprovider.close()

    # Single batch write: one write-lock acquisition for the entire cycle.
    # Replaces N individual INSERT+COMMIT calls (one per ticker) with a single
    # executemany transaction, reducing SQLite write-lock contention.
    if _pending_snaps:
        insert_market_snapshot_batch(_pending_snaps)

    return {"collected": collected, "errors": errors}


def latest_snapshot(symbol: str, market_type: str | None = None) -> dict | None:
    """Return the most recent stored snapshot for a symbol.

    Accepts any symbol format (canonical, bitget, yfinance).
    Tries canonical first, then bitget format for backwards compatibility
    with snapshots written before the CCXT migration.

    When ``market_type`` is given (proposal/DB vocabulary, e.g. "usdt_futures"),
    a typed snapshot is preferred so a futures decision does not read a spot row
    for the same symbol (#13). If no typed snapshot exists it FALLS BACK to the
    symbol-only lookup — old DBs stored futures snapshots as "spot" (the #14 bug),
    and the risk freshness check fails closed on None, so falling back keeps
    behaviour strictly more correct with no new rejections.
    """
    from aletheia.storage.repositories import list_market_snapshots
    canon = to_canonical(symbol)
    snap_mt = _snapshot_market_type(market_type)
    if snap_mt:
        rows = list_market_snapshots(symbol=canon, market_type=snap_mt, limit=1)
        if rows:
            return rows[0]
        # fall through to symbol-only lookup below
    rows = list_market_snapshots(symbol=canon, limit=1)
    if rows:
        return rows[0]
    # Fallback: old snapshots stored with Bitget format (BTCUSDT)
    bitget_sym = to_bitget(canon)
    if bitget_sym != canon:
        rows = list_market_snapshots(symbol=bitget_sym, limit=1)
        return rows[0] if rows else None
    return None
