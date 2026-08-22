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
from datetime import datetime, timezone

from aletheia.data.symbols import to_binance, to_ccxt_perp
from aletheia.exchanges.binance.client import BinanceClient, OrderRequest
from aletheia.exchanges.submission_errors import (
    ClosePositionAbsentError,
    is_ambiguous_submission_error as _is_ambiguous_submission_error,
)
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.close_convergence import (
    CLOSE_CLAIM_STATES,
    has_unresolved_close_claim,
)
from aletheia.execution.post_commit_audit import write_post_commit_audit
from aletheia.execution.position_enrichment import build_entry_enrichment, liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    find_compatible_open_position,
    get_order,
    insert_order, insert_fill, insert_position, update_position,
    update_order, transaction,
)
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.binance_broker")

_TESTNET_FEE_RATE = 0.0004  # Binance futures taker fee
_BINANCE_ACCOUNT_MODES = {
    "binance_testnet": "testnet",
    "binance_live_limited": "live",
}
_OPEN_CLOSE_STATES = set(CLOSE_CLAIM_STATES)
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
    """Return complete, account- and order-matched Binance private trades.

    The normalized rows contain actual per-trade fee and exchange time; no
    requested or aggregate response economics are substituted.
    """
    execution_mode = str(order.get("execution_mode") or "")
    account_mode = _BINANCE_ACCOUNT_MODES.get(execution_mode)
    if account_mode is None:
        raise ValueError("Binance reconciliation account mismatch")
    if order.get("market_type") != "usdt_futures":
        raise ValueError("Binance reconciliation market type unsupported")
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()
    if not exchange_order_id and not client_order_id:
        raise ValueError("Binance exchange order identity unavailable")

    client = BinanceClient(mode=account_mode)
    try:
        exchange = client._ensure_exchange()
        await exchange.load_markets()
        ccxt_symbol = to_ccxt_perp(str(order["symbol"]))
        start_ms = _created_at_ms(order.get("created_at"))
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        raw_fills: list[dict] = []
        from_id: str | None = None
        complete = False
        for _ in range(100):
            params: dict = {"endTime": end_ms}
            since = start_ms if from_id is None else None
            if from_id is not None:
                params["fromId"] = from_id
            page = await exchange.fetch_my_trades(
                ccxt_symbol,
                since=since,
                limit=1000,
                params=params,
            )
            if not isinstance(page, list):
                raise ValueError("Binance private trade history malformed")
            raw_fills.extend(row for row in page if isinstance(row, dict))
            if len(page) < 1000:
                complete = True
                break
            last_id = str(page[-1].get("id") or "")
            if not last_id.isdigit():
                raise RuntimeError("Binance private trade pagination incomplete")
            next_id = str(int(last_id) + 1)
            if next_id == from_id:
                raise RuntimeError("Binance private trade pagination stalled")
            from_id = next_id
        if not complete:
            raise RuntimeError("Binance private trade history incomplete")

        normalized: list[dict] = []
        recovered_order_ids: set[str] = set()
        for row in raw_fills:
            info = row.get("info") if isinstance(row.get("info"), dict) else {}
            remote_order_id = str(row.get("order") or info.get("orderId") or "")
            remote_client_id = str(info.get("clientOrderId") or "")
            if exchange_order_id:
                matched_identity = remote_order_id == exchange_order_id
            else:
                matched_identity = remote_client_id == client_order_id
            if not matched_identity:
                continue
            if not remote_order_id:
                raise ValueError("Binance order-matched private trade is incomplete")
            returned_symbol = str(row.get("symbol") or "").strip()
            if returned_symbol and returned_symbol != ccxt_symbol:
                raise ValueError("Binance order-matched private trade symbol mismatch")
            recovered_order_ids.add(remote_order_id)
            fee_data = row.get("fee") if isinstance(row.get("fee"), dict) else {}
            fill_id = str(row.get("id") or info.get("id") or "").strip()
            try:
                price = float(row.get("price") or info.get("price"))
                quantity = float(row.get("amount") or info.get("qty"))
                timestamp_ms = int(row.get("timestamp") or info.get("time"))
                raw_fee = (
                    fee_data.get("cost")
                    if fee_data.get("cost") not in (None, "")
                    else info.get("commission")
                )
                fee = float(raw_fee)
            except (TypeError, ValueError):
                raise ValueError("Binance order-matched private trade is incomplete")
            fee_currency = str(
                fee_data.get("currency") or info.get("commissionAsset") or ""
            ).strip()
            if (
                not fill_id
                or price <= 0
                or quantity <= 0
                or timestamp_ms <= 0
                or not fee_currency
            ):
                raise ValueError("Binance order-matched private trade is incomplete")
            normalized.append({
                "orderId": remote_order_id,
                "tradeId": fill_id,
                "clientOid": str(info.get("clientOrderId") or "") or None,
                "fillPrice": str(price),
                "fillQuantity": str(quantity),
                "fee": str(fee),
                "feeCoin": fee_currency,
                "cTime": str(timestamp_ms),
            })
        if len(recovered_order_ids) > 1:
            raise ValueError("Binance client order identity is ambiguous")
        return normalized
    finally:
        try:
            await client.close()
        except Exception as exc:
            logger.warning(
                "Binance reconciliation client close failed (%s)",
                type(exc).__name__,
            )


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
    _guard_unresolved_submission(proposal_id, execution_mode)
    if market_type != "spot" and leverage is None:
        raise ValueError(
            f"futures order for {symbol!r} ({market_type}) requires leverage but none was provided"
        )
    mode = "live" if execution_mode == "binance_live_limited" else "testnet"
    client = BinanceClient(mode=mode)
    side = "buy" if direction in ("long", "buy") else "sell"
    exchange_symbol = to_binance(symbol)  # noqa: F841 — kept for logging

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
                "actor": "system", "source": "binance_broker",
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
            "actor": "system", "source": "binance_broker",
            "event_type": "order_submission_unknown", "entity_type": "order",
            "entity_id": order.id,
            "reason": "missing_exchange_order_id",
            "before": {"status": "pending"},
            "after": {"status": "submission_unknown"},
            "message": "Exchange acknowledged submission without an order id; automatic retry suppressed",
        })
        raise SubmissionUnknownError(client_oid=proposal_id)
    # #1502: explicit terminal rejection from the venue is NOT submission_unknown.
    if order_resp.status in ("cancelled", "rejected", "expired") and exchange_order_id:
        try:
            update_order(order.id, exchange_order_id=exchange_order_id, status="cancelled",
                         error_reason="explicit_venue_rejection")
        except Exception:
            pass
        write_post_commit_audit({
            "actor": "system", "source": "binance_broker",
            "event_type": "order_explicitly_rejected", "entity_type": "order",
            "entity_id": order.id,
            "reason": f"venue_status:{order_resp.status}",
            "after": {"status": "cancelled"},
        })
        return
    exchange_reported_fill = order_resp.status in ("filled", "partially_filled")
    stored_status = "materialization_missing" if exchange_reported_fill else "submitted"
    # #1553: late ack must not regress an Order that reconciliation already resolved.
    _UNRESOLVED = {"pending", "submission_unknown"}
    try:
        current = get_order(order.id)
        if current is not None and current.get("status") not in _UNRESOLVED:
            logger.debug(
                "binance_broker: discarding late ack for order %s (current status: %s)",
                order.id, current.get("status"),
            )
            return
    except Exception:
        pass
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
            "actor": "system", "source": "binance_broker",
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
    """Atomically claim one Binance close before any exchange side effect."""
    from aletheia.storage import repositories as repo

    with transaction(immediate=True):
        row = repo._select_one("positions", position_id)
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        if row.get("execution_mode") not in (
            "binance_testnet", "binance_live_limited",
        ):
            raise ValueError(
                "close_testnet_position only handles Binance modes, "
                f"got {row.get('execution_mode')!r}"
            )
        if row.get("status") != "open":
            raise ValueError(
                f"position {position_id} is not open (status={row.get('status')})"
            )
        if has_unresolved_close_claim(row):
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


