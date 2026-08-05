"""Position manager — closes open PAPER positions by stop-loss / take-profit / max-hold.

This is the keystone that turns open positions into realized PnL, which in turn
activates the daily-loss breaker (risk/engine.py §8) and the strategy promotion
gates. It only updates the position projection + writes an audit event — it never
places an exchange order, so it lives in portfolio/ (not the change-controlled
execution/).

Governance:
- LIVE positions (bitget_live_limited) are NEVER auto-closed (future + human-only).
- The kill switch does NOT block closing — closing reduces risk.
- No latest price → skip the position (never fabricate an exit).
- The scheduler job that calls this still honors runtime.paused.
"""

import logging
import math
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.price_feed import (
    get_fresh_price_for_protective_close,
    protective_price_venue,
)
from aletheia.storage.repositories import list_all_open_positions
from aletheia.portfolio.exit_plan import ExitPlan, effective_exit_plan, load_exit_plan, save_exit_plan
from aletheia.portfolio.pnl import close_position, _latest_price
logger = logging.getLogger("aletheia.position_manager")

_PAPER_MODES = {"paper_internal", "bitget_demo", "binance_testnet", "okx_demo"}
_OPEN_CLOSE_STATES = {
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing", "overfilled",
}
_PENDING_CLOSE_STALE_HOURS = 4


def _pending_close_age_hours(requested_at: str | None) -> float | None:
    requested = _parse_dt(requested_at)
    if requested is None:
        return float("inf")  # unknown age must not suppress protection silently
    age_hours = (datetime.now(timezone.utc) - requested).total_seconds() / 3600
    return age_hours if age_hours >= 0 else float("inf")


