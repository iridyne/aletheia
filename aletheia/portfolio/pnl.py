"""Portfolio PnL computation + snapshots (guide §7 portfolio/)."""

from datetime import datetime, timezone

from aletheia.storage.models import PortfolioSnapshot
from aletheia.storage.repositories import (
    list_positions, insert_portfolio_snapshot, update_position, list_market_snapshots,
)


def _latest_price(symbol: str) -> float | None:
    from aletheia.trading.market_data import latest_snapshot
    snap = latest_snapshot(symbol)
    return float(snap["price"]) if snap else None


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


def close_position(position_id: str, exit_price: float, reason: str = "manual") -> dict:
    """Close an open position at exit_price, realizing PnL. Returns the result.

    Writes a position_closed audit event (trust §1.4). `reason` is recorded as
    close_reason (e.g. stop_loss / take_profit / max_hold / manual).
    """
    from aletheia.storage import repositories as repo
    from aletheia.files.jsonl import write_audit
    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")

    gross_pnl = _realized_pnl(row["direction"], row["entry_price"], exit_price, row["quantity"])
    # Deduct estimated round-trip fees (entry + exit) based on execution mode rate
    fee_rate = 0.001 if row.get("execution_mode", "").startswith("paper") else 0.0006
    entry_fee = row["entry_price"] * row["quantity"] * fee_rate
    exit_fee = exit_price * row["quantity"] * fee_rate
    realized = gross_pnl - entry_fee - exit_fee

    # BTC benchmark at exit for alpha/beta separation
    btc_exit = _btc_price_at_close()

    update_position(
        position_id,
        status="closed",
        current_price=exit_price,
        realized_pnl=realized,
        unrealized_pnl=0.0,
        close_reason=reason,
        closed_at=datetime.now(timezone.utc).isoformat(),
        btc_exit_price=btc_exit,
    )
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
    # Resolve shadow trades for this position batch
    try:
        from aletheia.analytics.shadow_trades import resolve_shadow_trades_for_position
        resolve_shadow_trades_for_position(row)
    except Exception:
        pass
    return {"position_id": position_id, "realized_pnl": realized, "exit_price": exit_price, "reason": reason}


def today_realized_pnl(execution_mode: str | None = None) -> float:
    """Sum realized PnL of positions closed today (UTC). Source for §21.3 max_daily_loss."""
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


def compute_portfolio(execution_mode: str | None = None, persist_marks: bool = False) -> dict:
    """Aggregate open positions into a portfolio summary (marks to market first)."""
    positions = mark_positions_to_market(persist=persist_marks)
    if execution_mode:
        positions = [p for p in positions if p["execution_mode"] == execution_mode]

    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_notional = sum(p["quantity"] * (p.get("current_price") or p["entry_price"]) for p in positions)
    total_today_realized = today_realized_pnl(execution_mode=execution_mode)

    # Account-level realized PnL from closed positions
    closed = list_positions(status="closed")
    if execution_mode:
        closed = [p for p in closed if p["execution_mode"] == execution_mode]
    total_realized = sum(p.get("realized_pnl", 0.0) for p in closed)

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
    try:
        from aletheia.portfolio import exchange_sync
        cached = exchange_sync.get_cached_state()
        sync_status = exchange_sync.status_for(cached)
        if cached is not None and sync_status in ("ok", "stale"):
            exchange_equity_usdt = cached.get("equity_usdt")
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
            exc_upnl = cached.get("futures_unrealized_pnl")
            if exc_upnl is not None:
                final_unrealized = float(exc_upnl)
                final_today_pnl = float(exc_upnl)

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

    return {
        "total_value_usdt": round(final_total_value, 4),
        "unrealized_pnl": round(final_unrealized, 4),
        "realized_pnl": round(total_realized, 4),
        "today_pnl": round(final_today_pnl, 4),
        "positions_count": final_positions_count,
        "exposure_by_symbol": {k: round(v, 4) for k, v in final_exposure.items()},
        "positions": positions,
        "today_realized_pnl": round(total_today_realized, 4),
        # Exchange-account view (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap).
        "exchange_equity_usdt": exchange_equity_usdt,
        "exchange_state_age_sec": exchange_state_age_sec,
        "exchange_external_positions": exchange_external_positions,
        "exchange_external_summary": exchange_external_summary,
        "exchange_sync_status": sync_status,
        "exchange_asset_breakdown": exchange_asset_breakdown,
    }


def compute_and_store_snapshot(execution_mode: str = "paper_internal") -> PortfolioSnapshot:
    """Compute the portfolio and persist portfolio_snapshot rows.

    Writes two rows when exchange data is available:
    1. Paper-local row (execution_mode from arg) — pure mark-to-market, no exchange data
    2. Exchange row (execution_mode="exchange_account") — real account equity from cache

    Called from the scheduler thread — uses persist=True to also write
    mark-to-market prices back to the positions table.
    """
    summary = compute_portfolio(persist_marks=True)

    # Row 1: paper-local (pure mark-to-market, ignoring exchange equity override)
    local_positions = [p for p in summary.get("positions", [])
                       if p.get("execution_mode", "").startswith("paper")]
    local_unrealized = sum(p.get("unrealized_pnl", 0) for p in local_positions)
    local_notional = sum(
        p["quantity"] * (p.get("current_price") or p["entry_price"])
        for p in local_positions
    )
    snap = PortfolioSnapshot(
        total_value_usdt=round(local_notional, 4),
        unrealized_pnl=round(local_unrealized, 4),
        realized_pnl=summary["realized_pnl"],
        positions_count=len(local_positions),
        execution_mode=execution_mode,
    )
    insert_portfolio_snapshot(snap)

    # Row 2: exchange account (real equity from Bitget cache, when available)
    exchange_equity = summary.get("exchange_equity_usdt")
    if exchange_equity is not None:
        exc_snap = PortfolioSnapshot(
            total_value_usdt=float(exchange_equity),
            unrealized_pnl=summary["unrealized_pnl"],
            realized_pnl=summary["realized_pnl"],
            positions_count=summary["positions_count"],
            execution_mode="exchange_account",
            exchange_equity_usdt=float(exchange_equity),
        )
        insert_portfolio_snapshot(exc_snap)

    return snap
