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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from aletheia.config.loader import load_config
from aletheia.storage.sqlite import execute_write, get_db

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

    The writer lane owns the connection, so an abandoned timeout worker cannot
    leave an implicit transaction open on its thread-local connection.
    """
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
    execute_write(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (_settings_key(state.mode), payload),
    )


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

    Multi-broker default-path correction: the legacy per-mode cache key is
    "demo"/"live" and is written ONLY by the Bitget-specific sync
    (`sync_exchange_account`). When the active paper broker has migrated away
    from Bitget (e.g. paper_execution_mode="okx_demo"), that "demo" cache holds
    the stale/idle Bitget balance (~$167), NOT the real active account. Callers
    that pass `mode=None` (the trading context, PnL, scorecard) want the REAL
    active-account equity — so on the default path we prefer the unified
    (multi-broker) cache projection when the active broker isn't Bitget. Callers
    that pass an explicit `mode` (CLI bitget tools) still get the per-mode cache
    verbatim. This keeps the System Operator from seeing a phantom near-bankrupt account
    and skipping every signal (the "minimum profit loop" stall, 2026-06-23).
    """
    explicit_mode = mode is not None
    if mode is None:
        cfg = load_config()
        mode = cfg.exchange.mode
        # Default path + active paper broker is non-Bitget → use unified cache,
        # which carries the true total equity across all brokers.
        if not explicit_mode and not (cfg.exchange.paper_execution_mode or "").startswith("bitget"):
            unified = get_unified_cached_state()
            if unified is not None and (unified.get("total_equity_usdt") or 0) > 0:
                return unified
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