def _escalate_stale_pending_close(position: dict, age_hours: float) -> None:
    """Durably surface a stale close claim without releasing or retrying it."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent

    with repo.transaction(immediate=True):
        existing = repo.get_db().execute(
            "SELECT 1 FROM audit_events WHERE source='position_manager' "
            "AND event_type='pending_close_reconciliation_required' "
            "AND entity_id=? LIMIT 1",
            (position["id"],),
        ).fetchone()
        if existing is not None:
            return
        repo.insert_audit_event(AuditEvent(
            actor="system",
            source="position_manager",
            event_type="pending_close_reconciliation_required",
            entity_type="position",
            entity_id=position["id"],
            message=(
                f"Pending close {position.get('pending_close_order_id')} has remained "
                f"{position.get('pending_close_status')} for {age_hours:.1f}h; "
                "retain the claim and reconcile before any further close action."
            ),
        ))


class LocalPaperClosePreflightError(RuntimeError):
    """No local close was attempted because at least one mark was unavailable."""

    def __init__(self, failures: list[dict]):
        self.failures = failures
        position_ids = ",".join(str(item.get("position_id")) for item in failures)
        super().__init__(f"bulk_close_mark_preflight_failed:{position_ids}")


def _fresh_local_paper_mark(
    position: dict,
    *,
    configured_data_venue: str | None = None,
) -> float:
    """Resolve the only mark allowed to authorize a local-paper close Fill."""
    if position.get("execution_mode") != "paper_internal":
        raise ValueError(
            f"position {position.get('id')} is not paper_internal "
            f"(mode={position.get('execution_mode')!r})"
        )
    if configured_data_venue is None:
        configured_data_venue = load_config().market_data.ccxt_exchange
    venue = protective_price_venue(
        "paper_internal",
        configured_data_venue=configured_data_venue,
    )
    if venue is None:
        raise RuntimeError(
            f"protective_mark_venue_unavailable:{position.get('id')}"
        )
    price = get_fresh_price_for_protective_close(
        position["symbol"],
        position.get("market_type") or "spot",
        venue=venue,
    )
    if price is None:
        raise RuntimeError(
            f"protective_mark_unavailable:{position.get('id')}"
        )
    return float(price)


def _close_local_paper_with_authorized_mark(
    position_id: str,
    mark_price: float,
    *,
    reason: str,
    actor: str,
    source: str,
) -> dict:
    """Materialize a local close from a mark authorized inside this module."""
    from aletheia.execution.paper_broker import close_paper_position

    return close_paper_position(
        position_id,
        mark_price=mark_price,
        reason=reason,
        actor=actor,
        source=source,
    )


def _bulk_close_failure(
    position: dict,
    code: str,
    exc: Exception | None = None,
) -> dict:
    failure = {
        "position_id": position.get("id"),
        "symbol": position.get("symbol"),
        "market_type": position.get("market_type"),
        "code": code,
    }
    if exc is not None:
        failure["error_type"] = type(exc).__name__
        failure["message"] = str(exc)[:200]
    return failure


def close_local_positions_batch(
    market_type: str | None,
    *,
    reason: str = "manual_bulk_close",
    decision_actor: str = "human",
    decision_source: str = "dashboard",
) -> dict:
    """Preflight and close the complete local-paper working set.

    Every exact-instrument mark is resolved before the first close. A missing or
    failed mark therefore rejects the whole batch without any close facts. Once
    preflight succeeds, non-price execution failures may be partial, so both the
    successful and failed identities are returned explicitly.
    """
    candidates = [
        row for row in list_all_open_positions()
        if row.get("execution_mode") == "paper_internal"
        and (market_type is None or row.get("market_type") == market_type)
    ]
    configured_venue = load_config().market_data.ccxt_exchange
    authorized_marks: dict[str, float] = {}
    preflight_failures: list[dict] = []
    for position in candidates:
        try:
            authorized_marks[position["id"]] = _fresh_local_paper_mark(
                position,
                configured_data_venue=configured_venue,
            )
        except Exception as exc:
            code = (
                "protective_mark_unavailable"
                if str(exc).startswith("protective_mark_unavailable:")
                else "protective_mark_lookup_failed"
            )
            preflight_failures.append(
                _bulk_close_failure(position, code, exc)
            )
    if preflight_failures:
        raise LocalPaperClosePreflightError(preflight_failures)

    closed_position_ids: list[str] = []
    failed: list[dict] = []
    for position in candidates:
        try:
            result = _close_local_paper_with_authorized_mark(
                position["id"],
                authorized_marks[position["id"]],
                reason=reason,
                actor=decision_actor,
                source=decision_source,
            )
            if result.get("closed", True):
                closed_position_ids.append(position["id"])
            else:
                failed.append(_bulk_close_failure(
                    position,
                    "close_not_confirmed",
                ))
        except Exception as exc:
            failed.append(_bulk_close_failure(position, "close_failed", exc))

    return {
        "requested_count": len(candidates),
        "closed_position_ids": closed_position_ids,
        "failed": failed,
    }


def close_position_routed(
    position_id: str,
    reason: str,
    fallback_exit_price: float | None = None,
    *,
    allow_live: bool = False,
    decision_reasoning: str | None = None,
    decision_actor: str = "system",
    decision_source: str = "position_lifecycle",
) -> dict:
    """Close a position through the correct path for its execution_mode.

    SINGLE SOURCE OF TRUTH for close routing. Real-exchange modes (demo AND
    live-limited) MUST close via their broker so the exchange position is actually
    reduced and the exit price is the exchange's real fill. A local-only close would
    (a) leave a dangling real position — the §1.3 desync, and on *_live_limited that
    position holds REAL funds — and (b) fabricate the exit from a local mark, breaking
    the "never fabricate an exit" honesty invariant. Only paper_internal closes purely
    locally.

    Used by BOTH the auto-exit loop (evaluate_open_positions) and any manual/agent
    close (e.g. PI portfolio review) so neither can silently desync a real position.

    ``fallback_exit_price`` is retained for call compatibility only. It never
    authorizes a ``paper_internal`` Fill: the local branch always resolves its own
    fresh exact venue/symbol/market mark immediately before materialization.

    LIVE GUARD (governance, see module docstring §LIVE): a `*_live_limited` position is
    human-only — NEVER auto-closed. Because this router is the chokepoint, the guard
    lives HERE: autonomous callers (auto-exit loop, PI portfolio review) leave
    `allow_live=False`, so a live position raises instead of silently placing a REAL
    exchange order (CLAUDE.md: "Never place live orders ... autonomously"). Only an
    explicit human-initiated close passes `allow_live=True`.
    """
    from aletheia.storage.repositories import _select_one

    row = _select_one("positions", position_id)
    mode = (row or {}).get("execution_mode")
    if mode and mode.endswith("_live_limited") and not allow_live:
        raise RuntimeError(
            f"refusing to autonomously close live position {position_id} "
            f"(mode={mode}); live closes are human-only (pass allow_live=True to override)"
        )
    if mode in ("bitget_demo", "bitget_live_limited"):
        from aletheia.execution.bitget_broker import close_demo_position
        result = close_demo_position(position_id, reason=reason)
    elif mode in ("binance_testnet", "binance_live_limited"):
        from aletheia.execution.binance_broker import close_testnet_position
        result = close_testnet_position(position_id, reason=reason)
    elif mode in ("okx_demo", "okx_live_limited"):
        from aletheia.execution.okx_broker import close_okx_position
        result = close_okx_position(position_id, reason=reason)
    elif mode == "paper_internal":
        # paper_internal — pure local simulation; needs an observed mark. The
        # paper broker applies adverse close-side slippage and atomically writes
        # the close Order/Fill with Position and cash settlement.
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        mark_price = _fresh_local_paper_mark(row)
        result = _close_local_paper_with_authorized_mark(
            position_id,
            mark_price,
            reason=reason,
            actor=decision_actor,
            source=decision_source,
        )
    else:
        raise ValueError(
            f"unsupported position execution_mode for close: {mode!r}"
        )

    # Only overwrite the generic factual explanation after a confirmed close.
    # This deliberately sits outside the broker dispatch: no exchange adapter or
    # order semantics change, and a failed exchange close cannot look complete.
    if decision_reasoning:
        from aletheia.portfolio.pnl import persist_exit_reasoning
        persist_exit_reasoning(
            position_id,
            actor=decision_actor,
            source=decision_source,
            reason=reason,
            reasoning=decision_reasoning,
            is_ai_judgment=decision_actor == "system_operator",
            trigger={"trigger_type": "operator_review"} if decision_actor == "system_operator" else None,
        )
    return result


def reduce_position_routed(position_id: str, reduce_qty: float, reason: str = "operator_reduce") -> dict:
    """Fail closed for partial reduce; retain full-exit delegation.

    The isolated OKX helper is a mock-tested reference, not an authorized product
    path. It has no active Operator trigger, attributable Proposal/RiskDecision
    chain, venue-confirmed partial-fill contract, or TP/SL bracket resync.
    """
    from aletheia.storage.repositories import _select_one

    row = _select_one("positions", position_id)
    if row is None or row.get("status") != "open":
        return {"error": "position_not_found_or_not_open", "position_id": position_id}
    mode = row.get("execution_mode")
    qty = float(row.get("quantity") or 0.0)
    reduce_qty = float(reduce_qty)
    if reduce_qty <= 0:
        return {"error": "reduce_qty_must_be_positive", "position_id": position_id}
    if reduce_qty >= qty:
        # A "reduce" that's actually a full exit → route through the normal close path.
        return close_position_routed(position_id, reason=reason)
    return {
        "error": "partial_reduce_not_production_authorized",
        "position_id": position_id,
        "execution_mode": mode,
        "reduce_qty": reduce_qty,
        "hint": (
            "active product supports full closes only; partial reduce requires an "
            "attributable Proposal, RiskDecision, confirmed fill, and TP/SL resync"
        ),
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def _native_proposal_protection(
    position: dict,
    proposal_field: str,
) -> tuple[bool, float | None]:
    """Return whether immutable Proposal authority governs a missing level.

    Native positions may use a persisted trailing stop after entry, so callers
    first honor a non-NULL Position value.  When that value is missing, however,
    mutable config must not invent protection that Risk never approved.  Recover
    the original Proposal value when present; an explicit NULL stays NULL.

    Adopted or pre-Proposal legacy positions keep their historical config
    fallback because they did not originate from a native Risk-approved intent.
    """
    proposal_id = position.get("proposal_id")
    if not proposal_id:
        return False, None
    if position.get("source") == "adopted":
        return False, None

    try:
        from aletheia.storage.repositories import get_trade_proposal

        proposal = get_trade_proposal(str(proposal_id))
    except Exception:
        # A native identity whose authority cannot be read must fail closed.
        return True, None
    if proposal is None:
        return True, None
    if proposal.get("source") == "adopted":
        return False, None
    value = proposal.get(proposal_field)
    return True, float(value) if value is not None else None


def _effective_stop(position: dict, cfg) -> float | None:
    stop = position.get("stop_price")
    if stop:
        return float(stop)
    governed, proposal_stop = _native_proposal_protection(position, "stop_price")
    if governed:
        return proposal_stop
    pct = cfg.trading.default_stop_loss_pct
    entry = position["entry_price"]
    if not pct or entry <= 0:
        return None
    if position["direction"] in ("long", "buy"):
        return round(entry * (1 - pct / 100.0), 8)
    return round(entry * (1 + pct / 100.0), 8)


def _effective_take_profit(position: dict, cfg) -> float | None:
    tp = position.get("take_profit_price")
    if tp:
        return float(tp)
    governed, proposal_tp = _native_proposal_protection(position, "take_profit")
    if governed:
        return proposal_tp
    pct = cfg.trading.default_take_profit_pct
    entry = position["entry_price"]
    if not pct or entry <= 0:
        return None
    if position["direction"] in ("long", "buy"):
        return round(entry * (1 + pct / 100.0), 8)
    return round(entry * (1 - pct / 100.0), 8)


def _get_atr_pct(symbol: str) -> float | None:
    """Return latest ATR% for a symbol from universe_feature_snapshots, or None.

    universe_feature_snapshots uses asset_id (FK → universe_registry) + ts,
    not symbol + created_at. We join through universe_registry to filter by symbol.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT ufs.atr_pct FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ufs.asset_id = ur.id "
            "WHERE ur.symbol = ? "
            "ORDER BY ufs.ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row:
            val = row[0] if not hasattr(row, "get") else row.get("atr_pct")
            if val and float(val) > 0:
                return float(val)
    except Exception:
        pass
    return None


