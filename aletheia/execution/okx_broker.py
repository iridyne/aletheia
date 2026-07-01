"""OKX broker — routes approved proposals to OKX demo or live.

State-consistency protocol identical to binance_broker/bitget_broker:
1. Write pending Order row BEFORE exchange call
2. Call exchange
3. Backfill exchange_order_id, fill, position

Includes an O(1) symbol filter for OKX demo mode: two frozensets (swap and spot)
are cached module-level and refreshed every 4h by the scheduler. The check is
market-type-aware so a symbol available only as spot does not pass a perp order.
"""

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from aletheia.data.symbols import to_canonical
from aletheia.exchanges.okx.client import OKXClient, OrderRequest
from aletheia.execution.broker_utils import clean_error_message
from aletheia.execution.position_enrichment import build_entry_enrichment, liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, insert_fill, insert_position, list_positions, update_position,
    update_order,
)

logger = logging.getLogger("aletheia.okx_broker")

_OKX_FEE_RATE = 0.0005  # OKX futures taker fee
_OPEN_CLOSE_STATES = {"pending", "submitted", "partial"}

# ---------------------------------------------------------------------------
# O(1) Symbol filter cache for OKX demo — market-type-aware
#
# _okx_demo_symbols is now a dict {"swap": frozenset, "spot": frozenset}.
# Both sets store canonical symbols (BTC/USDT).  A futures/perp order is only
# allowed when the canonical is in the "swap" set; a spot order requires "spot".
# ---------------------------------------------------------------------------

_okx_demo_symbols: dict | None = None  # {"swap": frozenset, "spot": frozenset}
_okx_demo_symbols_loaded_at: float = 0.0
_OKX_SYMBOLS_TTL = 14400  # 4h
_symbols_lock = threading.Lock()


def _get_demo_symbols(market_type: str = "usdt_futures") -> frozenset | None:
    """Return cached OKX demo symbols for the given market_type, loading lazily.

    market_type "usdt_futures" / "perp" → swap frozenset
    market_type "spot"                  → spot frozenset

    Returns None only when the initial load fails (fail-open: allow the order through).
    """
    global _okx_demo_symbols, _okx_demo_symbols_loaded_at
    if _okx_demo_symbols is not None and (time.time() - _okx_demo_symbols_loaded_at) < _OKX_SYMBOLS_TTL:
        key = "spot" if market_type == "spot" else "swap"
        return _okx_demo_symbols.get(key)
    # Cache missing or stale — trigger a synchronous refresh
    refresh_okx_demo_symbols()
    if _okx_demo_symbols is None:
        return None  # refresh failed, fail-open
    key = "spot" if market_type == "spot" else "swap"
    return _okx_demo_symbols.get(key)


def refresh_okx_demo_symbols() -> int:
    """Refresh the OKX demo symbol cache. Called by scheduler every 4h.

    Returns the total number of supported symbols loaded (swap + spot).
    """
    global _okx_demo_symbols, _okx_demo_symbols_loaded_at

    async def _load():
        from aletheia.exchanges.okx.client import OKXClient
        return await OKXClient.load_demo_markets()

    with _symbols_lock:
        try:
            result = _run_coro(_load())
            _okx_demo_symbols = result
            _okx_demo_symbols_loaded_at = time.time()
            total = len(result.get("swap", frozenset())) + len(result.get("spot", frozenset()))
            logger.info("OKX demo symbols refreshed: %d swap + %d spot",
                        len(result.get("swap", frozenset())), len(result.get("spot", frozenset())))
            return total
        except Exception as e:
            logger.warning("Failed to refresh OKX demo symbols: %s", e)
            return 0


# ---------------------------------------------------------------------------
# Error message sanitiser


# ---------------------------------------------------------------------------
# Async runner helper
# ---------------------------------------------------------------------------

def _run_coro(coro):
    """Run a coroutine to completion from sync code."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Order execution
# ---------------------------------------------------------------------------

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
    execution_mode: str = "okx_demo",
    fee_rate: float = _OKX_FEE_RATE,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    leverage: int | None = None,
    opportunity_id: str | None = None,
    trade_school: str = "short_term",
) -> dict:
    """Place an order on OKX and record locally.

    For okx_demo: checks the O(1) symbol filter first. Unsupported symbols
    are rejected with an audit record before any exchange call.
    """
    # O(1) symbol filter for demo mode — market-type-aware. On rejection we just raise
    # — the gateway's try/except writes a complete demo_order_error audit (incl. proposal
    # status transition), so a write_audit here only produced a duplicate record (EC-2).
    if execution_mode == "okx_demo":
        supported = _get_demo_symbols(market_type)
        if supported is not None and to_canonical(symbol) not in supported:
            raise RuntimeError(f"OKX demo does not support {symbol} ({market_type})")

    mode = "demo" if execution_mode == "okx_demo" else "live"
    client = OKXClient(mode=mode)
    side = "buy" if direction in ("long", "buy") else "sell"

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


# ---------------------------------------------------------------------------
# Exchange-side protective orders (parity with bitget_broker._try_place_tpsl)
# ---------------------------------------------------------------------------

def place_okx_protective_orders(
    position_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    quantity: float,
    stop_price: float | None,
    take_profit_price: float | None,
    execution_mode: str,
) -> dict:
    """Place reduce-only SL/TP trigger orders on OKX for an EXISTING position and
    persist the resulting ids to positions.exchange_tpsl_order_id.

    OKX equivalent of bitget_broker._try_place_tpsl. Non-fatal — a placement error
    is logged, never raised (a position must never fail to exist because its
    exchange bracket could not be placed). Gating (flag + demo-only) is the
    caller's job (execution.stops.place_exchange_protective_orders).
    """
    import json as _json
    from aletheia.storage.repositories import update_position

    close_side = "sell" if direction in ("long", "buy") else "buy"
    mode = "demo" if execution_mode == "okx_demo" else "live"
    ids: dict = {}
    try:
        client = OKXClient(mode=mode)
        try:
            ids = _run_coro(client.place_algo_order(
                symbol=symbol, market_type=market_type, close_side=close_side,
                quantity=quantity, stop_price=stop_price, take_profit_price=take_profit_price,
            ))
        finally:
            try:
                _run_coro(client.close())
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001 — protective placement must never raise
        logger.warning("OKX protective order placement failed for %s %s: %s", symbol, direction, e)
        return {}

    if ids:
        update_position(position_id, exchange_tpsl_order_id=_json.dumps(ids))
        logger.info("OKX protective orders placed for %s %s: %s", symbol, direction, ids)
    return ids


# ---------------------------------------------------------------------------
# Position close
# ---------------------------------------------------------------------------

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


def close_okx_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Close an OKX position with a reduceOnly order.

    Works for both okx_demo and okx_live_limited positions.
    """
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.pnl import close_position

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("execution_mode") not in ("okx_demo", "okx_live_limited"):
        raise ValueError(f"close_okx_position only handles OKX modes, got {row.get('execution_mode')!r}")
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

    mode = "demo" if row["execution_mode"] == "okx_demo" else "live"
    client = OKXClient(mode=mode)
    direction = row["direction"]
    is_long = direction in ("long", "buy")
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
                fee=fill_qty * fill_price * _OKX_FEE_RATE)
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