def _run_async(coro, timeout: float = 10.0):
    """Run a coroutine to completion from sync code, safe whether or not a loop runs.

    Uses shutdown(wait=False) so that a hung asyncio.run() teardown (Windows
    ProactorEventLoop IOCP drain) doesn't block the calling thread forever.
    The outer _run_sync_with_timeout has a 75s budget; individual API calls
    must finish well within that (default 10s each) to avoid the outer guard
    abandoning this thread while inner pools still hold live sockets.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(asyncio.run, coro).result(timeout=timeout)
    finally:
        pool.shutdown(wait=False)




def get_unified_cached_state() -> Optional[dict]:
    """Return the cached unified (multi-broker) state."""
    raw = _read_raw("unified")
    if raw is None:
        return None
    raw["age_seconds"] = _age_seconds(raw.get("fetched_at", ""))
    return raw


def active_broker_view_complete(cfg=None) -> tuple[bool, str]:
    """Is the ACTIVE paper broker actually present in the unified (multi-broker) view?

    The UTA sync records brokers that returned an account in `accounts[].broker_id`
    and brokers whose query FAILED in `errors`. When the active broker (e.g.
    okx_demo) is NOT among the connected accounts — its API failed (`50001`),
    creds missing, etc. — the unified view is INCOMPLETE for the account we
    actually trade. Acting on it is unsafe on two fronts, both observed 2026-07-01:

      - reconciler: classifies the active broker's real positions as `orphaned`
        (they're absent from the incomplete view) → reconcile-close → re-adopt
        thrash. This is distinct from a STALE cache — the view can be fresh yet
        missing the broker we care about.
      - sizing / drawdown: reads the WRONG account's equity (e.g. the idle bitget
        $167 instead of the real okx ~$89k), fabricating a false drawdown or
        mis-scaling any %-of-equity size.

    Returns (complete, reason). `paper_internal` (no exchange) is always complete.
    A missing unified cache is treated as INCOMPLETE (conservative) — callers
    should refuse destructive/size-scaling action rather than guess.
    """
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    active = (cfg.exchange.paper_execution_mode or "").strip()
    if not active or active == "paper_internal":
        return True, "paper_internal (no exchange view needed)"
    unified = get_unified_cached_state()
    if unified is None:
        return False, "no unified view cached"
    connected = {(a.get("broker_id") or "") for a in (unified.get("accounts") or [])}
    if active in connected:
        return True, f"{active} connected"
    return False, f"{active} not connected (have {sorted(connected) or 'none'})"


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
        # 1. Try spot/account/assets — works for live classic accounts, returns real coin balances.
        # 2. Try mix/account/accounts — futures account, authoritative for classic demo accounts.
        # 3. If both return zero/empty, try UTA v3 API (unified trading account).
        # 4. Each non-USDT coin value is coin_qty * USDT_ticker_price (or usdValue from UTA).
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

        # Mix (futures) account — authoritative total equity for classic demo accounts
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
                # If spot returned nothing (classic demo), use mix USDT as the whole balance
                if not coin_usdt and equity_from_mix > 0:
                    coin_usdt["USDT"] = equity_from_mix
                logger.info("exchange_sync: mix equity=%.4f avail=%.4f", equity_from_mix, usdt_avail)
        except Exception as e:
            logger.info("exchange_sync: mix account skipped (%s), falling back to spot", e)

        # UTA (Unified Trading Account) — fallback when classic spot+mix both return zero.
        # Unified accounts (v3 API) hold multi-coin assets that are invisible to v2 endpoints.
        if not coin_usdt and (equity_from_mix is None or equity_from_mix <= 0):
            try:
                uta_acct = _run_async(client.get_uta_account())
                if uta_acct:
                    uta_equity = float(uta_acct.get("usdtEquity") or 0)
                    uta_unrealised = float(uta_acct.get("usdtUnrealisedPnl") or uta_acct.get("unrealisedPnl") or 0)
                    assets = uta_acct.get("assets") or []
                    if assets:
                        for a in assets:
                            coin = (a.get("coin") or "").upper()
                            usd_val = float(a.get("usdValue") or 0)
                            avail = float(a.get("available") or 0)
                            locked = float(a.get("locked") or 0)
                            if usd_val > 0:
                                coin_usdt[coin] = coin_usdt.get(coin, 0) + usd_val
                            if coin == "USDT":
                                usdt_avail += avail
                                usdt_frozen += locked
                        futures_upnl += uta_unrealised
                        if uta_equity > 0:
                            equity_from_mix = uta_equity
                        logger.info(
                            "exchange_sync: UTA account equity=%.4f assets=%d",
                            uta_equity, len(assets),
                        )
            except Exception as e:
                logger.info("exchange_sync: UTA account skipped (%s)", e)

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
                    # get_positions() returns derivative positions only — tag the
                    # type so symbol display (perp → no slash) stays correct.
                    "market_type": "usdt_futures",
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


def sync_all_exchanges() -> Optional[dict]:
    """Parallel-query all configured brokers via UTA and cache the unified state.

    Also refreshes the Bitget-specific cache so the legacy get_cached_state() path
    stays warm for callers that haven't migrated to get_unified_cached_state() yet.
    Returns the serialised UnifiedState dict, or None on total failure.
    """
    from aletheia.portfolio.uta.manager import get_unified_state_sync

    try:
        sync_exchange_account()
    except Exception as e:
        logger.debug("legacy bitget sync failed (non-fatal): %s", e)

    try:
        unified = get_unified_state_sync()
        payload = {
            "total_equity_usdt": unified.total_equity_usdt,
            "total_available_usdt": unified.total_available_usdt,
            "total_unrealized_pnl": unified.total_unrealized_pnl,
            "accounts": unified.accounts,
            "positions": unified.positions,
            "errors": unified.errors,
            "fetched_at": unified.fetched_at,
        }
        _store_unified(payload)

        # Auto-record initial deposit for newly-seen brokers.
        # Use available_usdt (excludes unrealized PnL) as the deposit amount
        # to avoid inflating the baseline when the account already has open gains.
        try:
            from aletheia.storage.repositories import has_capital_event_for_broker, insert_capital_event
            from aletheia.storage.models import CapitalEvent
            for acct in unified.accounts:
                broker_id = acct.get("broker_id", "")
                available = acct.get("available_usdt", 0)
                equity = acct.get("equity_usdt", 0)
                deposit_amount = available if available > 0 else equity
                if broker_id and deposit_amount > 0 and not has_capital_event_for_broker(broker_id):
                    insert_capital_event(CapitalEvent(
                        broker_id=broker_id,
                        event_type="deposit",
                        amount_usdt=deposit_amount,
                        equity_at_event=equity,
                        note="auto:initial_sync",
                    ))
                    logger.info("capital_events: auto-recorded initial deposit %.2f for %s", deposit_amount, broker_id)
        except Exception as cap_err:
            logger.warning("capital_events auto-record failed: %s", cap_err)

        return payload
    except Exception as e:
        logger.warning("sync_all_exchanges failed: %s", e)
        return None


def _store_unified(payload: dict) -> None:
    import json

    key = f"{_KEY_PREFIX}.unified"
    data = json.dumps(payload)
    execute_write(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, data),
    )