def _resolve_trailing(position: dict, cfg) -> dict | None:
    """Resolve the effective trailing config for a position (M4/D2).

    Operator-authored ExitPlan.trailing overrides the global default; mode 'off'
    disables trailing; absence falls back to the global default (D2=A) unless
    trading.trailing_requires_authoring is set (D2=B). Returns a resolved dict
    {mode, atr_mult, pct, activation_profit_pct, floor_at_breakeven} or None to skip.
    """
    trading = cfg.trading
    spec = load_exit_plan(position).trailing
    if spec is not None:
        mode = str(spec.get("mode") or "").lower()
        if mode in ("off", "none", "disabled") or spec.get("enabled") is False:
            return None
        if mode not in ("atr", "pct"):
            mode = "atr" if _get_atr_pct(position.get("symbol", "")) else "pct"
        distance = spec.get("distance")
        return {
            "mode": mode,
            "atr_mult": float(distance) if (mode == "atr" and distance is not None) else trading.trail_atr_mult,
            "pct": float(distance) if (mode == "pct" and distance is not None) else trading.trail_pct,
            "activation_profit_pct": spec.get("activation_profit_pct"),
            "floor_at_breakeven": bool(spec.get("floor_at_breakeven", True)),
        }
    # Not operator-authored.
    if getattr(trading, "trailing_requires_authoring", False):
        return None  # D2=B: no default trailing without operator authoring
    if not trading.trail_enabled:
        return None  # D2=A: global default off
    return {
        "mode": "atr" if _get_atr_pct(position.get("symbol", "")) else "pct",
        "atr_mult": trading.trail_atr_mult,
        "pct": trading.trail_pct,
        "activation_profit_pct": None,
        "floor_at_breakeven": True,
    }


