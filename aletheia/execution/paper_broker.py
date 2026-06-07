"""Paper broker — simulates order fills for paper/demo trading."""

from datetime import datetime

from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, update_order, insert_fill, insert_position, update_position, list_positions,
)


def execute_paper_order(
    proposal_id: str,
    risk_decision_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    stop_price: float | None = None,
) -> dict:
    """Execute a paper order: create order, fill, and position.

    The protective stop_price (from the proposal) and a take-profit derived from
    config TP% are recorded on a newly-opened position so the position_manager can
    act on them. Averaging into an existing position preserves its original stop/TP.
    """

    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        status="filled",
        filled_at=datetime.utcnow().isoformat(),
    )
    insert_order(order)

    fill = Fill(
        order_id=order.id,
        fill_price=price,
        fill_quantity=quantity,
        fee=quantity * price * 0.001,  # 0.1% simulated fee
    )
    insert_fill(fill)

    # Check if we have an existing position to update
    existing = _find_open_position(symbol, market_type, direction, execution_mode, strategy_id)
    if existing:
        new_qty = existing["quantity"] + quantity
        new_entry = (existing["entry_price"] * existing["quantity"] + price * quantity) / new_qty
        update_kwargs = {"quantity": new_qty, "entry_price": new_entry, "current_price": price}
        if stop_price is not None:
            old_stop = existing.get("stop_price")
            if old_stop is not None:
                update_kwargs["stop_price"] = max(stop_price, old_stop) if direction in ("long", "buy") else min(stop_price, old_stop)
            else:
                update_kwargs["stop_price"] = stop_price
        update_position(existing["id"], **update_kwargs)
        position_id = existing["id"]
    else:
        take_profit_price = _take_profit_for(direction, price)
        position = Position(
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            quantity=quantity,
            entry_price=price,
            current_price=price,
            execution_mode=execution_mode,
            strategy_id=strategy_id,
            # Causal-chain back-link (trust §1.1): the proposal/order that opened it.
            proposal_id=proposal_id,
            order_id=order.id,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
        )
        insert_position(position)
        position_id = position.id

    return {
        "order_id": order.id,
        "fill_id": fill.id,
        "position_id": position_id,
        "fill_price": price,
        "fill_quantity": quantity,
    }


def _take_profit_for(direction: str, entry_price: float) -> float | None:
    """Derive a take-profit price from config TP% and direction."""
    from aletheia.config.loader import load_config
    pct = load_config().trading.default_take_profit_pct
    if not pct or entry_price <= 0:
        return None
    if direction in ("long", "buy"):
        return round(entry_price * (1 + pct / 100.0), 8)
    return round(entry_price * (1 - pct / 100.0), 8)


def _find_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    execution_mode: str,
    strategy_id: str | None,
) -> dict | None:
    positions = list_positions(status="open")
    for p in positions:
        if (p["symbol"] == symbol and p["market_type"] == market_type
                and p["direction"] == direction and p["execution_mode"] == execution_mode):
            if strategy_id is not None and p.get("strategy_id") not in (None, "", strategy_id):
                continue
            return p
    return None
