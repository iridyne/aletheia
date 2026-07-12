"""Portfolio PnL computation + snapshots (guide §7 portfolio/)."""

import logging
import json
from datetime import datetime, timezone

from aletheia.storage.models import PortfolioSnapshot

logger = logging.getLogger("aletheia.pnl")
from aletheia.storage.repositories import (
    list_positions, insert_portfolio_snapshot, update_position, list_market_snapshots,
)


def _latest_price(symbol: str) -> float | None:
    from aletheia.data.price_feed import get_price_for_pnl
    return get_price_for_pnl(symbol)


def _btc_price_at_close() -> float | None:
    """Fetch BTC/USDT price for benchmark recording on position close."""
    try:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot("BTC/USDT")
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def _build_exchange_upnl_index() -> dict[tuple[str, str], float]:
    """Build (canonical_symbol, normalised_direction) → exchange uPnL from cache.

    Returns empty dict when cache is unavailable — callers fall back to the local
    estimate so the system degrades honestly rather than showing zeros.
    """
    try:
        from aletheia.portfolio import exchange_sync
        from aletheia.data.symbols import to_canonical
        cached = exchange_sync.get_cached_state()
        if cached is None or exchange_sync.status_for(cached) not in ("ok", "stale"):
            return {}
        index: dict[tuple[str, str], float] = {}
        for p in cached.get("positions") or []:
            sym = to_canonical(p.get("symbol", ""))
            side = (p.get("side") or "").lower()
            direction = "long" if side in ("long", "buy") else "short" if side in ("short", "sell") else None
            if not sym or direction is None:
                continue
            upnl = p.get("unrealized_pnl")
            if upnl is not None:
                index[(sym, direction)] = float(upnl)
        return index
    except Exception:
        return {}


def mark_positions_to_market(persist: bool = False) -> list[dict]:
    """Compute unrealized PnL on open positions.

    Prefers exchange uPnL (includes funding fees and mark-price basis) when the
    exchange cache is available. Falls back to the local (price - entry) * qty
    estimate only when the cache is absent or the position has no exchange match.

    When persist=False (default, used by GET /api/portfolio), only computes in
    memory — no DB writes, no WAL lock contention with scheduler threads.
    When persist=True (used by scheduler's portfolio_snapshot job), also writes
    the updated price/PnL back to the positions table.
    """
    positions = list_positions(status="open")
    exc_upnl_index = _build_exchange_upnl_index()
    updated = []
    for p in positions:
        price = _latest_price(p["symbol"])
        if price is None:
            price = p.get("current_price") or p["entry_price"]
        qty = p["quantity"]
        entry = p["entry_price"]
        direction = p["direction"]
        norm_dir = "long" if direction in ("long", "buy") else "short"
        # Exchange uPnL is authoritative (real mark-price + funding fees).
        # Fall back to local estimate when exchange data is unavailable.
        exc_val = exc_upnl_index.get((p["symbol"], norm_dir))
        if exc_val is not None:
            upnl = exc_val
        elif direction in ("long", "buy"):
            upnl = (price - entry) * qty
        else:
            upnl = (entry - price) * qty
        if persist:
            update_position(p["id"], current_price=price, unrealized_pnl=upnl)
        rec = dict(p)
        rec["current_price"] = price
        rec["unrealized_pnl"] = upnl
        updated.append(rec)
    return updated


def _realized_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    if direction in ("long", "buy"):
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty  # short/sell