def _maybe_update_trailing_stop(position: dict, price: float, cfg) -> None:
    """Update stop_price for trailing stop and breakeven logic. Writes DB on change.

    Called before _decide_exit() so the updated stop is evaluated this cycle.

    M4/D2: trailing is AUTHORED on the ExitPlan — _resolve_trailing decides the
    effective params (operator override / global default / off). When a position has
    no trailing spec and the global default is on, behavior is identical to before.

    Serenity school skips trailing/breakeven entirely: long-horizon holdings rely
    on the wider fixed stop_loss_pct and a multi-month max_hold, not on intra-day
    ATR trails that would shake the position out of a research-grade entry.
    """
    if position.get("trade_school") == "serenity":
        return
    tc = _resolve_trailing(position, cfg)
    if tc is None:
        return
    direction = position["direction"]
    is_long = direction in ("long", "buy")
    entry = float(position["entry_price"])
    current_stop = position.get("stop_price")
    if current_stop is not None:
        current_stop = float(current_stop)
    else:
        governed, proposal_stop = _native_proposal_protection(
            position,
            "stop_price",
        )
        if governed and proposal_stop is None:
            # A mutable trailing configuration cannot invent protection that
            # the immutable native Proposal never asked Risk to approve.
            return
        if governed:
            current_stop = proposal_stop

    profit_pct = 0.0
    if entry > 0:
        profit_pct = ((price - entry) / entry * 100.0) if is_long else ((entry - price) / entry * 100.0)
    # Activation gate: an operator-authored trail can defer until a profit threshold.
    if tc["activation_profit_pct"] is not None and profit_pct < tc["activation_profit_pct"]:
        return

    # 1. Breakeven move (opt-out via floor_at_breakeven): floor stop at entry when
    #    profit clears breakeven_trigger_pct.
    if tc["floor_at_breakeven"] and current_stop is not None and entry > 0:
        if profit_pct >= cfg.trading.breakeven_trigger_pct:
            if (is_long and current_stop < entry) or (not is_long and current_stop > entry):
                from aletheia.storage.repositories import update_position
                update_position(position["id"], stop_price=entry)
                position["stop_price"] = entry
                current_stop = entry

    # 2. Trailing stop: keep stop within trail_distance of best seen price
    high_water = position.get("trail_high_water")
    high_water = float(high_water) if high_water is not None else price

    if tc["mode"] == "atr":
        atr_pct = _get_atr_pct(position.get("symbol", ""))
        trail_distance = price * (atr_pct / 100.0) * tc["atr_mult"] if atr_pct else price * (tc["pct"] / 100.0)
    else:
        trail_distance = price * (tc["pct"] / 100.0)

    updates: dict = {}
    if is_long:
        new_hw = max(high_water, price)
        new_stop = new_hw - trail_distance
        if current_stop is None or new_stop > current_stop:
            updates["stop_price"] = round(new_stop, 8)
            position["stop_price"] = updates["stop_price"]
        if new_hw != high_water:
            updates["trail_high_water"] = round(new_hw, 8)
    else:
        new_hw = min(high_water, price)
        new_stop = new_hw + trail_distance
        if current_stop is None or new_stop < current_stop:
            updates["stop_price"] = round(new_stop, 8)
            position["stop_price"] = updates["stop_price"]
        if new_hw != high_water:
            updates["trail_high_water"] = round(new_hw, 8)

    if updates:
        from aletheia.storage.repositories import update_position
        update_position(position["id"], **updates)
        # Sync the exchange TPSL bracket when the stop price changes.
        # #139 fix: dispatch broker-agnostically through execution.stops so OKX
        # and (eventually) Binance also honour the trailed stop, not just Bitget.
        # `replace_exchange_protective_stop` re-checks exchange_tpsl_enabled +
        # mode gating internally so the trail path stays honest per broker.
        new_stop = updates.get("stop_price")
        if new_stop:
            try:
                from aletheia.execution.stops import replace_exchange_protective_stop
                replace_exchange_protective_stop(position["id"], float(new_stop))
            except Exception as e:
                logger.warning("exchange stop sync failed pos=%s: %s", position["id"], e)


def _position_risk(position: dict, cfg):
    # Some close-path callers deliberately provide a minimal configuration
    # object with no risk subtree (for example deterministic materialization
    # fixtures). Preserve the pre-policy behavior for those callers: no
    # additional Layer-0 policy is available. Real AletheiaConfig instances
    # always have cfg.risk and still resolve/fail closed by execution mode.
    if not hasattr(cfg, "risk"):
        return None
    if not hasattr(cfg.risk, "demo") or not hasattr(cfg.risk, "live"):
        # Legacy/unit callers may pass only the flat risk attributes exercised
        # by this close-path helper. Treat that object as the compatibility
        # view; it has no execution authority and is never used for proposal
        # approval.
        return cfg.risk
    from aletheia.risk.policy import (
        UnknownRiskExecutionMode,
        resolve_risk_policy,
    )
    try:
        resolved = resolve_risk_policy(
            cfg,
            str(position.get("execution_mode") or ""),
        )
        return resolved.legacy_view(cfg.risk)
    except UnknownRiskExecutionMode:
        # Historical rows with unknown authority receive the conservative Live
        # policy rather than silently falling into Demo.
        from aletheia.risk.policy import RiskPolicyView
        return RiskPolicyView(cfg.risk.live, cfg.risk)


def _catastrophic_stop_pct(position: dict, cfg) -> float:
    """Max adverse move (%) beyond which a position is force-closed regardless of its
    own stop — the Layer-0 backstop that makes handing stop discretion to the System
    Operator safe (exit-intent plan M6/D4). School-aware. 0.0 when disabled."""
    risk = _position_risk(position, cfg)
    if risk is None or not getattr(risk, "catastrophic_stop_enabled", True):
        return 0.0
    if position.get("trade_school") == "serenity":
        return float(getattr(risk, "catastrophic_stop_pct_serenity", 15.0) or 0.0)
    return float(getattr(risk, "catastrophic_stop_pct", 10.0) or 0.0)


def _layer0_safety_exit(position: dict, price: float, cfg) -> tuple[str, float] | None:
    """Layer-0 hard safety: force-close BEFORE any strategy exit, not relaxable by an
    ExitPlan. Order: catastrophic drawdown, then (leveraged) liquidation proximity.
    Returns (reason, exit_price) or None. Pure — reads only position + config + price.
    """
    is_long = position["direction"] in ("long", "buy")
    entry = float(position.get("entry_price") or 0.0)
    if entry <= 0 or price <= 0:
        return None

    # 1. Catastrophic drawdown from entry (school-aware hard line).
    pct = _catastrophic_stop_pct(position, cfg)
    if pct > 0:
        loss_pct = ((entry - price) / entry * 100.0) if is_long else ((price - entry) / entry * 100.0)
        if loss_pct >= pct:
            return "catastrophic_stop", price

    # 2. Runtime liquidation proximity — leveraged positions only. Emergency de-risk
    #    when current price is within runtime_liquidation_buffer_pct of est. liq price.
    risk = _position_risk(position, cfg)
    if getattr(risk, "runtime_liquidation_monitor_enabled", True):
        leverage = int(position.get("leverage") or 0)
        if leverage > 1:
            from aletheia.trading.instruments import calc_liquidation_price
            liq = calc_liquidation_price(entry, position["direction"], leverage, position.get("symbol", ""))
            if liq is not None and liq > 0:
                buffer_pct = float(getattr(risk, "runtime_liquidation_buffer_pct", 3.0) or 0.0)
                remaining_pct = ((price - liq) / price * 100.0) if is_long else ((liq - price) / price * 100.0)
                if remaining_pct <= buffer_pct:
                    return "liquidation_proximity", price
    return None


