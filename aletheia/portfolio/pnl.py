"""Portfolio PnL computation + snapshots (guide §7 portfolio/)."""

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


def compute_portfolio(execution_mode: str | None = None) -> dict:
    """Aggregate open positions into a portfolio summary (marks to market first)."""
    positions = mark_positions_to_market()
    if execution_mode:
        positions = [p for p in positions if p["execution_mode"] == execution_mode]

    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_realized = sum(p.get("realized_pnl", 0.0) for p in positions)
    total_notional = sum(p["quantity"] * (p.get("current_price") or p["entry_price"]) for p in positions)

    # Exposure by symbol
    exposure: dict[str, float] = {}
    for p in positions:
        notional = p["quantity"] * (p.get("current_price") or p["entry_price"])
        exposure[p["symbol"]] = exposure.get(p["symbol"], 0.0) + notional

    return {
        "total_value_usdt": round(total_notional, 4),
        "unrealized_pnl": round(total_unrealized, 4),
        "realized_pnl": round(total_realized, 4),
        "today_pnl": round(total_unrealized + total_realized, 4),
        "positions_count": len(positions),
        "exposure_by_symbol": {k: round(v, 4) for k, v in exposure.items()},
        "positions": positions,
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