def build_exit_reasoning(
    position: dict,
    *,
    exit_price: float | None,
    reason: str,
    actor: str = "system",
    source: str = "position_lifecycle",
    reasoning: str | None = None,
    is_ai_judgment: bool = False,
    trigger: dict | None = None,
) -> dict:
    """Build an honest close explanation from facts known at close time.

    Mechanical close reasons are deliberately described as rule triggers, not
    new AI opinions.  Callers with an actual System Operator review may provide
    its original text and mark it as an AI judgment.  This function never
    synthesizes an AI justification when none was recorded.
    """
    known_trigger: dict = {"exit_price": exit_price}
    if reason == "stop_loss":
        known_trigger["configured_stop_price"] = position.get("stop_price")
        default_reason = "Protective stop was reached; this is the rule committed at entry, not a new AI judgment."
    elif reason == "take_profit":
        known_trigger["configured_take_profit_price"] = position.get("take_profit_price")
        default_reason = "Take-profit target was reached; this is the rule committed at entry, not a new AI judgment."
    elif reason == "max_hold":
        known_trigger["opened_at"] = position.get("opened_at")
        default_reason = "The configured maximum holding period elapsed; this is an automatic lifecycle rule, not a new AI judgment."
    elif reason == "market_signal_exit":
        known_trigger["market_signal_reason"] = position.get("last_market_signal_reason")
        default_reason = "The market-aware exit rule fired; this is an automatic lifecycle rule, not a new AI judgment."
    elif reason == "exchange_bracket_triggered":
        default_reason = "The exchange reported that the pre-existing protective bracket closed the position; no new AI judgment was recorded."
    elif reason in {"verified_exit", "reconciled_gone"}:
        default_reason = "The position was closed from verified reconciliation evidence; no AI trade judgment was recorded."
    else:
        default_reason = "The position was closed through the recorded lifecycle path; no AI trade judgment was recorded."
    if trigger:
        known_trigger.update(trigger)
    text = reasoning.strip()[:1200] if isinstance(reasoning, str) and reasoning.strip() else default_reason
    return {
        "version": "trade_decision_rationale.v1",
        "actor": actor,
        "source": source,
        "reason_code": reason,
        "reason": text,
        "is_ai_judgment": bool(is_ai_judgment),
        "trigger": known_trigger,
    }


def persist_exit_reasoning(
    position_id: str,
    *,
    actor: str,
    source: str,
    reason: str,
    reasoning: str | None,
    is_ai_judgment: bool,
    trigger: dict | None = None,
) -> bool:
    """Replace a generic close explanation only after the position is closed.

    Exchange brokers close through their own adapters, which call
    :func:`close_position` after a confirmed fill.  A later AI-specific write is
    therefore allowed only on an already-closed position; a failed close cannot
    acquire an explanation that would make the dashboard lie.
    """
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if row is None or row.get("status") != "closed":
        return False
    payload = build_exit_reasoning(
        row,
        exit_price=row.get("current_price"),
        reason=reason,
        actor=actor,
        source=source,
        reasoning=reasoning,
        is_ai_judgment=is_ai_judgment,
        trigger=trigger,
    )
    update_position(position_id, exit_reasoning=json.dumps(payload, sort_keys=True))
    return True