def _decide_exit(position: dict, price: float, cfg) -> tuple[str, float] | None:
    """Return (reason, exit_price) if the position should close, else None."""
    is_long = position["direction"] in ("long", "buy")
    # SL/TP resolve through the ExitPlan abstraction (operator-authored levels where
    # present, config defaults otherwise). effective_exit_plan reproduces the exact
    # numbers _effective_stop / _effective_take_profit returned, so this is a
    # behavior-preserving swap (M1 equivalence spec) that lets authored levels flow.
    plan = effective_exit_plan(position, cfg)
    stop = plan.stop_price
    tp = plan.target_price

    if stop is not None:
        if (is_long and price <= stop) or (not is_long and price >= stop):
            return "stop_loss", stop
    # Upside exit: an author-placed limit exit (M3.5) REPLACES the take-profit — the
    # author deliberately chose this exit level, so it overrides the auto/default TP
    # (otherwise a lower default TP would pre-empt the chosen level on the way up).
    # Downside is still protected by the stop checked above → local OCO, stop wins ties.
    elp = position.get("exit_limit_price")
    if elp:
        elp = float(elp)
        if (is_long and price >= elp) or (not is_long and price <= elp):
            # Attribute the fill to whoever authored the level. A human's manual limit
            # must not be reported as AI exit discretion; NULL means the only author
            # that existed before exit_limit_source was persisted (system_operator).
            if position.get("exit_limit_source") == "human":
                return "human_limit_exit", elp
            return "operator_limit_exit", elp
    elif tp is not None:
        if (is_long and price >= tp) or (not is_long and price <= tp):
            return "take_profit", tp

    # Hard time-stop. The school-aware max_hold (serenity max_hold_days*24, else
    # trading.max_position_hold_hours) is the legacy absolute cap. When the Operator
    # authored an expected_horizon, the AUTO-CLOSE moves out to the far backstop
    # min(horizon * N, school_max); hitting the horizon itself only WAKES the Operator
    # (see _maybe_wake_on_horizon) — it no longer auto-closes (D3).
    if position.get("trade_school") == "serenity":
        school_max = (cfg.serenity.max_hold_days or 0) * 24
    else:
        school_max = cfg.trading.max_position_hold_hours
    horizon = plan.expected_horizon_hours
    if horizon and horizon > 0:
        risk = _position_risk(position, cfg)
        mult = getattr(risk, "horizon_hard_backstop_mult", 3.0) or 3.0
        hard_hold = horizon * mult
        if school_max and school_max > 0:
            hard_hold = min(hard_hold, school_max)
    else:
        hard_hold = school_max
    if hard_hold and hard_hold > 0:
        opened = _parse_dt(position.get("opened_at"))
        if opened is not None:
            age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
            if age_hours >= hard_hold:
                return "max_hold", price

    # Thesis-invalidation exit (serenity only): default auto-close (D5 option 2). When
    # thesis_auto_close is disabled, the loop wakes the Operator to review/veto instead
    # of auto-closing here (see _maybe_wake_on_thesis).
    if getattr(cfg.trading, "thesis_auto_close", True) and _thesis_invalidated(position):
        return "thesis_invalidated", price

    return None


def _thesis_invalidated(position: dict) -> bool:
    """True if a serenity position's underlying research opportunity is no longer
    active (dismissed / expired / downgraded)."""
    if position.get("trade_school") != "serenity" or not position.get("opportunity_id"):
        return False
    from aletheia.storage.repositories import get_serenity_opportunity
    opp = get_serenity_opportunity(position["opportunity_id"])
    return bool(opp and opp.get("status") != "active")


def _emit_wake(
    position: dict,
    kind: str,
    payload: dict,
    source: str,
    priority: int = -1,
) -> bool:
    """Emit a per-position WakeEvent to the System Operator daemon (best-effort). The
    daemon coalesces + rate-limits (M1.5); priority -1 → derived from the kind.
    Return whether delivery to the daemon event loop was scheduled."""
    try:
        import time
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
        from aletheia.agent_runtime.wake_events import WakeEvent
        delivered = notify_pi_daemon(
            source=source,
            event=WakeEvent(
                position_id=position["id"], kind=kind, ts=time.monotonic(),
                payload=payload, priority=priority,
            ),
        )
        if not delivered:
            logger.warning(
                "wake emit unavailable pos=%s kind=%s", position.get("id"), kind
            )
        return bool(delivered)
    except Exception as e:
        logger.warning("wake emit failed pos=%s kind=%s: %s", position.get("id"), kind, e)
        return False


def _maybe_wake_on_thesis(position: dict, cfg) -> bool:
    """D5 review path: when thesis_auto_close is OFF, wake the Operator on thesis
    invalidation instead of auto-closing. Rate-limited by the daemon token bucket."""
    if getattr(cfg.trading, "thesis_auto_close", True):
        return False  # default path auto-closes in _decide_exit
    if not _thesis_invalidated(position):
        return False
    from aletheia.agent_runtime.wake_events import THESIS_INVALIDATED
    delivered = _emit_wake(
        position, THESIS_INVALIDATED,
        {"symbol": position.get("symbol"), "opportunity_id": position.get("opportunity_id")},
        source="thesis_review",
    )
    if delivered:
        logger.info(
            "thesis_review wake: %s opp=%s",
            position.get("symbol"),
            position.get("opportunity_id"),
        )
    return delivered


