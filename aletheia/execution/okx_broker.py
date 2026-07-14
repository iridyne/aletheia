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
from datetime import datetime, timezone

from aletheia.data.symbols import to_canonical
from aletheia.exchanges.okx.client import OKXClient, OrderRequest, okx_ccxt_symbol
from aletheia.exchanges.submission_errors import (
    is_ambiguous_submission_error as _is_ambiguous_submission_error,
)
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.post_commit_audit import write_post_commit_audit
from aletheia.execution.position_enrichment import build_entry_enrichment, liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    find_compatible_open_position,
    insert_order, insert_fill, insert_position, update_position,
    update_order, transaction,
)
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.okx_broker")

_OKX_FEE_RATE = 0.0005  # OKX futures taker fee
_OKX_ACCOUNT_MODES = {
    "okx_demo": "demo",
    "okx_live_limited": "live",
}
_OPEN_CLOSE_STATES = {
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing", "overfilled",
}
_RETRY_SUPPRESSING_ORDER_STATES = (
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)


def _guard_unresolved_submission(proposal_id: str, execution_mode: str) -> None:
    placeholders = ",".join("?" for _ in _RETRY_SUPPRESSING_ORDER_STATES)
    row = get_db().execute(
        f"""
        SELECT id, exchange_order_id, status
        FROM orders
        WHERE proposal_id=? AND execution_mode=? AND status IN ({placeholders})
        ORDER BY rowid ASC
        LIMIT 1
        """,
        (proposal_id, execution_mode, *_RETRY_SUPPRESSING_ORDER_STATES),
    ).fetchone()
    if row is None:
        return
    if row["status"] == "materialization_missing":
        raise PostSubmitDBError(
            "exchange fill awaits local materialization", row["exchange_order_id"],
        )
    raise SubmissionUnknownError(
        exchange_order_id=row["exchange_order_id"], client_oid=proposal_id,
    )


def _validated_fill_economics(
    status: str,
    fill_price,
    filled_quantity,
    requested_quantity: float,
) -> tuple[float, float] | None:
    """Validate that exchange status and reported economics agree."""
    try:
        price = float(fill_price)
        quantity = float(filled_quantity)
        requested = float(requested_quantity)
    except (TypeError, ValueError):
        return None
    if price <= 0 or quantity <= 0 or requested <= 0:
        return None
    tolerance = max(1e-12, requested * 1e-9)
    if status == "filled" and abs(quantity - requested) <= tolerance:
        return price, quantity
    if status == "partially_filled" and quantity < requested - tolerance:
        return price, quantity
    return None

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
            logger.warning("Failed to refresh OKX demo symbols: %s", type(e).__name__)
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


