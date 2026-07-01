"""Binance Futures broker — routes approved proposals to Binance testnet or live.

Parallel to bitget_broker.py. Reached from the ExecutionGateway for modes
`binance_testnet` and `binance_live_limited`.

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
from aletheia.exchanges.binance.client import BinanceClient, OrderRequest
from aletheia.execution.broker_utils import clean_error_message
from aletheia.execution.position_enrichment import build_entry_enrichment, liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
    update_order,
)

logger = logging.getLogger("aletheia.binance_broker")

_TESTNET_FEE_RATE = 0.0004  # Binance futures taker fee
_OPEN_CLOSE_STATES = {"pending", "submitted", "partial"}


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
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    leverage: int | None = None,
    opportunity_id: str | None = None,
    trade_school: str = "short_term",
) -> dict:
    """Place an order on Binance Futures (testnet or live) and record locally.

    Signature matches bitget_broker.execute_live_order for gateway compatibility.
    execution_mode: "binance_testnet" or "binance_live_limited"
    """
    mode = "live" if execution_mode == "binance_live_limited" else "testnet"
    client = BinanceClient(mode=mode)
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
            client_oid=proposal_id, leverage=leverage,
        )))
    except Exception as e:
        update_order(order.id, status="error", error_reason=clean_error_message(str(e))[:500])
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
                update_kwargs = {"quantity": new_qty, "entry_price": new_entry, "current_price": fill_price}
                # Tighten (never loosen) the protective stop when averaging in.
                merged_stop = merge_protective_stop(direction, stop_price, existing.get("stop_price"))
                if merged_stop is not None:
                    update_kwargs["stop_price"] = merged_stop
                update_position(existing["id"], **update_kwargs)
                position_id = existing["id"]
            else:
                # Stamp the same entry-time metadata paper_broker records (btc benchmark,
                # regime, market summary, reasoning, fees) via the shared helper, so
                # live-broker positions are not blind to alpha/regime/cost analytics
                # (clone-divergence guard — see position_enrichment).
                enrichment = build_entry_enrichment(
                    symbol=symbol, fill_price=fill_price, quantity=fill_qty,
                    requested_price=price, fee_rate=fee_rate, proposal_id=proposal_id,
                )
                position = Position(
                    symbol=symbol, market_type=market_type, direction=direction,
                    quantity=fill_qty, entry_price=fill_price, current_price=fill_price,
                    execution_mode=execution_mode, strategy_id=strategy_id,
                    proposal_id=proposal_id, order_id=order.id, opportunity_id=opportunity_id,
                    # Futures leverage + estimated liquidation (None for spot/1x).
                    leverage=leverage,
                    liquidation_price=liquidation_price_for(fill_price, direction, leverage, symbol),
                    # Persist protective stop/TP so position_manager acts on the AI's
                    # intended levels, not just the default-% fallback (regression EC-stop).
                    stop_price=stop_price, take_profit_price=take_profit_price,
                    trade_school=trade_school,
                    **enrichment,
                )
                insert_position(position)
                position_id = position.id
            # Notify operator of the fill (best-effort; mirrors paper_broker so demo/live
            # opens are not silent while only paper opens notify).
            try:
                from aletheia.notifications import notify_trade_fill
                notify_trade_fill(
                    symbol, direction, fill_qty, fill_price, position_id, execution_mode,
                    stop_price=stop_price,
                    take_profit_price=take_profit_price if not existing else None,
                )
            except Exception:
                pass
        else:
            position_id = None
    except Exception as e:
        from aletheia.execution.errors import PostSubmitDBError
        raise PostSubmitDBError(f"Post-submit DB error: {e}", exchange_order_id) from e

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
    """Close a Binance position (testnet OR live-limited) with a reduceOnly order.

    Unlike Bitget's hedge-mode close, Binance uses reduceOnly=True with the opposite
    side (close long = sell reduceOnly, close short = buy reduceOnly). A local-only
    close would leave a dangling real position on the exchange (§1.3 hazard); on
    `binance_live_limited` that dangling position holds REAL funds.
    """
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.pnl import close_position

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("execution_mode") not in ("binance_testnet", "binance_live_limited"):
        raise ValueError(f"close_testnet_position only handles Binance modes, got {row.get('execution_mode')!r}")
    if row.get("status") != "open":
        raise ValueError(f"position {position_id} is not open (status={row.get('status')})")
    if row.get("pending_close_order_id") and row.get("pending_close_status") in _OPEN_CLOSE_STATES:
        existing_order = repo.get_order(row["pending_close_order_id"])
        return {
            "order_id": row["pending_close_order_id"],
            "exchange_order_id": (existing_order or {}).get("exchange_order_id"),
            "status": row.get("pending_close_status"),
            "closed": False,
            "fill_price": None,
            "fill_quantity": 0,
            "already_pending": True,
        }

    mode = "live" if row["execution_mode"] == "binance_live_limited" else "testnet"
    client = BinanceClient(mode=mode)
    direction = row["direction"]
    is_long = direction in ("long", "buy")
    # Close = opposite side + reduceOnly
    side = "sell" if is_long else "buy"
    quantity = float(row["quantity"])

    close_requested_at = datetime.utcnow().isoformat()
    close_order = Order(
        proposal_id=row.get("proposal_id"),
        symbol=row["symbol"],
        market_type=row["market_type"],
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=row.get("current_price") or row["entry_price"],
        execution_mode=row["execution_mode"],
        status="pending",
    )
    insert_order(close_order)
    update_position(
        position_id,
        pending_close_order_id=close_order.id,
        pending_close_status="pending",
        pending_close_requested_at=close_requested_at,
    )

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=row["symbol"],
            market_type=row["market_type"],
            side=side,
            order_type="market",
            quantity=quantity,
            reduce_only=True,
            client_oid=f"close-{position_id}",
        )))
    except Exception as e:
        update_order(close_order.id, status="error", error_reason=clean_error_message(str(e))[:500])
        update_position(position_id, pending_close_status="error")
        raise
    finally:
        try:
            _run_coro(client.close())
        except Exception:
            pass

    exchange_order_id = order_resp.order_id or ""
    confirmed_close = order_resp.status == "filled"
    close_status = "filled" if confirmed_close else (
        "partial" if order_resp.status == "partially_filled" else "submitted"
    )
    update_order(
        close_order.id,
        exchange_order_id=exchange_order_id,
        status=close_status,
        filled_at=datetime.utcnow().isoformat() if confirmed_close else None,
    )
    if not confirmed_close:
        update_position(
            position_id,
            pending_close_order_id=close_order.id,
            pending_close_status=close_status,
            pending_close_requested_at=close_requested_at,
        )
        return {
            "order_id": close_order.id,
            "exchange_order_id": exchange_order_id,
            "status": close_status,
            "closed": False,
            "fill_price": None,
            "fill_quantity": 0,
        }

    fill_price = order_resp.fill_price if order_resp.fill_price else float(row.get("current_price") or row["entry_price"])
    fill_qty = order_resp.filled_quantity if order_resp.filled_quantity else quantity

    fill = Fill(order_id=close_order.id, fill_price=fill_price, fill_quantity=fill_qty,
                fee=fill_qty * fill_price * _TESTNET_FEE_RATE)
    insert_fill(fill)

    close_position(position_id, exit_price=fill_price, reason=reason)
    update_position(
        position_id,
        pending_close_order_id=None,
        pending_close_status=None,
        pending_close_requested_at=None,
    )

    return {
        "order_id": close_order.id,
        "exchange_order_id": exchange_order_id,
        "status": "filled",
        "closed": True,
        "fill_price": fill_price,
        "fill_quantity": fill_qty,
    }