def _maybe_wake_on_horizon(position: dict) -> bool:
    """When a position passes its Operator-authored expected_horizon, wake the Operator
    ONCE for a must-answer decision (close / extend) instead of auto-closing (D3).

    Side-effecting (emits a WakeEvent + stamps horizon_wake_sent_at), so it lives in
    the loop rather than the pure _decide_exit. Returns True if a wake was emitted.
    The far backstop in _decide_exit still auto-closes if the Operator never responds.
    """
    horizon = position.get("expected_horizon_hours")
    try:
        horizon = float(horizon) if horizon is not None else None
    except (TypeError, ValueError):
        horizon = None
    if not horizon or horizon <= 0:
        return False
    if position.get("horizon_wake_sent_at"):
        return False  # already woke once for this horizon
    opened = _parse_dt(position.get("opened_at"))
    if opened is None:
        return False
    age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
    if age_hours < horizon:
        return False

    try:
        import time
        from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
        from aletheia.agent_runtime.wake_events import HORIZON_DUE, WakeEvent
        notify_pi_daemon(
            source="horizon_due",
            event=WakeEvent(
                position_id=position["id"],
                kind=HORIZON_DUE,
                ts=time.monotonic(),
                payload={
                    "symbol": position.get("symbol"),
                    "age_hours": round(age_hours, 2),
                    "horizon_hours": horizon,
                },
            ),
        )
    except Exception as e:
        logger.warning("horizon wake emit failed %s: %s", position.get("id"), e)

    from aletheia.storage.repositories import update_position
    update_position(position["id"], horizon_wake_sent_at=datetime.now(timezone.utc).isoformat())
    logger.info(
        "horizon_due: %s age=%.1fh horizon=%.1fh — woke Operator (must-answer)",
        position.get("symbol"), age_hours, horizon,
    )
    return True

def evaluate_open_positions() -> dict:
    """Evaluate all open paper positions and manage triggered lifecycle rules.

    Price-based safety rules may close positions. Market-aware evaluation only
    requests an Operator review and reports a failed request explicitly.
    """
    cfg = load_config()
    closed = 0
    checked = 0
    market_exits = 0
    failed_close_count = 0
    errors: list[dict] = []
    market_signal_wakes = 0
    catastrophic_exits = 0
    def record_error(position: dict, code: str, exc: Exception | None = None) -> None:
        item = {
            "position_id": position["id"],
            "symbol": position.get("symbol"),
            "market_type": position.get("market_type"),
            "code": code,
        }
        if exc is not None:
            item["error_type"] = type(exc).__name__
            item["message"] = str(exc)[:200]
        errors.append(item)

    # NOTE (P0 defuse, 2026-07-02): RuleSet exit signals no longer auto-close
    # positions. Exit authority is SL/TP/max_hold/System Operator only — a
    # deterministic RuleSet must not override PI autonomy (PI-central, Option A).
    # The former ruleset exit-scan path had zero live effect (0/208 closes) but
    # was a wired-but-dormant bypass. Locked by spec PL-PM-08.

    # Build market signal index once per cycle (batch query, not per-position)
    market_signal_index: dict = {}
    if cfg.trading.market_aware_exits.enabled:
        try:
            from aletheia.portfolio.market_signal_reader import get_market_signals_for_positions
            all_open = list_all_open_positions()
            syms = list({p["symbol"] for p in all_open if p.get("execution_mode") in _PAPER_MODES})
            if syms:
                market_signal_index = get_market_signals_for_positions(
                    syms, max_signal_age_min=cfg.trading.market_aware_exits.max_signal_age_min,
                )
        except Exception as e:
            logger.warning("market_signal_index build failed: %s", e)

    for p in list_all_open_positions():
        if p.get("execution_mode") not in _PAPER_MODES:
            continue
        checked += 1
        if p.get("pending_close_status") in _OPEN_CLOSE_STATES:
            age_hours = _pending_close_age_hours(p.get("pending_close_requested_at"))
            if age_hours is not None and age_hours >= _PENDING_CLOSE_STALE_HOURS:
                _escalate_stale_pending_close(p, age_hours)
                errors.append({
                    "position_id": p["id"],
                    "symbol": p.get("symbol"),
                    "market_type": p.get("market_type"),
                    "code": "pending_close_reconciliation_required",
                })
                logger.warning(
                    "pending close requires reconciliation for %s: order=%s status=%s age=%.1fh",
                    p["id"], p.get("pending_close_order_id"),
                    p.get("pending_close_status"), age_hours,
                )
            else:
                logger.info(
                    "skip close for %s: pending close order %s status=%s",
                    p["id"], p.get("pending_close_order_id"),
                    p.get("pending_close_status"),
                )
            # Never release an unresolved close claim or submit a replacement.
            # The scheduler's read-only reconciliation job owns convergence.
            continue
        try:
            price_venue = protective_price_venue(
                p.get("execution_mode"),
                configured_data_venue=cfg.market_data.ccxt_exchange,
            )
            price = get_fresh_price_for_protective_close(
                p["symbol"],
                p.get("market_type") or "spot",
                venue=price_venue or "",
            )
        except Exception as e:
            logger.warning("protective mark lookup failed %s: %s", p["id"], e)
            record_error(p, "protective_mark_lookup_failed", e)
            continue
        if price is None:
            record_error(p, "protective_mark_unavailable")
            continue

        # Layer-0 hard safety (catastrophic stop, liquidation proximity) runs FIRST
        # and pre-empts trailing / strategy / market-aware. Not relaxable by intent.
        decision = _layer0_safety_exit(p, float(price), cfg)
        if decision is not None:
            logger.warning(
                "layer0_safety_exit: %s reason=%s entry=%s price=%s lev=%s school=%s",
                p["symbol"], decision[0], p.get("entry_price"), price,
                p.get("leverage"), p.get("trade_school"),
            )
            catastrophic_exits += 1
        else:
            _maybe_update_trailing_stop(p, float(price), cfg)
            decision = _decide_exit(p, float(price), cfg)
            # Horizon reached but not yet at the hard backstop → wake the Operator
            # for a must-answer decision instead of auto-closing (D3).
            if decision is None:
                _maybe_wake_on_horizon(p)
                _maybe_wake_on_thesis(p, cfg)

        # Market-aware (M5): a counter-signal now WAKES the Operator to decide (close /
        # adjust / hold) instead of auto-closing or auto-tightening the stop. The daemon
        # token bucket rate-limits per position. Off by default (enabled flag).
        if decision is None and cfg.trading.market_aware_exits.enabled:
            signal = market_signal_index.get(p["symbol"])
            if signal is not None:
                try:
                    from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
                    from aletheia.agent_runtime.wake_events import ADVERSE_SIGNAL, adverse_signal_priority
                    action = evaluate_market_exit_rules(p, float(price), signal, cfg)
                    if action.action in ("urgent_exit", "tighten_stop"):
                        score = getattr(signal, "confidence_score", None)
                        delivered = _emit_wake(
                            p, ADVERSE_SIGNAL,
                            {"reason": action.reason, "score": score,
                             "suggested": action.action, "symbol": p.get("symbol")},
                            source="market_adverse_signal",
                            priority=adverse_signal_priority(score),
                        )
                        from aletheia.storage.repositories import update_position
                        if delivered:
                            market_signal_wakes += 1
                            update_position(
                                p["id"],
                                last_market_signal_action=f"wake:{action.action}",
                                last_market_signal_reason=action.reason[:200],
                                last_market_signal_at=datetime.now(timezone.utc).isoformat(),
                            )
                            logger.info(
                                "market_adverse_wake: %s action=%s reason=%s",
                                p["symbol"], action.action, action.reason,
                            )
                        else:
                            update_position(
                                p["id"],
                                last_market_signal_action=f"wake_failed:{action.action}",
                                last_market_signal_reason="operator_daemon_unavailable",
                                last_market_signal_at=datetime.now(timezone.utc).isoformat(),
                            )
                            record_error(p, "operator_wake_unavailable")
                except Exception as e:
                    logger.warning("market_aware_exit failed %s: %s", p["id"], e)
                    record_error(p, "market_aware_evaluation_failed", e)

        if decision is None:
            continue
        reason, exit_price = decision
        try:
            # Route through the shared helper so the exchange leg is never skipped
            # (live positions are already filtered out above; demo modes still need
            # their real exchange close, paper closes locally).
            close_result = close_position_routed(p["id"], reason, fallback_exit_price=exit_price)
            if close_result.get("closed", True):
                closed += 1
        except Exception as e:
            logger.warning("close failed for %s: %s", p["id"], e)
            failed_close_count += 1
            record_error(p, "close_failed", e)
    return {
        "checked": checked,
        "closed": closed,
        "market_exits": market_exits,
        "failed_close_count": failed_close_count,
        "errors": errors,
        "market_signal_wakes": market_signal_wakes,
        "catastrophic_exits": catastrophic_exits,
    }


