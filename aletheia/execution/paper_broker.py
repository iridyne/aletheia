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
) -> dict:
    """Execute a paper order: create order, fill, and position."""

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
        # Average into existing position
        new_qty = existing["quantity"] + quantity
        new_entry = (existing["entry_price"] * existing["quantity"] + price * quantity) / new_qty
        update_position(existing["id"], quantity=new_qty, entry_price=new_entry, current_price=price)
        position_id = existing["id"]
    else:
        position = Position(
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            quantity=quantity,
            entry_price=price,
            current_price=price,
            execution_mode=execution_mode,
            strategy_id=strategy_id,
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
