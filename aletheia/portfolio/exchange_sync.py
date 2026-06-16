"""Exchange Account Sync — pull real account state from Bitget (read-only).

Closes the portfolio-sync-gap (memory `aletheia-portfolio-sync-gap` /
WEBUI_BACKEND_HANDOFF #3): until now `compute_portfolio` only saw locally-managed
positions, so the dashboard hero number was zero even when the real demo account
held 10,000 USDT. This module fetches the truth and caches it.

Governance:
- READ-ONLY against the exchange. This module never modifies external positions and
  never places orders. It also does not, by itself, write externals into the local
  positions table. Adoption of an external position into the local books (trust §1.1,
  AS AMENDED 2026-06-12) happens only via `position_reconciler.adopt_position`, which
  builds a synthetic-but-honest `source="adopted"` causal chain — see docs/DECISIONS.md.
  This module's job is purely to surface the truth so the reconciler can act on it.
- DEGRADES HONESTLY. Missing creds → `unconfigured`. API error → `error` + last
  good cache stays available. Stale cache → `stale`. Never fakes a positive state.
- CACHED. The HTTP API path reads cache only (no synchronous Bitget call); the
  scheduler refreshes it on an interval.
- Stored in the existing `settings` table as a JSON blob (no schema change).
"""

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aletheia.config.loader import load_config
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.exchange_sync")

# Settings key namespace; one entry per exchange-mode so demo/live don't collide.
_KEY_PREFIX = "exchange_account_state"

# Default freshness window for the cached state — anything older is reported `stale`.
_DEFAULT_MAX_AGE_SEC = 600


@dataclass
class AssetBreakdown:
    coin: str
    usdt_value: float   # current value in USDT
    pct: float          # percentage of total equity (0-100)


@dataclass
class ExchangeAccountState:
    mode: str  # "demo" | "live"
    equity_usdt: float
    available_usdt: float
    frozen_usdt: float
    futures_unrealized_pnl: float
    open_positions_count: int
    open_orders_count: int
    fetched_at: str  # ISO UTC
    asset_breakdown: list  # list[AssetBreakdown] as dicts for JSON serialisation
    positions: list  # list[dict] — raw exchange positions for exposure/PnL wiring


def _settings_key(mode: str) -> str:
    return f"{_KEY_PREFIX}.{mode}"


def _store(state: ExchangeAccountState) -> None:
    """Persist the latest snapshot. Idempotent upsert into settings.

    Uses a dedicated autocommit connection (isolation_level=None) so that the
    INSERT is immediately committed as a single atomic statement. This prevents
    the thread-local connection from holding an open write transaction if the
    daemon thread is abandoned by _run_sync_with_timeout — the root cause of
    the permanent DB lock under uvicorn.
    """
    from aletheia.storage.sqlite import db_path

    payload = json.dumps({
        "mode": state.mode,
        "equity_usdt": state.equity_usdt,
        "available_usdt": state.available_usdt,
        "frozen_usdt": state.frozen_usdt,
        "futures_unrealized_pnl": state.futures_unrealized_pnl,
        "open_positions_count": state.open_positions_count,
        "open_orders_count": state.open_orders_count,
        "fetched_at": state.fetched_at,
        "asset_breakdown": state.asset_breakdown or [],
        "positions": state.positions or [],
    })
    for attempt in range(3):
        conn = None
        try:
            conn = sqlite3.connect(str(db_path()), isolation_level=None, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (_settings_key(state.mode), payload),
            )
            return
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower() or attempt >= 2:
                raise
            time.sleep(0.25 * (attempt + 1))
        finally:
            if conn:
                conn.close()


