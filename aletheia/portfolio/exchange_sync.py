"""Exchange Account Sync — cache read-only UTA account projections.

Closes the portfolio-sync-gap (memory `aletheia-portfolio-sync-gap` /
WEBUI_BACKEND_HANDOFF #3): until now `compute_portfolio` only saw locally-managed
positions, so the dashboard hero number was zero even when the real demo account
held 10,000 USDT. This module fetches the truth and caches it.

Governance:
- READ-ONLY against the exchange. This module never modifies external positions and
  never places orders. It also does not, by itself, write externals into the local
  positions table. Adoption of an external position into the local books (trust §1.1,
  AS AMENDED 2026-06-12) happens only via `position_reconciler.adopt_position`, which
  builds a synthetic-but-honest `source="adopted"` causal chain — see
  docs/governance/decisions/DECISIONS.md.
  This module's job is purely to surface the truth so the reconciler can act on it.
- DEGRADES HONESTLY. Missing creds → `unconfigured`. API error → `error` + last
  good cache stays available. Stale cache → `stale`. Never fakes a positive state.
- CACHED. The HTTP API path reads cache only (no synchronous Bitget call); the
  scheduler refreshes it on an interval.
- Stored in the existing `settings` table as a JSON blob (no schema change).
"""

import json
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from aletheia.config.loader import load_config
from aletheia.storage.sqlite import execute_write, get_db

logger = logging.getLogger("aletheia.exchange_sync")

# Settings key namespace; one entry per exchange-mode so demo/live don't collide.
_KEY_PREFIX = "exchange_account_state"

# Default freshness window for the cached state — anything older is reported `stale`.
_DEFAULT_MAX_AGE_SEC = 600


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")



def _settings_key(mode: str) -> str:
    return f"{_KEY_PREFIX}.{mode}"



