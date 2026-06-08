"""Portfolio PnL computation + snapshots (guide §7 portfolio/)."""

from datetime import datetime, timezone

from aletheia.storage.models import PortfolioSnapshot
from aletheia.storage.repositories import (
    list_positions, insert_portfolio_snapshot, update_position, list_market_snapshots,
)


def _latest_price(symbol: str) -> float | None:
    rows = list_market_snapshots(symbol=symbol, limit=1)
    return rows[0]["price"] if rows else None


def mark_positions_to_market() -> list[dict]:
    """Update unrealized PnL on open positions using the latest market price."""
    positions = list_positions(status="open")
    updated = []
    for p in positions:
        price = _latest_price(p["symbol"])
        if price is None:
            price = p.get("current_price") or p["entry_price"]
        qty = p["quantity"]
        entry = p["entry_price"]
        direction = p["direction"]
        if direction in ("long", "buy"):
            upnl = (price - entry) * qty
        else:  # short/sell
            upnl = (entry - price) * qty
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
    update_position(
        position_id,
        status="closed",
        current_price=exit_price,
        realized_pnl=realized,
        unrealized_pnl=0.0,
        close_reason=reason,
        closed_at=datetime.now(timezone.utc).isoformat(),
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
    return {"position_id": position_id, "realized_pnl": realized, "exit_price": exit_price, "reason": reason}


def today_realized_pnl(execution_mode: str | None = None) -> float:
    """Sum realized PnL of positions closed today (UTC). Source for §21.3 max_daily_loss."""
    from aletheia.storage.repositories import _select_all
    today = datetime.now(timezone.utc).date().isoformat()
    rows = _select_all("positions", where="status = ?", params=("closed",), limit=1000)
    total = 0.0
    for r in rows:
        closed_at = r.get("closed_at") or ""
        if not closed_at.startswith(today):
            continue
        if execution_mode and r.get("execution_mode") != execution_mode:
            continue
        total += r.get("realized_pnl") or 0.0
    return round(total, 8)


def compute_portfolio(execution_mode: str | None = None) -> dict:
    """Aggregate open positions into a portfolio summary (marks to market first)."""
    positions = mark_positions_to_market()
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
    try:
        from aletheia.portfolio import exchange_sync
        cached = exchange_sync.get_cached_state()
        sync_status = exchange_sync.status_for(cached)
        if cached is not None:
            exchange_equity_usdt = cached.get("equity_usdt")
            exchange_state_age_sec = cached.get("age_seconds")
            local_open = sum(1 for p in positions)  # locally-managed open
            ext_total = int(cached.get("open_positions_count") or 0)
            # Drift signal: any externally-held positions beyond what we manage.
            # Negative would mean local > exchange (shouldn't happen on a dedicated
            # account); clamp to 0 to keep the signal honest.
            exchange_external_positions = max(0, ext_total - local_open)
    except Exception:
        sync_status = "error"

    return {
        "total_value_usdt": round(total_notional, 4),
        "unrealized_pnl": round(total_unrealized, 4),
        "realized_pnl": round(total_realized, 4),
        "today_pnl": round(total_unrealized + total_today_realized, 4),
        "positions_count": len(positions),
        "exposure_by_symbol": {k: round(v, 4) for k, v in exposure.items()},
        "positions": positions,
        "today_realized_pnl": round(total_today_realized, 4),
        # Exchange-account view (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap).
        "exchange_equity_usdt": exchange_equity_usdt,
        "exchange_state_age_sec": exchange_state_age_sec,
        "exchange_external_positions": exchange_external_positions,
        "exchange_sync_status": sync_status,
    }


def compute_and_store_snapshot(execution_mode: str = "paper_internal") -> PortfolioSnapshot:
    """Compute the portfolio and persist a portfolio_snapshot row."""
    summary = compute_portfolio()
    snap = PortfolioSnapshot(
        total_value_usdt=summary["total_value_usdt"],
        unrealized_pnl=summary["unrealized_pnl"],
        realized_pnl=summary["realized_pnl"],
        positions_count=summary["positions_count"],
        execution_mode=execution_mode,
    )
    insert_portfolio_snapshot(snap)
    return snap
