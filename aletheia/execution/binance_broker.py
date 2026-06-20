"""Binance Futures Testnet broker — routes approved proposals to Binance testnet.

Parallel to bitget_broker.py. Reached from the ExecutionGateway for mode
`binance_testnet`. Uses CCXT binanceusdm with sandbox mode for the testnet at
testnet.binancefuture.com. No real funds, exchange-side record for high-fidelity
paper trading with 556 USDT-M pairs (vs Bitget demo's 42).

State-consistency protocol is identical to bitget_broker:
1. Write pending Order row BEFORE exchange call (§1.3 hazard protection)
2. Call exchange
3. Backfill exchange_order_id, fill, position
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from aletheia.data.symbols import to_binance
from aletheia.exchanges.binance.client import BinanceTestnetClient, OrderRequest
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
    update_order,
)

logger = logging.getLogger("aletheia.binance_broker")

_TESTNET_FEE_RATE = 0.0004  # Binance futures taker fee


def _run_coro(coro):
    """Run a coroutine to completion from sync code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
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
    execution_mode: str = "binance_testnet",
    fee_rate: float = _TESTNET_FEE_RATE,
) -> dict:
    """Place an order on Binance Futures Testnet and record locally.

    Signature matches bitget_broker.execute_live_order for gateway compatibility.
    """
    client = BinanceTestnetClient()
    side = "buy" if direction in ("long", "buy") else "sell"
    exchange_symbol = to_binance(symbol)  # noqa: F841 — kept for logging

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
        status="pending",
    )
    insert_order(order)

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=symbol, market_type=market_type, side=side,
            order_type=order_type, quantity=quantity,
            price=price if order_type == "limit" else None,
            client_oid=proposal_id,
        )))
    except Exception as e:
        update_order(order.id, status="error", error_reason=str(e)[:500])
        raise
    finally:
        try:
            _run_coro(client.close())
        except Exception:
            pass

    exchange_order_id = order_resp.order_id or ""
    resolved_status = order_resp.status if order_resp.status in ("filled", "partially_filled") else "submitted"
    update_order(order.id, exchange_order_id=exchange_order_id, status=resolved_status)

    filled = order_resp.status in ("filled", "partially_filled")
    fill_price = order_resp.fill_price if (filled and order_resp.fill_price) else price
    fill_qty = order_resp.filled_quantity if (filled and order_resp.filled_quantity) else quantity

    try:
        if filled:
            fill = Fill(
                order_id=order.id,
                fill_price=fill_price,
                fill_quantity=fill_qty,
                fee=fill_qty * fill_price * fee_rate,
            )
            insert_fill(fill)
            update_order(order.id, status="filled", filled_at=datetime.utcnow().isoformat())

            existing = _find_open_position(symbol, market_type, direction, execution_mode, strategy_id)
            if existing:
                new_qty = existing["quantity"] + fill_qty
                new_entry = (existing["entry_price"] * existing["quantity"] + fill_price * fill_qty) / new_qty
                update_position(existing["id"], quantity=new_qty, entry_price=new_entry, current_price=fill_price)
                position_id = existing["id"]
            else:
                position = Position(
                    symbol=symbol, market_type=market_type, direction=direction,
                    quantity=fill_qty, entry_price=fill_price, current_price=fill_price,
                    execution_mode=execution_mode, strategy_id=strategy_id,
                    proposal_id=proposal_id, order_id=order.id,
                )
                insert_position(position)
                position_id = position.id
        else:
            position_id = None
    except Exception as e:
        err = RuntimeError(f"Post-submit DB error: {e}")
        err.exchange_order_id = exchange_order_id  # type: ignore[attr-defined]
        raise err from e

    return {
        "order_id": order.id,
        "exchange_order_id": exchange_order_id,
        "fill_id": fill.id if filled else None,
        "position_id": position_id,
        "fill_price": fill_price if filled else None,
        "fill_quantity": fill_qty if filled else 0,
    }


def _find_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    execution_mode: str,
    strategy_id: str | None,
) -> dict | None:
    for p in list_positions(status="open"):
        if (p["symbol"] == symbol and p["market_type"] == market_type
                and p["direction"] == direction
                and p["execution_mode"] == execution_mode):
            if strategy_id is not None and p.get("strategy_id") not in (None, "", strategy_id):
                continue
            return p
    return None


def close_testnet_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Close a `binance_testnet` position with a reduceOnly order on the testnet.

    Unlike Bitget's hedge-mode close, Binance testnet uses reduceOnly=True with the
    opposite side (close long = sell reduceOnly, close short = buy reduceOnly).
    """
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.pnl import close_position

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("execution_mode") != "binance_testnet":
        raise ValueError(f"close_testnet_position only handles binance_testnet, got {row.get('execution_mode')!r}")
    if row.get("status") != "open":
        raise ValueError(f"position {position_id} is not open (status={row.get('status')})")

    client = BinanceTestnetClient()
    direction = row["direction"]
    is_long = direction in ("long", "buy")
    # Close = opposite side + reduceOnly
    side = "sell" if is_long else "buy"
    quantity = float(row["quantity"])

    close_order = Order(
        proposal_id=row.get("proposal_id"),
        symbol=row["symbol"],
        market_type=row["market_type"],
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=row.get("current_price") or row["entry_price"],
        execution_mode="binance_testnet",
        status="pending",
    )
    insert_order(close_order)

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=row["symbol"],
            market_type=row["market_type"],
            side=side,
            order_type="market",
            quantity=quantity,
            reduce_only=True,
            client_oid=close_order.id,
        )))
    except Exception as e:
        update_order(close_order.id, status="error", error_reason=str(e)[:500])
        raise
    finally:
        try:
            _run_coro(client.close())
        except Exception:
            pass

    exchange_order_id = order_resp.order_id or ""
    update_order(close_order.id, exchange_order_id=exchange_order_id, status="filled",
                 filled_at=datetime.utcnow().isoformat())

    fill_price = order_resp.fill_price if order_resp.fill_price else float(row.get("current_price") or row["entry_price"])
    fill_qty = order_resp.filled_quantity if order_resp.filled_quantity else quantity

    fill = Fill(order_id=close_order.id, fill_price=fill_price, fill_quantity=fill_qty,
                fee=fill_qty * fill_price * _TESTNET_FEE_RATE)
    insert_fill(fill)

    close_position(position_id, exit_price=fill_price, reason=reason)

    return {
        "order_id": close_order.id,
        "exchange_order_id": exchange_order_id,
        "fill_price": fill_price,
        "fill_quantity": fill_qty,
    }