def close_position(position_id: str, exit_price: float, reason: str = "manual") -> dict:
    """Close an open position at exit_price, realizing PnL. Returns the result.

    Writes a position_closed audit event (trust §1.4). `reason` is recorded as
    close_reason (e.g. stop_loss / take_profit / max_hold / manual).
    """
    from aletheia.analytics.close_reasons import is_synthetic_close
    if is_synthetic_close(reason):
        raise ValueError(
            "administrative close reasons must use close_position_administrative"
        )
    from aletheia.storage import repositories as repo
    from aletheia.files.jsonl import write_audit
    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("status") != "open":
        raise ValueError(f"position is not open: {position_id}")

    gross_pnl = _realized_pnl(row["direction"], row["entry_price"], exit_price, row["quantity"])
    # Deduct round-trip fees; prefer stored entry_fee (precise) over re-derivation.
    # Fee rates must match the open-leg constants in each broker file so realized PnL
    # is internally consistent regardless of which execution path opened the position.
    _EXIT_FEE_BY_MODE: dict[str, float] = {
        "paper_internal": 0.0006,      # matches paper_broker default (Bitget taker)
        "bitget_demo": 0.0006,         # bitget_broker._DEMO_FEE_RATE
        "bitget_live_limited": 0.0006, # bitget_broker._LIVE_FEE_RATE
        "okx_demo": 0.0005,            # okx_broker._OKX_FEE_RATE
        "okx_live_limited": 0.0005,
        "binance_testnet": 0.0004,     # binance_broker._TESTNET_FEE_RATE
        "binance_live_limited": 0.0004,
    }
    fee_rate = _EXIT_FEE_BY_MODE.get(row.get("execution_mode", ""), 0.0006)
    # Sprint B (QR-2026-07-01-02): entry_fee=0.0 is a legitimate stored value (zero-cost
    # paper fills). Only fall back to re-deriving from the fee schedule when the field is
    # genuinely missing (NULL from pre-Sprint-B positions). Truthiness "or 0" had been
    # recomputing fees on legible zeros and inflating realized PnL.
    entry_fee = row.get("entry_fee")
    if entry_fee is None:
        entry_fee = row["entry_price"] * row["quantity"] * fee_rate
    exit_fee = exit_price * row["quantity"] * fee_rate
    realized = gross_pnl - entry_fee - exit_fee

    # BTC benchmark at exit for alpha/beta separation
    btc_exit = _btc_price_at_close()

    # Estimate round-trip slippage for cost visibility
    try:
        from aletheia.config.loader import load_config as _lc
        _cfg = _lc()
        _sbps = _cfg.trading.slippage_bps
        _entry_slip = row["entry_price"] * row["quantity"] * (_sbps / 10_000.0)
        _exit_slip = exit_price * row["quantity"] * (_sbps / 10_000.0)
        slippage_cost = round(_entry_slip + _exit_slip, 8)
    except Exception:
        slippage_cost = None

    # #103 fix: compute `closed_at` before update_position and reuse it below.
    # _close_serenity_feedback receives `row` — that's the PRE-CLOSE snapshot
    # fetched at line 111, so `row['closed_at']` is None and PostMortem's
    # hold_h was always 0. Stamp the value into row too so the Serenity hook
    # sees the honest close time.
    closed_at = datetime.now(timezone.utc).isoformat()
    exit_reasoning = build_exit_reasoning(
        row,
        exit_price=exit_price,
        reason=reason,
    )
    update_position(
        position_id,
        status="closed",
        current_price=exit_price,
        realized_pnl=realized,
        unrealized_pnl=0.0,
        close_reason=reason,
        closed_at=closed_at,
        btc_exit_price=btc_exit,
        entry_fee=round(entry_fee, 8),
        exit_fee=round(exit_fee, 8),
        slippage_cost=slippage_cost,
        exit_reasoning=json.dumps(exit_reasoning, sort_keys=True),
    )
    row["closed_at"] = closed_at  # so the Serenity hook computes real hold_h
    if row.get("execution_mode") == "paper_internal":
        from aletheia.portfolio.paper_account import settle_position_closed
        settle_position_closed(position_id, realized)
    write_audit({
        "actor": "system",
        "source": "scheduler",
        "event_type": "position_closed",
        "entity_type": "position",
        "entity_id": position_id,
        "before": {"status": row.get("status"), "unrealized_pnl": row.get("unrealized_pnl")},
        "after": {"status": "closed", "realized_pnl": realized, "close_reason": reason},
        "message": f"Position {row.get('symbol')} closed @ {exit_price} ({reason}); realized={realized}",
    })
    # Notify operator of trade close (Telegram + toast)
    try:
        from aletheia.notifications import notify_operator
        pnl_emoji = "+" if realized > 0 else ""
        notify_operator(
            title=f"Position Closed: {row.get('symbol')}",
            message=(
                f"{row.get('direction')} → {reason} @ {exit_price:.4f}\n"
                f"PnL: {pnl_emoji}{realized:.4f} USDT"
            ),
            level="info" if realized >= 0 else "warning",
            event_type="trade_notification",
            entity_type="position",
            entity_id=position_id,
            error_log=False,
        )
    except Exception:
        pass
    # Resolve shadow trades for this position batch
    try:
        from aletheia.analytics.shadow_trades import resolve_shadow_trades_for_position
        resolve_shadow_trades_for_position(row)
    except Exception:
        pass
    # Serenity feedback loop: write PostMortem + close the source opportunity
    try:
        if row.get("trade_school") == "serenity" and row.get("opportunity_id"):
            _close_serenity_feedback(row, realized, reason)
    except Exception:
        pass
    return {"position_id": position_id, "realized_pnl": realized, "exit_price": exit_price, "reason": reason}


