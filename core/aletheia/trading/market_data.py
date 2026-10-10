"""Market-data collection loop — fetches tickers and stores snapshots.

Runs without credentials (public market endpoints via CCXT).
Degrades gracefully if the exchange is unreachable.
"""

from aletheia.config.exchange_alignment import market_data_exchange
import asyncio
import logging

from aletheia.config.loader import load_config
from aletheia.config.exchanges import execution_mode_venue
from aletheia.data.symbols import InstrumentId, MARKET_SPOT, MARKET_PERP, to_canonical, to_bitget
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot, insert_market_snapshot_batch
from aletheia.trading.instruments import (
    MARKET_USDT_FUTURES,
    observed_instruments_from_config,
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


def _build_dynamic_instruments(cfg) -> list[InstrumentId]:
    """Build deduplicated instrument list from config + universe + open positions.

    Sources (first occurrence wins for dedup):
      1. Open position symbols (never capped; lifecycle pricing comes first)
      2. Active pending candidates and EntryPlans (never displaced by discovery work)
      3. Config core symbols (always included when capacity remains)
      4. Active universe members (dynamic — the main altcoin source)

    Discovery sources are capped at _MAX_DYNAMIC_INSTRUMENTS to keep collection
    within the scheduler budget; the total expands when open Positions exceed it.
    """
    exchange = market_data_exchange(cfg)

    seen: dict[tuple[str, str, str], InstrumentId] = {}
    try:
        from aletheia.storage import repositories as repo
        for pos in repo.list_all_open_positions():
            sym = pos.get("symbol")
            if not sym:
                continue
            try:
                mt = _instrument_market_from_row(pos.get("market_type"))
                venue = execution_mode_venue(pos.get("execution_mode"))
                if venue is None:
                    continue
                iid = InstrumentId.from_parts(venue, sym, mt)
                seen.setdefault((iid.exchange, iid.symbol, iid.market_type), iid)
            except ValueError:
                logger.warning("market_data: instrument id construction failed for an open position; position left untracked for price updates", exc_info=True)
    except Exception:
        logger.warning("market_data: instrument scan outer block failed (partial result)", exc_info=True)
    n_open = len(seen)

    try:
        for asset in repo.list_candidate_planning_refresh_assets():
            iid = InstrumentId.from_parts(asset["venue"], asset["symbol"], asset["market_type"])
            seen.setdefault((iid.exchange, iid.symbol, iid.market_type), iid)
    except Exception:
        logger.warning("market_snapshots: planning expansion failed", exc_info=True)
    try:
        for plan in repo.list_watchable_entry_plans(limit=None):
            iid = InstrumentId.from_parts(
                plan["venue"], plan["symbol"], _instrument_market_from_row(plan["market_type"]),
            )
            seen.setdefault((iid.exchange, iid.symbol, iid.market_type), iid)
    except Exception:
        logger.warning("market_snapshots: entry plan expansion failed", exc_info=True)
    n_pending = len(seen) - n_open

    for iid in observed_instruments_from_config(cfg):
        seen.setdefault((iid.exchange, iid.symbol, iid.market_type), iid)
    n_config = len(seen) - n_open - n_pending

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
                seen.setdefault((iid.exchange, iid.symbol, iid.market_type), iid)
            except ValueError:
                logger.warning("market_data: instrument id construction failed for a universe candidate; symbol left out of the watch set", exc_info=True)
    except Exception:
        logger.warning("market_data: candidate instruments scan failed (partial result)", exc_info=True)

    # The cap protects discovery work, never an existing Position. When open
    # positions alone exceed it, expand the cycle rather than silently starving
    # a stop/PnL lifecycle of fresh prices.
    effective_cap = max(_MAX_DYNAMIC_INSTRUMENTS, n_open + n_pending)
    instruments = list(seen.values())[:effective_cap]
    logger.debug(
        "market_snapshots: collecting %d instruments "
        "(open=%d pending=%d config=%d dynamic=%d)",
        len(instruments),
        n_open,
        n_pending,
        n_config,
        max(0, len(instruments) - n_open - n_pending - n_config),
    )
    return instruments


def market_snapshot_target_groups(cfg) -> set[tuple[str, str]]:
    """Return the venue/market-type groups this collector is responsible for."""
    return {(target.exchange, target.market_type) for target in _build_dynamic_instruments(cfg)}


async def collect_market_snapshots() -> dict:
    """Fetch a ticker for each observed instrument and persist a market snapshot.

    Includes open Positions and exact pending carriers before bounded discovery
    sources so active entry candidates retain durable price observations.
    """
    # The gateway owner applies pending migrations before process start; scheduler
    # jobs MUST NOT apply migrations — schema ownership stays outside workers.
    # was the dominant `database is locked` source.
    cfg = load_config()

    instruments = _build_dynamic_instruments(cfg)

    from aletheia.data.providers import get_market_data_provider, require_exact_ticker_venue
    collected = []
    errors = []
    # Batch accumulators — all network fetches complete first, then a single
    # batch write acquires the write lock exactly once per collection cycle.
    # Previously each _fetch_and_store call issued its own BEGIN/INSERT/COMMIT,
    # causing N write transactions per cycle (N = watched instrument count).
    _pending_snaps: list[MarketSnapshot] = []

    async def _fetch_snap(instrument, prov) -> MarketSnapshot:
        """Fetch one ticker and return a MarketSnapshot; does NOT write to DB.

        Separating fetch from persist lets the caller accumulate all snapshots
        and flush them in a single batch transaction (one write-lock acquisition
        per cycle instead of one per ticker).
        """
        ticker = require_exact_ticker_venue(
            await prov.get_ticker(instrument), instrument.exchange,
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

    primary_written = 0
    providers = {}
    try:
        for instrument in instruments:
            try:
                venue = instrument.exchange
                if venue not in providers:
                    providers[venue] = get_market_data_provider(
                        exchange=venue, exact_venue=True,
                    )
                provider = providers[venue]
                snap = await _fetch_snap(instrument, provider)
                _pending_snaps.append(snap)
                collected.append(str(instrument))
            except Exception as e:
                errors.append(f"{instrument}: {e}")
                logger.warning("Failed to fetch ticker %s: %s", instrument, e)
    finally:
        try:
            if _pending_snaps:
                primary_written = insert_market_snapshot_batch(_pending_snaps)
                _pending_snaps.clear()
            logger.info(
                "market_data sources finished attempted=%d written=%d errors=%d",
                len(instruments),
                primary_written,
                len(errors),
            )
        finally:
            close_results = await asyncio.gather(
                *(provider.close() for provider in providers.values()),
                return_exceptions=True,
            )
            for result in close_results:
                if isinstance(result, BaseException):
                    raise result

    return {
        "attempted": len(instruments),
        "written": len(collected),
        "collected": collected,
        "errors": errors,
    }


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
