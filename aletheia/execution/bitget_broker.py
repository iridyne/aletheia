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
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.data.symbols import to_bitget
from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest
from aletheia.execution.broker_utils import clean_error_message
from aletheia.execution.position_enrichment import build_entry_enrichment, liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
    update_order,
)

logger = logging.getLogger("aletheia.bitget_broker")

# Round-trip taker fee rates (one side). Demo mirrors live mix taker fee.
_LIVE_FEE_RATE = 0.0006
_DEMO_FEE_RATE = 0.0006
_OPEN_CLOSE_STATES = {"pending", "submitted", "partial"}


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
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    leverage: int | None = None,
    opportunity_id: str | None = None,
    trade_school: str = "short_term",
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
            client_oid=proposal_id, leverage=leverage,
        )))
    except Exception as e:
        update_order(order.id, status="error", error_reason=clean_error_message(str(e))[:500])
        raise

    exchange_order_id = order_resp.order_id or ""
    resolved_status = order_resp.status if order_resp.status in ("filled", "partially_filled") else "submitted"
    update_order(order.id, exchange_order_id=exchange_order_id, status=resolved_status)

    # Use the exchange's CONFIRMED fill price/qty when it returned them; never
    # fabricate. Fall back to the requested values only when detail is absent (the
    # order is still effectively at the requested level until reconciliation refines).
    filled = order_resp.status in ("filled", "partially_filled")
    partial = order_resp.status == "partially_filled"
    fill_price = order_resp.fill_price if (filled and order_resp.fill_price) else price
    fill_qty = order_resp.filled_quantity if (filled and order_resp.filled_quantity) else quantity

    # Order row is persisted with its exchange id. If anything below fails,
    # reconciliation can still find this order by exchange_order_id.
    fill = None
    try:
        if filled:
            fill = Fill(
                order_id=order.id,
                fill_price=fill_price,
                fill_quantity=fill_qty,
                fee=fill_qty * fill_price * fee_rate,
            )
            insert_fill(fill)
            # #109 fix: preserve partial state — the previous code always wrote
            # status='filled' even when the exchange returned 'partially_filled'.
            # A downstream `WHERE status='filled'` query then treated a partial
            # as a full fill, and averaging-in / close paths saw the position
            # as complete when it was still being filled. Now the row keeps
            # 'partially_filled' until a follow-up fetch flips it to 'filled'
            # (or the reconciler observes the exchange marking it complete).
            order_status = "partially_filled" if partial else "filled"
            update_order(order.id, status=order_status, filled_at=datetime.now(timezone.utc).isoformat())

            existing = _find_open_position(symbol, market_type, direction, execution_mode, strategy_id)
            if existing:
                new_qty = existing["quantity"] + fill_qty
                new_entry = (existing["entry_price"] * existing["quantity"] + fill_price * fill_qty) / new_qty
                update_kwargs = {"quantity": new_qty, "entry_price": new_entry, "current_price": fill_price}
                # Tighten (never loosen) the protective stop when averaging in.
                merged_stop = merge_protective_stop(direction, stop_price, existing.get("stop_price"))
                if merged_stop is not None:
                    update_kwargs["stop_price"] = merged_stop
                # #140 fix: accumulate entry_fee across averaging-in legs. Without this,
                # a NULL/0.0 stored fee is kept while realized-PnL closeout re-derives the
                # (single-leg) fee from the schedule → overstated realized PnL feeds the
                # learning loop. Paper_broker already does this; the three live brokers
                # were clones that missed it.
                new_entry_fee_leg = round(fill_price * fill_qty * fee_rate, 8)
                existing_entry_fee = existing.get("entry_fee")
                update_kwargs["entry_fee"] = round((existing_entry_fee or 0.0) + new_entry_fee_leg, 8)
                # #141 fix: recompute liquidation for the new weighted-average entry.
                # Display/LLM only — real risk-gate uses the exchange's own liquidation,
                # this is what surfaces on the UI and in trading_context.
                existing_lev = existing.get("leverage")
                if existing_lev and existing_lev > 1:
                    update_kwargs["liquidation_price"] = liquidation_price_for(
                        new_entry, direction, existing_lev, symbol,
                    )
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
                    # Causal-chain back-link (trust §1.1): proposal/order/opportunity that opened it.
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
                # Optionally place exchange-native TPSL bracket (non-fatal on failure)
                if cfg.trading.exchange_tpsl_enabled and execution_mode in ("bitget_demo", "bitget_live_limited"):
                    _try_place_tpsl(
                        client=client,
                        position_id=position_id,
                        exchange_symbol=exchange_symbol,
                        hold_side="long" if direction in ("long", "buy") else "short",
                        size=fill_qty,
                        stop_price=stop_price,
                        take_profit_price=take_profit_price,
                        cfg=cfg,
                    )
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
        "fill_id": getattr(fill, "id", None) if filled else None,
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
    """Close a Bitget position (demo OR live-limited) with a REAL hedge-mode close
    order, then realize PnL locally. Keeps the exchange and local books consistent
    (state-consistency is the top priority): a local-only close would leave a
    dangling real position on the exchange — the exact §1.3 hazard, and on
    `bitget_live_limited` that dangling position holds REAL funds.

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
    if row.get("execution_mode") not in ("bitget_demo", "bitget_live_limited"):
        raise ValueError(f"close_demo_position only handles Bitget modes, got {row.get('execution_mode')!r}")
    if row.get("status") != "open":
        raise ValueError(f"position {position_id} is not open (status={row.get('status')})")
    if row.get("pending_close_order_id") and row.get("pending_close_status") in _OPEN_CLOSE_STATES:
        existing_order = repo.get_order(row["pending_close_order_id"])
        return {
            "order_id": row["pending_close_order_id"],
            "exchange_order_id": (existing_order or {}).get("exchange_order_id"),
            "close_order_id": row["pending_close_order_id"],
            "status": row.get("pending_close_status"),
            "closed": False,
            "position_id": position_id,
            "fill_price": None,
            "fill_quantity": 0,
            "already_pending": True,
        }

    cfg = load_config()
    # Derive the client mode from the POSITION's execution_mode, not cfg.exchange.mode:
    # a bitget_live_limited position must close on the live account even if the operator
    # has since flipped cfg.exchange.mode to demo (mirrors close_testnet_position).
    client = BitgetClient(mode="live" if row["execution_mode"] == "bitget_live_limited" else "demo")
    direction = row["direction"]
    is_long = direction in ("long", "buy")
    hold_side = "long" if is_long else "short"
    # Close `side` mirrors the OPEN side (verified empirically): close long = buy.
    side = "buy" if is_long else "sell"
    quantity = float(row["quantity"])
    exchange_symbol = to_bitget(row["symbol"])

    # Write a pending close order BEFORE the exchange call (same §1.3 hazard logic).
    close_requested_at = datetime.now(timezone.utc).isoformat()
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
            symbol=exchange_symbol, market_type=row["market_type"], side=side,
            order_type="market", quantity=quantity,
            client_oid=f"close-{position_id}",
            hold_side=hold_side,
        )))
    except Exception as e:
        update_order(close_order.id, status="error", error_reason=clean_error_message(str(e))[:500])
        update_position(position_id, pending_close_status="error")
        raise

    exchange_order_id = order_resp.order_id or ""
    confirmed_close = order_resp.status == "filled"
    close_status = "filled" if confirmed_close else (
        "partial" if order_resp.status == "partially_filled" else "submitted"
    )
    fee_rate = _DEMO_FEE_RATE

    update_order(
        close_order.id,
        exchange_order_id=exchange_order_id,
        status=close_status,
        filled_at=datetime.now(timezone.utc).isoformat() if confirmed_close else None,
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
            "close_order_id": close_order.id,
            "status": close_status,
            "closed": False,
            "position_id": position_id,
            "fill_price": None,
            "fill_quantity": 0,
        }

    # Only remove exchange protection after the market close is confirmed.
    if cfg.trading.exchange_tpsl_enabled and row.get("exchange_tpsl_order_id"):
        _try_cancel_tpsl(client, exchange_symbol, row["exchange_tpsl_order_id"])

    exit_price = order_resp.fill_price if order_resp.fill_price else (
        row.get("current_price") or row["entry_price"]
    )
    fill_qty = order_resp.filled_quantity if order_resp.filled_quantity else quantity
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
    result["status"] = "filled"
    result["closed"] = True
    result["fill_price"] = exit_price
    result["fill_quantity"] = fill_qty
    update_position(
        position_id,
        pending_close_order_id=None,
        pending_close_status=None,
        pending_close_requested_at=None,
        exchange_tpsl_order_id=None,
    )
    return result


# ─── TPSL bracket helpers ────────────────────────────────────────────────────

def _try_place_tpsl(
    client: BitgetClient,
    position_id: str,
    exchange_symbol: str,
    hold_side: str,
    size: float,
    stop_price: float | None,
    take_profit_price: float | None,
    cfg,
) -> None:
    """Place SL and/or TP trigger orders on the exchange. Non-fatal on failure."""
    import json as _json
    tpsl_ids: dict = {}

    if stop_price:
        try:
            sl_id = _run_coro(client.place_tpsl_order(
                symbol=exchange_symbol, plan_type="pos_loss",
                trigger_price=stop_price, hold_side=hold_side, size=size,
            ))
            if sl_id:
                tpsl_ids["sl"] = sl_id
        except Exception as e:
            logger.warning("TPSL SL placement failed for %s: %s", position_id, e)

    if take_profit_price:
        try:
            tp_id = _run_coro(client.place_tpsl_order(
                symbol=exchange_symbol, plan_type="pos_profit",
                trigger_price=take_profit_price, hold_side=hold_side, size=size,
            ))
            if tp_id:
                tpsl_ids["tp"] = tp_id
        except Exception as e:
            logger.warning("TPSL TP placement failed for %s: %s", position_id, e)

    if tpsl_ids:
        update_position(position_id, exchange_tpsl_order_id=_json.dumps(tpsl_ids))


def _try_cancel_tpsl(client: BitgetClient, exchange_symbol: str, tpsl_json: str) -> None:
    """Cancel all pending TPSL triggers for a position. Non-fatal."""
    import json as _json
    try:
        ids = _json.loads(tpsl_json) if isinstance(tpsl_json, str) else {}
    except (ValueError, TypeError):
        return
    if not isinstance(ids, dict):
        ids = {"sl": str(ids)}
    for key in ("sl", "tp"):
        oid = ids.get(key)
        if oid:
            try:
                _run_coro(client.cancel_plan_order(exchange_symbol, oid))
            except Exception as e:
                logger.warning("cancel TPSL %s %s failed: %s", key, oid, e)


def replace_exchange_stop(position_id: str, new_stop_price: float) -> None:
    """Cancel old SL trigger and place a new one at the updated stop price."""
    import json as _json
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if not row or not row.get("exchange_tpsl_order_id"):
        return
    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode)
    exchange_symbol = to_bitget(row["symbol"])
    hold_side = "long" if row["direction"] in ("long", "buy") else "short"

    try:
        tpsl_ids = _json.loads(row["exchange_tpsl_order_id"])
    except (ValueError, TypeError):
        return
    if not isinstance(tpsl_ids, dict):
        tpsl_ids = {}

    old_sl = tpsl_ids.get("sl")
    if old_sl:
        try:
            _run_coro(client.cancel_plan_order(exchange_symbol, old_sl))
        except Exception as e:
            logger.warning("cancel old SL %s failed: %s", old_sl, e)

    try:
        new_sl_id = _run_coro(client.place_tpsl_order(
            symbol=exchange_symbol, plan_type="pos_loss",
            trigger_price=new_stop_price, hold_side=hold_side,
            size=float(row["quantity"]),
        ))
        tpsl_ids["sl"] = new_sl_id
        update_position(position_id, exchange_tpsl_order_id=_json.dumps(tpsl_ids))
    except Exception as e:
        logger.warning("replace SL for %s failed: %s", position_id, e)