def today_raw_closed_realized_pnl(execution_mode: str | None = None) -> float:
    """Raw stored realized values; administrative unavailable values contribute nothing."""
    from aletheia.storage.repositories import _select_all
    today = datetime.now(timezone.utc).date().isoformat()
    rows = _select_all("positions", where="status = ?", params=("closed",), limit=1000)
    total = 0.0
    for r in rows:
        closed_at = r.get("closed_at") or ""
        try:
            closed_date = datetime.fromisoformat(closed_at).date().isoformat()
        except (ValueError, TypeError):
            continue
        if closed_date != today:
            continue
        if execution_mode and r.get("execution_mode") != execution_mode:
            continue
        total += r.get("realized_pnl") or 0.0
    return round(total, 8)


def today_eligible_strategy_pnl(execution_mode: str | None = None) -> float:
    """Today's PnL only from centrally verified, economically complete outcomes."""
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

    today = datetime.now(timezone.utc).date().isoformat()
    rows = eligible_strategy_outcomes(
        execution_mode=execution_mode,
        closed_after=f"{today}T00:00:00+00:00",
    )
    return round(sum(float(row.get("realized_pnl") or 0.0) for row in rows), 8)


def today_realized_pnl(execution_mode: str | None = None) -> float:
    """Compatibility raw metric; Risk migration to authoritative loss is RG-T09."""
    return today_raw_closed_realized_pnl(execution_mode=execution_mode)


def today_pnl_by_symbol(execution_mode: str | None = None) -> dict[str, float]:
    """Per-symbol realized PnL for positions closed today (UTC)."""
    from aletheia.storage.repositories import _select_all
    today = datetime.now(timezone.utc).date().isoformat()
    rows = _select_all("positions", where="status = ?", params=("closed",), limit=1000)
    by_sym: dict[str, float] = {}
    for r in rows:
        closed_at = r.get("closed_at") or ""
        try:
            closed_date = datetime.fromisoformat(closed_at).date().isoformat()
        except (ValueError, TypeError):
            continue
        if closed_date != today:
            continue
        if execution_mode and r.get("execution_mode") != execution_mode:
            continue
        sym = r.get("symbol", "?")
        by_sym[sym] = round(by_sym.get(sym, 0.0) + (r.get("realized_pnl") or 0.0), 4)
    return by_sym