def _created_at_ms(value) -> int:
    """Parse a durable UTC timestamp without inventing a history window."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("order creation timestamp unavailable")
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


async def fetch_private_fill_evidence(order: dict) -> list[dict]:
    """Return complete, account- and order-matched OKX private fills.

    This is a read-only adapter boundary.  It deliberately does not fall back
    to the aggregate create/fetch-order response because that response does not
    carry authoritative per-fill fee and exchange time.
    """
    execution_mode = str(order.get("execution_mode") or "")
    account_mode = _OKX_ACCOUNT_MODES.get(execution_mode)
    if account_mode is None:
        raise ValueError("OKX reconciliation account mismatch")
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()
    if not exchange_order_id and not client_order_id:
        raise ValueError("OKX exchange order identity unavailable")
    market_type = str(order.get("market_type") or "")
    if market_type not in {"spot", "usdt_futures"}:
        raise ValueError("OKX reconciliation market type unsupported")

    client = OKXClient(mode=account_mode)
    try:
        exchange = client._ensure_exchange()
        await exchange.load_markets()
        ccxt_symbol = okx_ccxt_symbol(market_type, str(order["symbol"]))
        market = exchange.market(ccxt_symbol)
        instrument_id = str((market or {}).get("id") or "").strip()
        if not instrument_id:
            raise ValueError("OKX instrument identity unavailable")
        if market_type == "spot":
            contract_size = 1.0
            instrument_type = "SPOT"
        else:
            try:
                contract_size = float((market or {}).get("contractSize"))
            except (TypeError, ValueError):
                contract_size = 0.0
            if contract_size <= 0:
                raise ValueError("OKX contract size unavailable")
            instrument_type = "SWAP"

        start_ms = _created_at_ms(order.get("created_at"))
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw_fills: list[dict] = []
        cursor: str | None = None
        complete = False
        for _ in range(100):
            params = {
                "instType": instrument_type,
                "instId": instrument_id,
                "begin": str(start_ms),
                "end": str(end_ms),
                "limit": "100",
            }
            if cursor:
                params["after"] = cursor
            payload = await exchange.private_get_trade_fills_history(params)
            if not isinstance(payload, dict) or str(payload.get("code")) != "0":
                raise RuntimeError("OKX private fill history rejected")
            page = payload.get("data")
            if not isinstance(page, list):
                raise ValueError("OKX private fill history malformed")
            raw_fills.extend(row for row in page if isinstance(row, dict))
            if len(page) < 100:
                complete = True
                break
            next_cursor = str(page[-1].get("billId") or "")
            if not next_cursor or next_cursor == cursor:
                raise RuntimeError("OKX private fill pagination incomplete")
            cursor = next_cursor
        if not complete:
            raise RuntimeError("OKX private fill history incomplete")

        normalized: list[dict] = []
        recovered_order_ids: set[str] = set()
        for row in raw_fills:
            remote_order_id = str(row.get("ordId") or "").strip()
            remote_client_id = str(row.get("clOrdId") or "").strip()
            if exchange_order_id:
                matched_identity = remote_order_id == exchange_order_id
            else:
                matched_identity = remote_client_id == client_order_id
            if not matched_identity:
                continue
            if str(row.get("instId") or instrument_id) != instrument_id:
                raise ValueError("OKX order-matched private fill instrument mismatch")
            if not remote_order_id:
                raise ValueError("OKX order-matched private fill is incomplete")
            recovered_order_ids.add(remote_order_id)
            fill_id = str(row.get("billId") or row.get("tradeId") or "").strip()
            try:
                price = float(row.get("fillPx"))
                quantity = float(row.get("fillSz")) * contract_size
                timestamp_ms = int(row.get("ts") or row.get("fillTime"))
                fee = float(row.get("fee"))
            except (TypeError, ValueError):
                raise ValueError("OKX order-matched private fill is incomplete")
            fee_currency = str(row.get("feeCcy") or "").strip()
            if (
                not fill_id
                or price <= 0
                or quantity <= 0
                or timestamp_ms <= 0
                or not fee_currency
            ):
                raise ValueError("OKX order-matched private fill is incomplete")
            normalized.append({
                "orderId": remote_order_id,
                "tradeId": fill_id,
                "clientOid": str(row.get("clOrdId") or "") or None,
                "fillPrice": str(price),
                "fillQuantity": str(quantity),
                "fee": str(fee),
                "feeCoin": fee_currency,
                "cTime": str(timestamp_ms),
            })
        if len(recovered_order_ids) > 1:
            raise ValueError("OKX client order identity is ambiguous")
        return normalized
    finally:
        try:
            await client.close()
        except Exception as exc:
            logger.warning("OKX reconciliation client close failed (%s)", type(exc).__name__)


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
    _guard_unresolved_submission(proposal_id, execution_mode)
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
        client_order_id=proposal_id,
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
        if _is_ambiguous_submission_error(e):
            update_order(
                order.id,
                status="submission_unknown",
                error_reason="order_submission_transport_acknowledgement_unknown",
            )
            write_post_commit_audit({
                "actor": "system", "source": "okx_broker",
                "event_type": "order_submission_unknown", "entity_type": "order",
                "entity_id": order.id,
                "reason": "transport_acknowledgement_unknown",
                "before": {"status": "pending"},
                "after": {"status": "submission_unknown"},
                "message": "Order transport failed after submission began; automatic retry suppressed",
            })
            raise SubmissionUnknownError(client_oid=proposal_id) from e
        update_order(order.id, status="error", error_reason="order_submission_rejected")
        raise
    finally:
        try:
            _run_coro(client.close())
        except Exception:
            pass

    exchange_order_id = order_resp.order_id or ""
    if not exchange_order_id:
        try:
            update_order(
                order.id,
                status="submission_unknown",
                error_reason="order_submission_missing_exchange_id",
            )
        except Exception:
            pass
        write_post_commit_audit({
            "actor": "system", "source": "okx_broker",
            "event_type": "order_submission_unknown", "entity_type": "order",
            "entity_id": order.id,
            "reason": "missing_exchange_order_id",
            "before": {"status": "pending"},
            "after": {"status": "submission_unknown"},
            "message": "Exchange acknowledged submission without an order id; automatic retry suppressed",
        })
        raise SubmissionUnknownError(client_oid=proposal_id)
    exchange_reported_fill = order_resp.status in ("filled", "partially_filled")
    stored_status = "materialization_missing" if exchange_reported_fill else "submitted"
    try:
        update_order(
            order.id,
            exchange_order_id=exchange_order_id,
            status=stored_status,
            error_reason=(
                "private_fill_evidence_pending" if exchange_reported_fill else None
            ),
        )
    except Exception as e:
        raise PostSubmitDBError(
            "exchange acknowledgement could not be persisted", exchange_order_id,
        ) from e
    if exchange_reported_fill:
        write_post_commit_audit({
            "actor": "system", "source": "okx_broker",
            "event_type": "order_materialization_pending", "entity_type": "order",
            "entity_id": order.id,
            "reason": "private_fill_evidence_pending",
            "before": {"status": "pending"},
            "after": {"status": "materialization_missing"},
            "message": (
                f"Exchange order {exchange_order_id} reported a fill; local "
                "Fill/Position waits for order-matched private fill evidence"
            ),
        })

    return {
        "order_id": order.id,
        "exchange_order_id": exchange_order_id,
        "order_status": stored_status,
        "fill_id": None,
        "position_id": None,
        "fill_price": None,
        "fill_quantity": 0,
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
        logger.warning(
            "OKX protective order placement failed for %s %s: %s",
            symbol, direction, type(e).__name__,
        )
        return {}

    if ids:
        update_position(position_id, exchange_tpsl_order_id=_json.dumps(ids))
        logger.info("OKX protective orders placed for %s %s: %s", symbol, direction, ids)
    return ids


def replace_okx_exchange_stop(position_id: str, new_stop_price: float) -> None:
    """Cancel the existing OKX SL algo order and place a new one at the tightened
    stop price. Mirrors bitget_broker.replace_exchange_stop.

    #139 fix (OKX side): the trail loop previously only reached Bitget. Non-fatal;
    a failure here is logged and the DB stop stays as-is on the row (the outer
    caller has already updated positions.stop_price for the local check path).
    """
    import json as _json
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if not row or not row.get("exchange_tpsl_order_id"):
        return
    try:
        tpsl_ids = _json.loads(row["exchange_tpsl_order_id"])
    except (ValueError, TypeError):
        return
    if not isinstance(tpsl_ids, dict):
        tpsl_ids = {}

    execution_mode = row.get("execution_mode") or ""
    mode = "demo" if execution_mode == "okx_demo" else "live"
    close_side = "sell" if row["direction"] in ("long", "buy") else "buy"

    try:
        client = OKXClient(mode=mode)
        try:
            old_sl = tpsl_ids.get("sl")
            if old_sl:
                try:
                    _run_coro(client.cancel_algo_order(
                        symbol=row["symbol"],
                        market_type=row.get("market_type") or "usdt_futures",
                        algo_id=old_sl,
                    ))
                except Exception as e:
                    logger.warning("cancel old OKX SL %s failed: %s", old_sl, type(e).__name__)
            new_ids = _run_coro(client.place_algo_order(
                symbol=row["symbol"],
                market_type=row.get("market_type") or "usdt_futures",
                close_side=close_side,
                quantity=float(row["quantity"]),
                stop_price=new_stop_price,
                take_profit_price=None,
            )) or {}
            if new_ids.get("sl"):
                tpsl_ids["sl"] = new_ids["sl"]
                update_position(position_id, exchange_tpsl_order_id=_json.dumps(tpsl_ids))
        finally:
            try:
                _run_coro(client.close())
            except Exception:
                pass
    except Exception as e:  # noqa: BLE001 — never raise from a trail update
        logger.warning(
            "OKX replace_exchange_stop for %s failed: %s",
            position_id, type(e).__name__,
        )


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
    return find_compatible_open_position(
        symbol, market_type, direction, execution_mode, strategy_id
    )


def _claim_close_order(
    position_id: str, reason: str,
) -> tuple[dict, Order | None, str | None]:
    """Atomically claim one OKX close before any exchange side effect."""
    from aletheia.storage import repositories as repo

    with transaction(immediate=True):
        row = repo._select_one("positions", position_id)
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        if row.get("execution_mode") not in ("okx_demo", "okx_live_limited"):
            raise ValueError(
                f"close_okx_position only handles OKX modes, got {row.get('execution_mode')!r}"
            )
        if row.get("status") != "open":
            raise ValueError(
                f"position {position_id} is not open (status={row.get('status')})"
            )
        if (
            row.get("pending_close_order_id")
            and row.get("pending_close_status") in _OPEN_CLOSE_STATES
        ):
            return row, None, None

        requested_at = datetime.now(timezone.utc).isoformat()
        close_order = Order(
            proposal_id=row.get("proposal_id"),
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason=reason,
            symbol=row["symbol"],
            market_type=row["market_type"],
            direction=row["direction"],
            order_type="market",
            quantity=float(row["quantity"]),
            price=row.get("current_price") or row["entry_price"],
            execution_mode=row["execution_mode"],
            status="pending",
        )
        insert_order(close_order)
        update_position(
            position_id,
            pending_close_order_id=close_order.id,
            pending_close_status="pending",
            pending_close_requested_at=requested_at,
        )
        return row, close_order, requested_at


def close_okx_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Close an OKX position with a reduceOnly order.

    Works for both okx_demo and okx_live_limited positions.
    """
    from aletheia.storage import repositories as repo
    from aletheia.portfolio.pnl import close_position

    row, close_order, close_requested_at = _claim_close_order(position_id, reason)
    if close_order is None:
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
    try:
        client = OKXClient(mode=mode)
    except Exception:
        # The durable claim exists, but no exchange method has run. Record a
        # known pre-submission failure so a later close is not suppressed.
        with transaction():
            update_order(
                close_order.id,
                status="error",
                error_reason="close_client_initialization_failed_before_submission",
            )
            update_position(position_id, pending_close_status="error")
        raise
    direction = row["direction"]
    is_long = direction in ("long", "buy")
    side = "sell" if is_long else "buy"
    quantity = float(row["quantity"])

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
        if _is_ambiguous_submission_error(e):
            update_order(
                close_order.id,
                status="submission_unknown",
                error_reason="close_submission_transport_acknowledgement_unknown",
            )
            update_position(position_id, pending_close_status="submission_unknown")
            write_post_commit_audit({
                "actor": "system", "source": "okx_broker",
                "event_type": "close_submission_unknown", "entity_type": "order",
                "entity_id": close_order.id,
                "reason": "transport_acknowledgement_unknown",
                "before": {"status": "pending"},
                "after": {"status": "submission_unknown"},
                "message": "Close transport failed after submission began; automatic retry suppressed",
            })
        else:
            update_order(close_order.id, status="error", error_reason="close_submission_rejected")
            update_position(position_id, pending_close_status="error")
        raise
    finally:
        try:
            _run_coro(client.close())
        except Exception:
            pass

    exchange_order_id = order_resp.order_id or ""
    if not exchange_order_id:
        try:
            update_order(
                close_order.id,
                status="submission_unknown",
                error_reason="close_submission_missing_exchange_id",
            )
            update_position(position_id, pending_close_status="submission_unknown")
        except Exception:
            pass
        write_post_commit_audit({
            "actor": "system", "source": "okx_broker",
            "event_type": "close_submission_unknown", "entity_type": "order",
            "entity_id": close_order.id,
            "reason": "missing_exchange_order_id",
            "before": {"status": "pending"},
            "after": {"status": "submission_unknown"},
            "message": "Exchange acknowledged close without an order id; automatic retry suppressed",
        })
        raise SubmissionUnknownError(client_oid=f"close-{position_id}")
    exchange_reported_fill = order_resp.status in {"filled", "partially_filled"}
    close_status = (
        "materialization_missing"
        if order_resp.status == "filled"
        else "partially_filled"
        if order_resp.status == "partially_filled"
        else "submitted"
    )
    try:
        update_order(
            close_order.id,
            exchange_order_id=exchange_order_id,
            status=close_status,
            error_reason=(
                "private_fill_evidence_pending" if exchange_reported_fill else None
            ),
            filled_at=None,
        )
    except Exception as e:
        try:
            update_position(position_id, pending_close_status="materialization_missing")
        except Exception:
            pass
        raise PostSubmitDBError(
            "exchange close acknowledgement could not be persisted", exchange_order_id,
        ) from e
    update_position(
        position_id,
        pending_close_order_id=close_order.id,
        pending_close_status=close_status,
        pending_close_requested_at=close_requested_at,
    )
    if exchange_reported_fill:
        write_post_commit_audit({
            "actor": "system", "source": "okx_broker",
            "event_type": "close_materialization_pending", "entity_type": "order",
            "entity_id": close_order.id,
            "reason": "private_fill_evidence_pending",
            "before": {"status": "pending"},
            "after": {"status": close_status},
            "message": (
                f"Exchange close {exchange_order_id} reported fill activity; "
                "local closure waits for order-matched private fill evidence"
            ),
        })

    return {
        "order_id": close_order.id,
        "exchange_order_id": exchange_order_id,
        "status": close_status,
        "closed": False,
        "fill_price": None,
        "fill_quantity": 0,
    }