def _read_raw(mode: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?", (_settings_key(mode),)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return None


def _age_seconds(fetched_at: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, AttributeError):
        return None


def get_cached_state(mode: Optional[str] = None) -> Optional[dict]:
    """Return the cached state for `mode` (defaults to current cfg.exchange.mode).

    Adds an `age_seconds` field. Caller decides what to do with stale data.
    Returns None if nothing has ever been fetched for this mode.
    """
    if mode is None:
        mode = load_config().exchange.mode
    raw = _read_raw(mode)
    if raw is None:
        return None
    raw["age_seconds"] = _age_seconds(raw.get("fetched_at", ""))
    return raw


def status_for(state: Optional[dict], max_age_sec: int = _DEFAULT_MAX_AGE_SEC) -> str:
    """Classify a cached state for the UI: ok / stale / unconfigured / error."""
    if state is None:
        return "unconfigured"
    age = state.get("age_seconds")
    if age is None or age > max_age_sec:
        return "stale"
    return "ok"


def _client_or_unconfigured() -> tuple[Optional[object], str]:
    """Return (client, mode) when creds are configured, else (None, mode).

    `BitgetClient._get_creds()` raises if any of the 3 secrets is missing — we
    catch that and report `unconfigured` cleanly so callers never see an exception.
    """
    cfg = load_config()
    mode = cfg.exchange.mode
    try:
        from aletheia.exchanges.bitget.client import BitgetClient
        client = BitgetClient(mode=mode)
        client._get_creds()  # raises RuntimeError when not configured
        return client, mode
    except Exception:
        return None, mode


def _run_async(coro):
    """Run a coroutine to completion from sync code, safe whether or not a loop runs.

    Uses shutdown(wait=False) so that a hung asyncio.run() teardown (Windows
    ProactorEventLoop IOCP drain) doesn't block the calling thread forever.
    The outer _run_sync_with_timeout will abandon this thread on hard timeout.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(asyncio.run, coro).result(timeout=55)
    finally:
        pool.shutdown(wait=False)


def sync_exchange_account() -> Optional[ExchangeAccountState]:
    """Fetch the real account state from Bitget once and cache it.

    Returns the new state on success, or None on any failure (caller falls back
    to the previous cached state). Never raises; logs and writes an audit on error.
    """
    client, mode = _client_or_unconfigured()
    if client is None:
        logger.info("exchange_sync skipped: %s credentials not configured", mode)
        return None

    try:
        # Build per-coin breakdown. Strategy:
        # 1. Try spot/account/assets — works for live keys, returns real coin balances.
        # 2. If spot returns empty (demo keys), fall back to mix/account/accounts which
        #    gives total usdtEquity but only one USDT "coin". We still fetch mix for
        #    total equity even when spot has data (mix is the authoritative total).
        # 3. Each non-USDT coin value is coin_qty * USDT_ticker_price.
        coin_usdt: dict[str, float] = {}  # coin → USDT value

        # Spot holdings (live accounts / coins held in spot wallet)
        try:
            balances = _run_async(client.get_balances())
            for b in balances:
                coin = (b.currency or "").upper()
                qty = float(b.available) + float(b.frozen)
                if qty <= 0:
                    continue
                if coin == "USDT":
                    coin_usdt["USDT"] = coin_usdt.get("USDT", 0) + qty
                else:
                    # Convert to USDT via spot ticker
                    try:
                        ticker = _run_async(client.get_ticker(f"{coin}USDT", market_type="spot"))
                        coin_usdt[coin] = coin_usdt.get(coin, 0) + qty * ticker.last_price
                    except Exception:
                        pass  # skip if no USDT pair
        except Exception as e:
            logger.info("exchange_sync: spot balances skipped (%s)", e)

        # Mix (futures) account — authoritative total equity for demo accounts
        usdt_avail = 0.0
        usdt_frozen = 0.0
        futures_upnl = 0.0
        equity_from_mix = None
        try:
            mix_acct = _run_async(client.get_mix_account("USDT-FUTURES"))
            if mix_acct:
                equity_from_mix = float(mix_acct.get("usdtEquity") or mix_acct.get("accountEquity") or 0)
                usdt_avail = float(mix_acct.get("available") or 0)
                usdt_frozen = float(mix_acct.get("locked") or 0)
                futures_upnl = float(mix_acct.get("unrealizedPL") or mix_acct.get("crossedUnrealizedPL") or 0)
                # If spot returned nothing (demo), use mix USDT as the whole balance
                if not coin_usdt and equity_from_mix > 0:
                    coin_usdt["USDT"] = equity_from_mix
                logger.info("exchange_sync: mix equity=%.4f avail=%.4f", equity_from_mix, usdt_avail)
        except Exception as e:
            logger.info("exchange_sync: mix account skipped (%s), falling back to spot", e)

        # Futures positions — unrealized PnL and open count
        positions_count = 0
        positions_list: list[dict] = []
        try:
            futures = _run_async(client.get_positions())
            positions_count = len(futures)
            positions_list = [
                {
                    "symbol": p.symbol,
                    "side": p.side,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "unrealized_pnl": float(p.unrealized_pnl or 0),
                    "leverage": p.leverage,
                    "mark_price": float(p.mark_price or 0),
                }
                for p in futures
            ]
            if equity_from_mix is None:
                futures_upnl = sum(float(p.unrealized_pnl or 0) for p in futures)
        except Exception as e:
            logger.info("exchange_sync: futures positions skipped (%s)", e)

        # Open orders
        open_orders = 0
        try:
            open_orders += len(_run_async(client.get_open_orders(market_type="spot")))
        except Exception as e:
            logger.info("exchange_sync: spot open orders skipped (%s)", e)
        try:
            open_orders += len(_run_async(client.get_open_orders(market_type="usdt_futures")))
        except Exception as e:
            logger.info("exchange_sync: futures open orders skipped (%s)", e)

        # No usable data from any source — return None to preserve the last good cache.
        if not coin_usdt and equity_from_mix is None:
            logger.info("exchange_sync: no data from any source, preserving cache")
            return None

        # Final equity: mix is authoritative when available, else sum of spot coins
        equity = equity_from_mix if equity_from_mix is not None else sum(coin_usdt.values())

        # Build breakdown list sorted by value desc, with percentages
        total = equity if equity > 0 else 1.0
        breakdown = [
            {"coin": coin, "usdt_value": round(val, 4), "pct": round(val / total * 100, 2)}
            for coin, val in sorted(coin_usdt.items(), key=lambda x: -x[1])
            if val > 0
        ]

        state = ExchangeAccountState(
            mode=mode,
            equity_usdt=round(equity, 4),
            available_usdt=round(usdt_avail, 4),
            frozen_usdt=round(usdt_frozen, 4),
            futures_unrealized_pnl=round(futures_upnl, 4),
            open_positions_count=positions_count,
            open_orders_count=open_orders,
            fetched_at=datetime.now(timezone.utc).isoformat(),
            asset_breakdown=breakdown,
            positions=positions_list,
        )
        _store(state)
        return state
    except Exception as e:
        logger.warning("exchange_sync failed: %s", e)
        try:
            from aletheia.files.jsonl import write_audit
            write_audit({
                "actor": "system", "source": "exchange_sync",
                "event_type": "exchange_sync_failed", "entity_type": "exchange_account",
                "entity_id": mode, "message": f"sync error: {e}",
            })
        except Exception:
            pass
        return None