def _read_raw(mode: str) -> Optional[dict]:
    db = get_db()
    row = db.execute(
        "SELECT value FROM settings WHERE key = ?", (_settings_key(mode),)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"], parse_constant=_reject_non_finite_json)
    except (ValueError, TypeError):
        return {
            "equity_usdt": None,
            "available_usdt": None,
            "frozen_usdt": None,
            "futures_unrealized_pnl": None,
            "total_equity_usdt": None,
            "total_available_usdt": None,
            "total_unrealized_pnl": None,
            "accounts": [],
            "positions": [],
            "errors": [],
            "connected_brokers": [],
            "fetched_at": None,
            "sync_status": "error",
            "sync_complete": False,
            "cache_error": "invalid_cache",
        }


def _age_seconds(fetched_at: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, AttributeError):
        return None


def _numeric_account_value(state: Optional[dict], *keys: str) -> float | None:
    if not isinstance(state, dict):
        return None
    for key in keys:
        if key not in state or state.get(key) is None:
            continue
        try:
            value = float(state[key])
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None
    return None


def account_equity_value(state: Optional[dict]) -> float | None:
    """Return explicit cached equity, preserving zero as a known value."""
    return _numeric_account_value(state, "equity_usdt", "total_equity_usdt")


def account_available_value(state: Optional[dict]) -> float | None:
    """Return explicit cached available balance, preserving zero as a known value."""
    return _numeric_account_value(state, "available_usdt", "total_available_usdt")



def status_for(state: Optional[dict], max_age_sec: int = _DEFAULT_MAX_AGE_SEC) -> str:
    """Classify a cached state for the UI: ok / stale / unconfigured / error."""
    if state is None:
        return "unconfigured"
    if state.get("cache_error") == "invalid_cache":
        return "error"
    age = state.get("age_seconds")
    try:
        age_value = float(age)
    except (TypeError, ValueError):
        return "stale"
    if not math.isfinite(age_value) or age_value < 0 or age_value > max_age_sec:
        return "stale"
    if state.get("sync_status") in {"error", "unconfigured", "generation_mismatch"}:
        return str(state["sync_status"])
    return "ok"


def get_unified_cached_state(*, execution_modes: tuple[str, ...] | None = None) -> Optional[dict]:
    """Return the UTA cache for the requested scope (default: demo accounts)."""
    raw = _read_raw(_unified_cache_scope(execution_modes))
    if raw is None:
        return None
    if raw.get("cache_error") == "invalid_cache":
        return raw
    raw["age_seconds"] = _age_seconds(raw.get("fetched_at", ""))
    if "connected_brokers" in raw:
        raw["connected_brokers"] = list(raw.get("connected_brokers") or [])
    else:
        raw["connected_brokers"] = [
            account.get("broker_id")
            for account in raw.get("accounts", [])
            if account.get("broker_id")
        ]
    identity_current, identity_reason = _unified_identity_is_current(raw)
    if identity_current and _unified_cache_scope(raw.get("execution_modes")) != _unified_cache_scope(execution_modes):
        identity_current, identity_reason = False, "uta_account_scope_mismatch"
    raw["identity_current"] = identity_current
    raw["identity_reason"] = identity_reason
    raw["sync_status"] = _unified_sync_status(raw)
    raw["sync_complete"] = raw["sync_status"] == "ok"
    return raw


def _unified_identity_is_current(state: dict) -> tuple[bool, str]:
    """Generation/profile/adapter mismatch immediately revokes cache authority."""
    scope_value = state.get("execution_modes")
    if not isinstance(scope_value, (list, tuple)):
        return False, "uta_scope_missing"
    if any(not isinstance(mode, str) or not mode.strip() for mode in scope_value):
        return False, "uta_scope_invalid"
    execution_modes = tuple(dict.fromkeys(scope_value))
    if len(execution_modes) != len(scope_value):
        return False, "uta_scope_invalid"
    try:
        from aletheia.uta.manager import build_uta_registry

        cfg = load_config()
        registry = build_uta_registry(cfg, execution_modes=execution_modes)
        expected = {
            identity.execution_mode: identity.model_dump(mode="json")
            for identity in registry.configured_identities()
            if identity.execution_mode in execution_modes
        }
    except Exception:
        return False, "uta_registry_configuration_invalid"
    observed = {
        str(account.get("broker_id") or ""): account
        for account in state.get("accounts", [])
        if isinstance(account, dict) and account.get("broker_id")
    }
    if not set(observed).issubset(set(execution_modes)):
        return False, "uta_account_scope_mismatch"
    if set(observed) != set(expected):
        return False, "uta_account_set_mismatch"
    for execution_mode, expected_identity in expected.items():
        account = observed[execution_mode]
        if account.get("identity") != expected_identity:
            return False, "uta_account_generation_mismatch"
    return True, "current"


def _unified_sync_status(state: dict, max_age_sec: int = 600) -> str:
    age = state.get("age_seconds")
    if age is None:
        age = _age_seconds(state.get("fetched_at", ""))
    if age is None or age > max_age_sec:
        return "stale"
    if state.get("identity_current") is False:
        return "generation_mismatch"
    if state.get("errors"):
        return "error"
    accounts = {
        account.get("broker_id")
        for account in state.get("accounts", [])
        if isinstance(account, dict) and account.get("broker_id")
    }
    if accounts != set(state.get("connected_brokers") or []):
        return "error"
    if not state.get("connected_brokers") and not state.get("accounts"):
        return "unconfigured"
    return "ok"


def active_broker_view_complete(cfg=None) -> tuple[bool, str]:
    """Is the active external demo broker actually present in the unified (multi-broker) view?

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

    Returns (complete, reason). A missing or unsupported account is incomplete.
    A missing unified cache is treated as INCOMPLETE (conservative) — callers
    should refuse destructive/size-scaling action rather than guess.
    """
    from aletheia.config.loader import load_config
    cfg = cfg or load_config()
    from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP

    active = (cfg.exchange.paper_execution_mode or "").strip()
    if active not in {mode for _, mode in EXCHANGE_UNIFICATION_MAP.values()}:
        return False, "execution account not configured"
    unified = get_unified_cached_state()
    if unified is None:
        return False, "no unified view cached"
    if status_for(unified) != "ok":
        return False, f"unified view {unified.get('sync_status') or 'unusable'}"
    connected = {(a.get("broker_id") or "") for a in (unified.get("accounts") or [])}
    if active in connected:
        return True, f"{active} connected"
    return False, f"{active} not connected (have {sorted(connected) or 'none'})"


def sync_all_exchanges(*, execution_modes: tuple[str, ...] | None = None) -> Optional[dict]:
    """Read configured UTA accounts and cache only their exact requested scope."""
    from aletheia.uta.manager import (
        get_unified_state_sync,
    )

    try:
        unified = get_unified_state_sync(execution_modes=execution_modes)
        payload = {
            "total_equity_usdt": unified.total_equity_usdt,
            "total_available_usdt": unified.total_available_usdt,
            "total_unrealized_pnl": unified.total_unrealized_pnl,
            "accounts": unified.accounts,
            "positions": unified.positions,
            "errors": unified.errors,
            "connected_brokers": unified.connected_brokers,
            "fetched_at": unified.fetched_at,
            "execution_modes": list(unified.execution_modes),
        }
        payload["age_seconds"] = _age_seconds(unified.fetched_at)
        payload["identity_current"], payload["identity_reason"] = _unified_identity_is_current(payload)
        payload["sync_status"] = _unified_sync_status(payload)
        payload["sync_complete"] = payload["sync_status"] == "ok"
        _store_unified(payload)

        # Auto-record initial deposit for newly-seen brokers.
        # Use available_usdt (excludes unrealized PnL) as the deposit amount
        # to avoid inflating the baseline when the account already has open gains.
        try:
            from aletheia.storage.repositories import has_capital_event_for_broker, insert_capital_event
            from aletheia.storage.models import CapitalEvent
            for acct in unified.accounts:
                if (
                    acct.get("account_completeness", "complete") != "complete"
                    or acct.get("position_completeness", "complete") != "complete"
                ):
                    continue
                broker_id = acct.get("broker_id", "")
                available = acct.get("available_usdt", 0)
                equity = acct.get("equity_usdt", 0)
                if not isinstance(available, (int, float)) or not isinstance(
                    equity, (int, float)
                ):
                    continue
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


def _unified_cache_scope(execution_modes: tuple[str, ...] | None) -> str:
    from aletheia.uta.manager import DEFAULT_UNIFIED_EXECUTION_MODES

    modes = execution_modes if execution_modes is not None else DEFAULT_UNIFIED_EXECUTION_MODES
    if set(modes) == set(DEFAULT_UNIFIED_EXECUTION_MODES):
        return "unified"
    return "unified." + ",".join(sorted(set(modes)))


def _store_unified(payload: dict) -> None:
    key = _settings_key(_unified_cache_scope(payload.get("execution_modes")))
    data = json.dumps(payload, allow_nan=False)
    execute_write(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, data),
    )