def compute_portfolio(execution_mode: str | None = None, persist_marks: bool = False) -> dict:
    """Aggregate open positions into a portfolio summary (marks to market first)."""
    positions = mark_positions_to_market(persist=persist_marks)
    if execution_mode:
        positions = [p for p in positions if p["execution_mode"] == execution_mode]

    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_notional = sum(p["quantity"] * (p.get("current_price") or p["entry_price"]) for p in positions)
    total_today_realized = today_realized_pnl(execution_mode=execution_mode)

    # Realized PnL: per-mode for the requested execution_mode (paper dashboard),
    # plus all-mode total for the exchange_account row.
    all_closed = list_positions(status="closed", limit=10000)
    if execution_mode:
        mode_closed = [p for p in all_closed if p.get("execution_mode") == execution_mode]
    else:
        mode_closed = all_closed
    total_realized = sum(float(p.get("realized_pnl") or 0.0) for p in mode_closed)
    all_realized = sum(float(p.get("realized_pnl") or 0.0) for p in all_closed)

    # Exposure by symbol
    exposure: dict[str, float] = {}
    for p in positions:
        notional = p["quantity"] * (p.get("current_price") or p["entry_price"])
        exposure[p["symbol"]] = exposure.get(p["symbol"], 0.0) + notional

    # Exchange-account view (real Bitget balance/positions, cached). Never blocks
    # on a network call here — the scheduler refreshes the cache out-of-band, so
    # the portfolio API stays fast and degrades honestly when the cache is empty
    # or stale (closes the portfolio-sync-gap; see exchange_sync.py).
    exchange_equity_usdt = None
    exchange_state_age_sec = None
    exchange_external_positions = None
    exchange_asset_breakdown: list = []
    # These get overridden by the real exchange view when available.
    final_unrealized = total_unrealized
    final_positions_count = len(positions)
    final_exposure = exposure
    final_today_pnl = total_unrealized + total_today_realized
    # The headline "total value". Defaults to locally-managed notional, but is
    # overridden with real account equity when the exchange cache is live — so the
    # hero number (and the stored snapshot series) is true account value, not a
    # notional sum that jumps by one position's size on every fill. When no
    # exchange view is available it degrades to notional; callers read
    # `exchange_sync_status` to know which meaning applies.
    final_total_value = total_notional
    exchange_external_summary: list = []
    from aletheia.config.loader import load_config
    uses_external_exchange = load_config().exchange.paper_execution_mode != "paper_internal"
    sync_status = "not_applicable" if not uses_external_exchange else "unconfigured"
    if uses_external_exchange:
        try:
            from aletheia.portfolio import exchange_sync
            cached = exchange_sync.get_cached_state()
            sync_status = exchange_sync.status_for(cached)
            if cached is not None and sync_status in ("ok", "stale"):
                # get_cached_state() default path may now return the unified (multi-broker)
                # cache, which uses total_equity_usdt instead of the Bitget-specific
                # equity_usdt. Fall back so exchange_equity_usdt isn't null on non-Bitget
                # accounts (mirrors trading_context.py:64-65).
                exchange_equity_usdt = cached.get("equity_usdt") or cached.get("total_equity_usdt")
                exchange_state_age_sec = cached.get("age_seconds")
                # Real account equity is the authoritative total value when present.
                if exchange_equity_usdt is not None:
                    final_total_value = float(exchange_equity_usdt)
                # Accurate external-position classification via a pair-based (symbol,
                # direction) join — the crude `exchange_count - local_count` count is
                # wrong under hedge mode (a symbol can be long AND short at once). The
                # back-compat int is derived from the precise list below.
                try:
                    from aletheia.portfolio.position_reconciler import classify_positions
                    _recon = classify_positions()
                    exchange_external_summary = [i.to_dict() for i in _recon.external]
                    exchange_external_positions = len(_recon.external)
                except Exception:
                    local_open = len(positions)
                    ext_total = int(cached.get("open_positions_count") or 0)
                    exchange_external_positions = max(0, ext_total - local_open)
                exchange_asset_breakdown = cached.get("asset_breakdown") or []

                # Override PnL/exposure metrics with real exchange data when available.
                # Unified cache uses total_unrealized_pnl; Bitget cache uses futures_unrealized_pnl.
                exc_upnl = cached.get("futures_unrealized_pnl")
                if exc_upnl is None:
                    exc_upnl = cached.get("total_unrealized_pnl")
                if exc_upnl is not None:
                    final_unrealized = float(exc_upnl)
                    final_today_pnl = float(exc_upnl) + total_today_realized

                exc_positions = cached.get("positions") or []
                if exc_positions:
                    final_positions_count = len(exc_positions)
                    exc_exposure: dict[str, float] = {}
                    for p in exc_positions:
                        sym = p.get("symbol", "")
                        notional = float(p.get("quantity") or 0) * float(p.get("entry_price") or 0)
                        exc_exposure[sym] = exc_exposure.get(sym, 0.0) + notional
                    if exc_exposure:
                        final_exposure = exc_exposure
        except Exception:
            sync_status = "error"

    # UTA unified state (multi-broker aggregate, cached)
    uta_state = None
    if uses_external_exchange:
        try:
            from aletheia.portfolio import exchange_sync
            unified = exchange_sync.get_unified_cached_state()
            if unified is not None:
                uta_age = unified.get("age_seconds")
                if uta_age is not None and uta_age < 600:
                    uta_state = unified
                    # When unified state is available and has data, use as hero number
                    uta_equity = unified.get("total_equity_usdt")
                    if uta_equity and uta_equity > 0:
                        final_total_value = float(uta_equity)
                    # UTA uPnL is the authoritative cross-broker unrealized PnL. The
                    # legacy single-broker Bitget cache above sets final_unrealized to
                    # 0 when the active account is on another broker (e.g. OKX), which
                    # contradicted the equity-curve's local estimate. Prefer UTA here
                    # so the UNREALIZED card matches the real exchange mark-price uPnL.
                    uta_count = len(unified.get("positions") or [])
                    uta_upnl = unified.get("total_unrealized_pnl")
                    if uta_upnl is not None:
                        final_unrealized = float(uta_upnl)
                        final_today_pnl = float(uta_upnl) + total_today_realized
                    if uta_count:
                        final_positions_count = uta_count
        except Exception:
            pass

    return {
        "total_value_usdt": round(final_total_value, 4),
        "unrealized_pnl": round(final_unrealized, 4),
        "realized_pnl": round(total_realized, 4),
        "all_realized_pnl": round(all_realized, 4),
        # #56 note (2026-07-08): `today_pnl` is a legacy misnomer — it's
        # `unrealized_pnl (lifetime for all currently-open positions) +
        # today_realized_pnl`. It is NOT PnL earned today alone. A card
        # labelled "Today" using this value overstates today's activity by
        # any long-held open position's total uPnL. Callers wanting pure
        # today-only PnL should read `today_realized_pnl` and add whatever
        # they can defensibly claim as today's mark-to-market delta
        # (requires a yesterday-close snapshot which the schema doesn't
        # currently persist — parked as a Serenity-adjacent improvement).
        # Kept as-is for API back-compat; UI callers are expected to
        # transition to today_realized_pnl for the honest "Today" number.
        "today_pnl": round(final_today_pnl, 4),
        "positions_count": final_positions_count,
        "exposure_by_symbol": {k: round(v, 4) for k, v in final_exposure.items()},
        "unrealized_by_symbol": {p["symbol"]: round(p["unrealized_pnl"], 4) for p in positions if p.get("unrealized_pnl")},
        "today_pnl_by_symbol": today_pnl_by_symbol(execution_mode),
        "positions": positions,
        "today_realized_pnl": round(total_today_realized, 4),
        # Exchange-account view (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap).
        "exchange_equity_usdt": exchange_equity_usdt,
        "exchange_state_age_sec": exchange_state_age_sec,
        "exchange_external_positions": exchange_external_positions,
        "exchange_external_summary": exchange_external_summary,
        "exchange_sync_status": sync_status,
        "exchange_asset_breakdown": exchange_asset_breakdown,
        # UTA unified view (multi-broker aggregate).
        "uta_state": uta_state,
    }


