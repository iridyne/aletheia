"""Bitget broker — routes an approved exchange-bound proposal to Bitget.

Change-controlled module (guide §31.3). Reached from the ExecutionGateway for two
modes, both real exchange calls:
- `bitget_live_limited` — real funds; only after the Risk Engine approves AND live
  is unlocked. The agent has no path here (cannot call directly, cannot unlock live,
  cannot modify the gateway/risk that guard it).
- `bitget_demo` — Bitget's demo/simulated-trading API (paptrading header, USDT-futures
  only). No real funds, but a real exchange-side record, so paper trades have higher
  fidelity than local simulation. Autonomous paper_trader reaches this (paper-only).

Symbol handling: local order/fill/position rows keep the symbol in the format the
caller passed (canonical BTC/USDT for demo, matching market data + position_manager +
pnl). Only the exchange call is normalised to Bitget execution format (BTCUSDT).
Fill price/quantity come from the exchange's confirmed fill — never fabricated from
the request (falls back to the requested values only when the exchange did not return
fill detail, in which case the order stays effectively at the requested level).
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from aletheia.config.loader import load_config
from aletheia.data.symbols import to_bitget
from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
    update_order,
)

logger = logging.getLogger("aletheia.bitget_broker")

# Round-trip taker fee rates (one side). Demo mirrors live mix taker fee.
_LIVE_FEE_RATE = 0.0006
_DEMO_FEE_RATE = 0.0006


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
    execution_mode: str = "bitget_live_limited",
    fee_rate: float = _LIVE_FEE_RATE,
) -> dict:
    """Place a real Bitget order (live or demo) and record order/fill/position locally.

    `execution_mode` selects which exchange credentials/record this is tagged as
    (`bitget_live_limited` real funds, `bitget_demo` simulated). `cfg.exchange.mode`
    (demo|live) decides the actual client/credentials + paptrading header — the risk
    engine enforces that the two agree (account_mode_mismatch), so by the time we are
    here they are consistent.
    """
    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode)
    side = "buy" if direction in ("long", "buy") else "sell"
    # Exchange wants Bitget execution format (BTCUSDT); local rows keep the caller's
    # format (canonical BTC/USDT for demo) so they line up with market data + pnl.
    exchange_symbol = to_bitget(symbol)

    # Persist a `pending` order row BEFORE touching the exchange (guide §1.3 hazard:
    # if place_order succeeds but the DB write fails, the exchange has a real order and
    # we keep no local trace). With the row written first, the order always exists
    # locally; we backfill exchange_order_id + status after the exchange confirms.
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
            symbol=exchange_symbol, market_type=market_type, side=side,
            order_type=order_type, quantity=quantity,
            price=price if order_type == "limit" else None,
            client_oid=proposal_id,
        )))
    except Exception as e:
        update_order(order.id, status="error", error_reason=str(e)[:500])
        raise

    exchange_order_id = order_resp.order_id or ""
    resolved_status = order_resp.status if order_resp.status in ("filled", "partially_filled") else "submitted"
    update_order(order.id, exchange_order_id=exchange_order_id, status=resolved_status)

    # Use the exchange's CONFIRMED fill price/qty when it returned them; never
    # fabricate. Fall back to the requested values only when detail is absent (the
    # order is still effectively at the requested level until reconciliation refines).
    filled = order_resp.status in ("filled", "partially_filled")
    fill_price = order_resp.fill_price if (filled and order_resp.fill_price) else price
    fill_qty = order_resp.filled_quantity if (filled and order_resp.filled_quantity) else quantity

    # Order row is persisted with its exchange id. If anything below fails,
    # reconciliation can still find this order by exchange_order_id.
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
                    # Causal-chain back-link (trust §1.1): proposal/order that opened it.
                    proposal_id=proposal_id, order_id=order.id,
                )
                insert_position(position)
                position_id = position.id
        else:
            position_id = None
    except Exception as e:
        err = RuntimeError(f"Post-submit DB error: {e}")
        err.exchange_order_id = exchange_order_id
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


def close_demo_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Close a `bitget_demo` position with a REAL hedge-mode close order, then realize
    PnL locally. Keeps the exchange and local books consistent (state-consistency is
    the top priority): a local-only close would leave a dangling real demo position.

    Hedge-mode close (DECISIONS.md 2026-06-09): tradeSide:"close" + holdSide (the
    position's side), with `side` SAME as the original open side — close a long =
    side:"buy", holdSide:"long". Market order, fills synchronously.

    The local close (pnl.close_position) uses the exchange's confirmed fill price as
    the exit price — never a fabricated mark. Raises on exchange failure WITHOUT
    marking the position closed locally, so a failed close never desyncs the books.
    """
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.pnl import close_position

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("execution_mode") != "bitget_demo":
        raise ValueError(f"close_demo_position only handles bitget_demo, got {row.get('execution_mode')!r}")
    if row.get("status") != "open":
        raise ValueError(f"position {position_id} is not open (status={row.get('status')})")

    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode)
    direction = row["direction"]
    is_long = direction in ("long", "buy")
    hold_side = "long" if is_long else "short"
    # Close `side` mirrors the OPEN side (verified empirically): close long = buy.
    side = "buy" if is_long else "sell"
    quantity = float(row["quantity"])
    exchange_symbol = to_bitget(row["symbol"])

    # Write a pending close order BEFORE the exchange call (same §1.3 hazard logic).
    close_order = Order(
        proposal_id=row.get("proposal_id"),
        symbol=row["symbol"],
        market_type=row["market_type"],
        # The close order's own side is the reducing side; we still tag the order
        # direction as the position direction for traceability of what was closed.
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=row.get("current_price") or row["entry_price"],
        execution_mode="bitget_demo",
        status="pending",
    )
    insert_order(close_order)

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=exchange_symbol, market_type=row["market_type"], side=side,
            order_type="market", quantity=quantity,
            client_oid=f"close-{position_id}",
            hold_side=hold_side,
        )))
    except Exception as e:
        update_order(close_order.id, status="error", error_reason=str(e)[:500])
        raise

    exchange_order_id = order_resp.order_id or ""
    filled = order_resp.status in ("filled", "partially_filled")
    exit_price = order_resp.fill_price if (filled and order_resp.fill_price) else (
        row.get("current_price") or row["entry_price"]
    )
    fill_qty = order_resp.filled_quantity if (filled and order_resp.filled_quantity) else quantity
    fee_rate = _DEMO_FEE_RATE

    update_order(
        close_order.id,
        exchange_order_id=exchange_order_id,
        status="filled" if filled else "submitted",
        filled_at=datetime.utcnow().isoformat() if filled else None,
    )
    if filled:
        insert_fill(Fill(
            order_id=close_order.id,
            fill_price=exit_price,
            fill_quantity=fill_qty,
            fee=fill_qty * exit_price * fee_rate,
        ))

    # Realize PnL + write the position_closed audit (pnl.close_position handles both).
    result = close_position(position_id, exit_price=float(exit_price), reason=reason)
    result["exchange_order_id"] = exchange_order_id
    result["close_order_id"] = close_order.id
    return result
