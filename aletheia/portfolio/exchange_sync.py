"""Exchange Account Sync — pull real account state from Bitget (read-only).

Closes the portfolio-sync-gap (memory `aletheia-portfolio-sync-gap` /
WEBUI_BACKEND_HANDOFF #3): until now `compute_portfolio` only saw locally-managed
positions, so the dashboard hero number was zero even when the real demo account
held 10,000 USDT. This module fetches the truth and caches it.

Governance:
- READ-ONLY against the exchange. We never modify external positions, never place
  orders here, never reconcile externals into the local positions table (they have
  no proposal back-link, so they would violate trust §1.1).
- DEGRADES HONESTLY. Missing creds → `unconfigured`. API error → `error` + last
  good cache stays available. Stale cache → `stale`. Never fakes a positive state.
- CACHED. The HTTP API path reads cache only (no synchronous Bitget call); the
  scheduler refreshes it on an interval.
- Stored in the existing `settings` table as a JSON blob (no schema change).
"""

import json
import logging
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
class ExchangeAccountState:
    mode: str  # "demo" | "live"
    equity_usdt: float
    available_usdt: float
    frozen_usdt: float
    futures_unrealized_pnl: float
    open_positions_count: int
    open_orders_count: int
    fetched_at: str  # ISO UTC


def _settings_key(mode: str) -> str:
    return f"{_KEY_PREFIX}.{mode}"


def _store(state: ExchangeAccountState) -> None:
    """Persist the latest snapshot. Idempotent upsert into settings."""
    db = get_db()
    payload = json.dumps({
        "mode": state.mode,
        "equity_usdt": state.equity_usdt,
        "available_usdt": state.available_usdt,
        "frozen_usdt": state.frozen_usdt,
        "futures_unrealized_pnl": state.futures_unrealized_pnl,
        "open_positions_count": state.open_positions_count,
        "open_orders_count": state.open_orders_count,
        "fetched_at": state.fetched_at,
    })
    # SQLite UPSERT — settings has key as PRIMARY KEY.
    db.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (_settings_key(state.mode), payload),
    )
    db.commit()


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
    """Run a coroutine to completion from sync code, safe whether or not a loop runs."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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
        # Primary path: mix/account/accounts returns usdtEquity for both demo and
        # live keys. Bitget demo keys return empty data from spot/account/assets,
        # so we prefer the mix endpoint which correctly includes all asset value.
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
                logger.info("exchange_sync: mix equity=%.4f avail=%.4f", equity_from_mix, usdt_avail)
        except Exception as e:
            logger.info("exchange_sync: mix account skipped (%s), falling back to spot", e)

        # Fallback: spot/account/assets (works for live keys, empty for demo keys)
        if equity_from_mix is None:
            balances = _run_async(client.get_balances())
            for b in balances:
                if (b.currency or "").upper() == "USDT":
                    usdt_avail = float(b.available)
                    usdt_frozen = float(b.frozen)
                    break

        # Futures positions (open count + unrealized PnL if not already from mix).
        positions_count = 0
        try:
            futures = _run_async(client.get_positions())
            positions_count = len(futures)
            if equity_from_mix is None:
                futures_upnl = sum(float(p.unrealized_pnl or 0) for p in futures)
        except Exception as e:
            logger.info("exchange_sync: futures positions skipped (%s)", e)

        # Open orders (spot + futures).
        open_orders = 0
        try:
            open_orders += len(_run_async(client.get_open_orders(market_type="spot")))
        except Exception as e:
            logger.info("exchange_sync: spot open orders skipped (%s)", e)
        try:
            open_orders += len(_run_async(client.get_open_orders(market_type="usdt_futures")))
        except Exception as e:
            logger.info("exchange_sync: futures open orders skipped (%s)", e)

        equity = equity_from_mix if equity_from_mix is not None else (usdt_avail + usdt_frozen + futures_upnl)
        state = ExchangeAccountState(
            mode=mode,
            equity_usdt=round(equity, 4),
            available_usdt=round(usdt_avail, 4),
            frozen_usdt=round(usdt_frozen, 4),
            futures_unrealized_pnl=round(futures_upnl, 4),
            open_positions_count=positions_count,
            open_orders_count=open_orders,
            fetched_at=datetime.now(timezone.utc).isoformat(),
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
