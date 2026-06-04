"""Bitget live broker — routes an approved live-limited proposal to the exchange.

Change-controlled module (guide §31.3). Only reached from the ExecutionGateway
after the Risk Engine approves a `bitget_live_limited` order AND live is unlocked.
The agent has no path here: it cannot call this directly (restricted), cannot
unlock live, and cannot modify the gateway/risk that guard it.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from aletheia.config.loader import load_config
from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
)

logger = logging.getLogger("aletheia.bitget_broker")


def _run_coro(coro):
    """Run a coroutine to completion from sync code, safe whether or not an event
    loop is already running in this thread (FastAPI async route vs scheduler thread)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # no loop in this thread
    # A loop is already running here — run the coroutine in a worker thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def execute_live_order(
    proposal_id: str,
    risk_decision_id: str | None,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    strategy_id: str | None = None,
) -> dict:
    """Place a real Bitget order and record order/fill/position locally."""
    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode)
    side = "buy" if direction in ("long", "buy") else "sell"

    order_resp = _run_coro(client.place_order(OrderRequest(
        symbol=symbol, market_type=market_type, side=side,
        order_type=order_type, quantity=quantity,
        price=price if order_type == "limit" else None,
    )))

    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        execution_mode="bitget_live_limited",
        exchange_order_id=order_resp.order_id,
        status="submitted",
        filled_at=datetime.utcnow().isoformat(),
    )
    insert_order(order)

    # Record a fill at the requested price. Reconciliation later corrects this
    # against the exchange's actual fills (execution/reconciliation.py).
    fill = Fill(order_id=order.id, fill_price=price, fill_quantity=quantity,
                fee=quantity * price * 0.0006)  # ~taker fee estimate
    insert_fill(fill)

    existing = _find_open_position(symbol, market_type, direction, strategy_id)
    if existing:
        new_qty = existing["quantity"] + quantity
        new_entry = (existing["entry_price"] * existing["quantity"] + price * quantity) / new_qty
        update_position(existing["id"], quantity=new_qty, entry_price=new_entry, current_price=price)
        position_id = existing["id"]
    else:
        position = Position(
            symbol=symbol, market_type=market_type, direction=direction,
            quantity=quantity, entry_price=price, current_price=price,
            execution_mode="bitget_live_limited", strategy_id=strategy_id,
        )
        insert_position(position)
        position_id = position.id

    return {
        "order_id": order.id,
        "exchange_order_id": order_resp.order_id,
        "fill_id": fill.id,
        "position_id": position_id,
        "fill_price": price,
        "fill_quantity": quantity,
    }


def _find_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    strategy_id: str | None,
) -> dict | None:
    for p in list_positions(status="open"):
        if (p["symbol"] == symbol and p["market_type"] == market_type
                and p["direction"] == direction
                and p["execution_mode"] == "bitget_live_limited"):
            if strategy_id is not None and p.get("strategy_id") not in (None, "", strategy_id):
                continue
            return p
    return None