def close_testnet_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Close a Binance position (testnet OR live-limited) with a reduceOnly order.

    Unlike Bitget's hedge-mode close, Binance uses reduceOnly=True with the opposite
    side (close long = sell reduceOnly, close short = buy reduceOnly). A local-only
    close would leave a dangling real position on the exchange (§1.3 hazard); on
    `binance_live_limited` that dangling position holds REAL funds.
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

    mode = "live" if row["execution_mode"] == "binance_live_limited" else "testnet"
    try:
        client = BinanceClient(mode=mode)
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
    # Close = opposite side + reduceOnly
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
        if isinstance(e, ClosePositionAbsentError):
            with transaction():
                update_order(
                    close_order.id,
                    status="error",
                    error_reason="close_position_absence_unverified",
                )
                update_position(
                    position_id,
                    pending_close_status="position_absence_unverified",
                )
            write_post_commit_audit({
                "actor": "system", "source": "binance_broker",
                "event_type": "close_position_absence_unverified",
                "entity_type": "position", "entity_id": position_id,
                "reason": "venue_reported_no_position_for_close",
                "before": {"pending_close_status": "pending"},
                "after": {
                    "pending_close_status": "position_absence_unverified",
                    "pending_close_order_id": close_order.id,
                },
                "message": (
                    "Venue rejected close because no matching position was available; "
                    "retained claim for exact reconciliation"
                ),
            })
        elif _is_ambiguous_submission_error(e):
            update_order(
                close_order.id,
                status="submission_unknown",
                error_reason="close_submission_transport_acknowledgement_unknown",
            )
            update_position(position_id, pending_close_status="submission_unknown")
            write_post_commit_audit({
                "actor": "system", "source": "binance_broker",
                "event_type": "close_submission_unknown", "entity_type": "order",
                "entity_id": close_order.id,
                "reason": "transport_acknowledgement_unknown",
                "before": {"status": "pending"},
                "after": {"status": "submission_unknown"},
                "message": "Close transport failed after submission began; automatic retry suppressed",
            })
        else:
            update_order(close_order.id, status="error", error_reason="close_submission_rejected")
            update_position(position_id, pending_close_status="submission_rejected")
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
            "actor": "system", "source": "binance_broker",
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
            "actor": "system", "source": "binance_broker",
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
