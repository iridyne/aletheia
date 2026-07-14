"""Market-data collection loop — fetches tickers and stores snapshots.

Runs without credentials (public market endpoints via CCXT).
Degrades gracefully if the exchange is unreachable.
"""

import logging

from aletheia.config.loader import load_config
from aletheia.data.symbols import InstrumentId, MARKET_SPOT, MARKET_PERP, to_canonical, to_bitget
from aletheia.storage.models import MarketSnapshot
from aletheia.storage.repositories import insert_market_snapshot
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
        # NOTE: the DB row (market_snapshots) is the canonical, queried record.
        # The former append_jsonl("market_snapshots.jsonl", ...) write was
        # write-only — no reader anywhere in the codebase — so it only grew
        # data/logs/ unbounded (~11.6MB) with zero consumers. Removed 2026-06-28
        # (junk-data sweep B2). Re-add a rotation-bounded sink here if a real
        # offline consumer ever needs the raw stream.
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
                    _xstock_failure_counts[instrument.symbol] = 0  # reset on success
                except Exception as e:
                    errors.append(f"{instrument}: {e}")
                    logger.warning("Failed to fetch xStocks ticker %s: %s", instrument, e)
                    sym = instrument.symbol
                    _xstock_failure_counts[sym] = _xstock_failure_counts.get(sym, 0) + 1
                    if _xstock_failure_counts[sym] >= _XSTOCK_FAILURE_THRESHOLD:
                        from aletheia.files.jsonl import write_audit
                        write_audit({
                            "actor": "system",
                            "source": "market_data",
                            "event_type": "infra_event",
                            "entity_id": sym,
                            "message": (
                                f"xStock {sym} fetch failed {_xstock_failure_counts[sym]} "
                                "consecutive times — provider may be degraded"
                            ),
                            "severity": "warning",
                            "consecutive_failures": _xstock_failure_counts[sym],
                            "last_error": str(e)[:200],
                        })
        finally:
            await xprovider.close()

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