def adjust_stop(position_id: str, new_stop_price: float) -> dict:
    """Update the stop-loss price for an open position.

    PI instruction 'adjust stop to X' lands here (RE-S05). Writes stop_price to
    DB via update_position and syncs the exchange TPSL bracket when applicable.
    Returns {"updated": True} on success, {"error": str} on failure.
    """
    from aletheia.storage.repositories import get_position, update_position

    pos = get_position(position_id)
    if pos is None or pos.get("status") != "open":
        return {"error": f"position {position_id} not found or not open"}

    update_position(position_id, stop_price=round(float(new_stop_price), 8))
    logger.info("adjust_stop: %s new_stop=%.6f", position_id, new_stop_price)

    cfg = load_config()
    if cfg.trading.exchange_tpsl_enabled and pos.get("execution_mode") in ("bitget_demo", "bitget_live_limited"):
        try:
            from aletheia.execution.bitget_broker import replace_exchange_stop
            replace_exchange_stop(position_id, float(new_stop_price))
        except Exception as e:
            logger.warning("exchange stop sync failed pos=%s: %s", position_id, e)

    return {"updated": True, "position_id": position_id, "new_stop_price": new_stop_price}


def _sync_exchange_stop(position_id: str, new_stop: float, position: dict, cfg) -> bool:
    """Push a revised stop to the exchange TPSL bracket (bitget). Returns True if synced."""
    if cfg.trading.exchange_tpsl_enabled and position.get("execution_mode") in (
        "bitget_demo", "bitget_live_limited",
    ):
        try:
            from aletheia.execution.bitget_broker import replace_exchange_stop
            replace_exchange_stop(position_id, float(new_stop))
            return True
        except Exception as e:
            logger.warning("exchange stop sync failed pos=%s: %s", position_id, e)
    return False


def _stop_beyond_catastrophic(position: dict, new_stop: float, cfg) -> bool:
    """True if new_stop is LOOSER than the Layer-0 catastrophic line (nonsensical — the
    catastrophic force-close would fire first). Used to reject bad operator adjustments."""
    pct = _catastrophic_stop_pct(position, cfg)
    entry = float(position.get("entry_price") or 0.0)
    if pct <= 0 or entry <= 0:
        return False
    if position.get("direction") in ("long", "buy"):
        return new_stop < entry * (1 - pct / 100.0)
    return new_stop > entry * (1 + pct / 100.0)