def compute_and_store_snapshot(execution_mode: str = "paper_internal") -> PortfolioSnapshot:
    """Compute the portfolio and persist portfolio_snapshot rows.

    Writes two rows when exchange data is available:
    1. Paper-local row (execution_mode from arg) — pure mark-to-market, no exchange data
    2. Exchange row (execution_mode="exchange_account") — real account equity from cache

    Called from the scheduler thread — uses persist=True to also write
    mark-to-market prices back to the positions table.
    """
    summary = compute_portfolio(execution_mode=execution_mode, persist_marks=True)

    # Row 1: mode-specific (mark-to-market for positions matching execution_mode)
    mode_positions = [p for p in summary.get("positions", [])
                      if p.get("execution_mode") == execution_mode]
    local_unrealized = sum(p.get("unrealized_pnl", 0) for p in mode_positions)
    local_notional = sum(
        p["quantity"] * (p.get("current_price") or p["entry_price"])
        for p in mode_positions
    )
    snap = PortfolioSnapshot(
        total_value_usdt=round(local_notional, 4),
        unrealized_pnl=round(local_unrealized, 4),
        realized_pnl=summary["realized_pnl"],
        positions_count=len(mode_positions),
        execution_mode=execution_mode,
    )
    insert_portfolio_snapshot(snap)

    # Row 2: exchange account — prefer UTA unified equity (multi-broker sum),
    # fallback to legacy single-broker Bitget cache.
    uta_state = summary.get("uta_state")
    uta_equity = None
    if uta_state:
        uta_equity = uta_state.get("total_equity_usdt")
    exchange_equity = uta_equity or summary.get("exchange_equity_usdt")
    # View-completeness gate (P0, 2026-07-02): the UNIFIED total (uta_equity) is a
    # multi-broker SUM. When the ACTIVE broker is missing from the unified view
    # (e.g. OKX API 50001 → the sum is only the idle bitget $167, not the real
    # ~$89k), recording it as "account equity" poisons the drawdown history with a
    # fake ~99% drawdown that then zeroes the size cap (drawdown-notional-trap
    # family). Skip the row rather than record a lie. The legacy single-broker
    # fallback (exchange_equity_usdt) is a direct per-broker read and is NOT subject
    # to this — only the unified-sum path can misattribute another broker's balance.
    if uta_equity is not None:
        from aletheia.portfolio.exchange_sync import active_broker_view_complete
        _view_complete, _view_reason = active_broker_view_complete()
        if not _view_complete:
            logger.warning(
                "Skipping exchange_account equity snapshot: unified total is missing the "
                "active broker (%s) — recording it would poison the drawdown history.",
                _view_reason,
            )
            exchange_equity = None
    if exchange_equity is not None:
        exc_snap = PortfolioSnapshot(
            total_value_usdt=float(exchange_equity),
            unrealized_pnl=summary["unrealized_pnl"],
            realized_pnl=summary["all_realized_pnl"],
            positions_count=summary["positions_count"],
            execution_mode="exchange_account",
            exchange_equity_usdt=float(exchange_equity),
        )
        insert_portfolio_snapshot(exc_snap)

    return snap


# ---------------------------------------------------------------------------
# Serenity feedback loop (Link 9→10 closure)
# ---------------------------------------------------------------------------


def _close_serenity_feedback(position: dict, realized_pnl: float, close_reason: str) -> None:
    """Write SerenityPostMortem and update opportunity status when a serenity position closes.

    This closes the attribution loop: position close → PostMortem → opportunity status.
    The dreaming_agent reads PostMortems for pattern synthesis; trade_attribution joins
    opportunity_id back to serenity_opportunities. Without this hook both are severed.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityPostMortem

    opp_id = position.get("opportunity_id")
    if not opp_id:
        return

    outcome = "win" if realized_pnl > 0 else "loss"
    entry = position.get("entry_price") or 0
    exit_price = position.get("current_price") or entry
    notional = entry * (position.get("quantity") or 0)
    pnl_pct = (realized_pnl / notional * 100) if notional else 0.0

    failure_bucket = None
    if outcome == "loss":
        if close_reason == "stop_loss":
            failure_bucket = "stop_hit"
        elif close_reason == "thesis_invalidated":
            failure_bucket = "thesis_invalidated"
        elif close_reason == "max_hold":
            failure_bucket = "time_decay"
        else:
            failure_bucket = close_reason or "unknown"

    pm = SerenityPostMortem(
        opportunity_id=opp_id,
        outcome=outcome,
        failure_bucket=failure_bucket,
        score_delta=None,
        attribution=(
            f"trade_school=serenity close_reason={close_reason} "
            f"pnl={realized_pnl:.2f} pnl_pct={pnl_pct:.1f}% "
            f"hold_h={_compute_holding_hours(position.get('opened_at'), position.get('closed_at')) or 0:.0f}"
        ),
        summary=(
            f"{position.get('symbol')} {position.get('direction')} "
            f"{'profit' if realized_pnl > 0 else 'loss'} {pnl_pct:+.1f}% "
            f"closed by {close_reason}"
        ),
        created_by="system_auto_close",
    )
    repo.insert_serenity_post_mortem(pm)

    repo.update_serenity_opportunity(
        opp_id,
        status="traded_closed",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _compute_holding_hours(opened_at: str | None, closed_at: str | None) -> float | None:
    """Compute holding duration in hours between two ISO timestamps."""
    if not opened_at or not closed_at:
        return None
    try:
        from datetime import datetime as _dt
        fmt_candidates = ["%Y-%m-%dT%H:%M:%S.%f+00:00", "%Y-%m-%dT%H:%M:%S+00:00",
                          "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
        t_open = t_close = None
        for fmt in fmt_candidates:
            try:
                t_open = _dt.strptime(opened_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        for fmt in fmt_candidates:
            try:
                t_close = _dt.strptime(closed_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        if t_open and t_close:
            return (t_close - t_open).total_seconds() / 3600.0
    except Exception:
        pass
    return None