def apply_operator_adjustment(
    position_id: str,
    *,
    new_stop: float | None = None,
    new_target: float | None = None,
    new_horizon: float | None = None,
    new_trailing: dict | None = None,
    reason: str | None = None,
) -> dict:
    """System Operator revises an open position's ExitPlan WITHOUT closing it — the M3
    pivot that lets the AI manage a position, not just open/close it.

    Validates the new stop against the Layer-0 catastrophic bound (a stop looser than
    catastrophic is rejected), bumps the ExitPlan version, re-marks provenance
    'operator', re-arms the horizon wake when the horizon changed, syncs the exchange
    stop bracket, and writes an audit event. Returns a result dict.
    """
    from aletheia.storage.repositories import get_position, update_position

    cfg = load_config()
    # Fetch by durable identity rather than scanning a capped list: the previous
    # list_positions(status="open", limit=500) scan silently reported a legitimately
    # open position as missing once more than 500 positions were open.
    pos = get_position(position_id)
    if pos is None or pos.get("status") != "open":
        return {"error": "position_not_found_or_not_open", "position_id": position_id}

    if new_stop is not None:
        new_stop = round(float(new_stop), 8)
        if _stop_beyond_catastrophic(pos, new_stop, cfg):
            return {"error": "stop_looser_than_catastrophic", "position_id": position_id, "new_stop": new_stop}

    plan = load_exit_plan(pos)
    old_stop = plan.stop_price
    horizon_changed = new_horizon is not None and float(new_horizon) != (plan.expected_horizon_hours or -1.0)
    new_plan = ExitPlan(
        stop_price=new_stop if new_stop is not None else plan.stop_price,
        target_price=round(float(new_target), 8) if new_target is not None else plan.target_price,
        expected_horizon_hours=float(new_horizon) if new_horizon is not None else plan.expected_horizon_hours,
        trailing=new_trailing if new_trailing is not None else plan.trailing,
        thesis_invalidation=plan.thesis_invalidation,
        provenance="operator",
        version=(plan.version or 0) + 1,
    )
    save_exit_plan(position_id, new_plan)
    if horizon_changed:
        # A re-authored horizon should be allowed to wake the Operator again.
        update_position(position_id, horizon_wake_sent_at=None)

    synced = False
    if new_stop is not None and (old_stop is None or new_stop != round(float(old_stop), 8)):
        synced = _sync_exchange_stop(position_id, new_stop, pos, cfg)

    logger.info(
        "adjust_position: %s v%d stop=%s target=%s horizon=%s synced=%s reason=%s",
        position_id, new_plan.version, new_plan.stop_price, new_plan.target_price,
        new_plan.expected_horizon_hours, synced, (reason or "")[:80],
    )
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system_operator",
            "source": "position_lifecycle",
            "event_type": "operator_adjust_position",
            "entity_type": "position",
            "entity_id": position_id,
            "after": {
                "stop_price": new_plan.stop_price,
                "take_profit_price": new_plan.target_price,
                "expected_horizon_hours": new_plan.expected_horizon_hours,
                "version": new_plan.version,
            },
            "message": (reason or "operator adjusted exit plan")[:200],
        })
    except Exception:
        pass

    return {
        "updated": True,
        "position_id": position_id,
        "version": new_plan.version,
        "exchange_synced": synced,
    }


def proactive_exit(
    position_id: str,
    *,
    order_type: str = "market",
    limit_price: float | None = None,
    reason: str | None = None,
    actor: str = "system_operator",
) -> dict:
    """Operator proactively exits a position ahead of its passive stop/target
    (exit-intent plan M3.5). ``market`` closes now through the routed path (which
    cancels the exchange bracket on demo/live). ``limit`` rests a locally-watched
    limit exit at ``limit_price`` — the executor market-closes when price reaches it,
    OCO with the stop (whichever level price hits first wins).

    A true resting reduce-only limit order + exchange-native OCO is a follow-on
    refinement; the locally-watched limit works uniformly across paper/demo/live and
    needs no untested broker order path.

    ``actor`` distinguishes the two legitimate authors of a proactive exit and is the
    ONLY thing that decides attribution: ``system_operator`` is the AI's own
    discretionary exit (``operator_close`` / ``operator_limit_exit``), ``human`` is an
    owner-initiated dashboard close (``human_manual_close`` / ``human_limit_exit``).
    A human close must never be recorded as AI exit discretion, because
    ``analytics/exit_driver_comparison.py`` reads ``close_reason`` to judge whether
    AI-discretionary exits beat deterministic rules.
    """
    from aletheia.storage.repositories import get_position, update_position

    if actor not in ("system_operator", "human"):
        return {"error": "unsupported_exit_actor", "position_id": position_id, "actor": actor}

    # Fetch by durable identity: the previous list_positions(limit=500) scan silently
    # failed to find a legitimately open position once more than 500 were open.
    pos = get_position(position_id)
    if pos is None or pos.get("status") != "open":
        return {"error": "position_not_found_or_not_open", "position_id": position_id}

    is_human = actor == "human"

    if (order_type or "market") == "limit":
        # Issue #416: the locally-watched limit executor (evaluate_open_positions)
        # only processes _PAPER_MODES positions.  A live-limited position that
        # accepts a limit exit is persisted as pending_limit_exit=True but nothing
        # can ever execute it.  Reject limit exits on live-limited positions so the
        # operator knows they must use market or close from the exchange UI.
        exec_mode = pos.get("execution_mode", "")
        if exec_mode.endswith("_live_limited"):
            return {
                "error": "limit_exit_not_supported_for_live",
                "position_id": position_id,
                "execution_mode": exec_mode,
                "message": (
                    "Limit exits cannot be watched by the local executor for live positions. "
                    "Use order_type='market' or close from the exchange UI."
                ),
            }
        # Parse once, guarded: a non-numeric or non-finite level must never reach
        # update_position and rest on the Position as an unexecutable exit trigger.
        try:
            level = float(limit_price)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return {"error": "limit_price_required", "position_id": position_id}
        if not math.isfinite(level) or level <= 0:
            return {"error": "limit_price_required", "position_id": position_id}
        level = round(level, 8)
        # exit_limit_source is persisted with the level so the executor can pick the
        # honest close_reason later, when the original caller is long gone.
        update_position(
            position_id,
            exit_limit_price=level,
            exit_limit_source=actor,
        )
        logger.info(
            "proactive_exit(limit): %s resting exit at %.6f actor=%s reason=%s",
            position_id, level, actor, (reason or "")[:80],
        )
        return {
            "pending_limit_exit": True,
            "position_id": position_id,
            "limit_price": level,
            "exit_limit_source": actor,
        }

    # market: close now.
    price = _latest_price(pos["symbol"])
    result = close_position_routed(
        position_id,
        reason="human_manual_close" if is_human else "operator_close",
        fallback_exit_price=float(price) if price else None,
        decision_reasoning=reason,
        decision_actor=actor,
        decision_source="dashboard_manual_close" if is_human else "operator_proactive_exit",
        allow_live=False,
    )
    return {
        "closed": bool(result.get("closed", True)) if isinstance(result, dict) else True,
        "position_id": position_id,
        "close_reason": "human_manual_close" if is_human else "operator_close",
    }
