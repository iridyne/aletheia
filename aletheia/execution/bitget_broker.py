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
Fill price/quantity/fee/time come only from private, order-matched fill history —
never from the aggregate submission response or the request. Aggregate fill activity
remains explicitly unresolved until reconciliation creates the local Fill/Position.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from aletheia.data.symbols import to_bitget
from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest
from aletheia.exchanges.submission_errors import (
    is_ambiguous_submission_error as _is_ambiguous_submission_error,
)
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.post_commit_audit import write_post_commit_audit
from aletheia.storage.models import Order
from aletheia.storage.repositories import (
    get_trade_proposal,
    insert_order,
    transaction,
    update_order,
    update_position,
)
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.bitget_broker")

# Retained as the public default for callers; private fill history supplies the
# actual fee used for durable accounting.
_LIVE_FEE_RATE = 0.0006
_OPEN_CLOSE_STATES = {
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing", "overfilled",
}
_RETRY_SUPPRESSING_ORDER_STATES = (
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)


def _client_mode_for_execution_mode(execution_mode: str) -> str:
    """Map authoritative Order mode to the matching Bitget account."""
    if execution_mode == "bitget_demo":
        return "demo"
    if execution_mode == "bitget_live_limited":
        return "live"
    raise ValueError(f"unsupported Bitget execution mode: {execution_mode!r}")


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


def _same_optional_price(left: float | None, right: object) -> bool:
    if left is None or right is None:
        return left is None and right is None
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return False


def _approved_protection_prices(
    proposal_id: str,
    stop_price: float | None,
    take_profit_price: float | None,
    *,
    require_existing: bool = True,
) -> tuple[float | None, float | None] | None:
    """Return hash-covered Proposal protection only when the caller matches it."""
    proposal = get_trade_proposal(proposal_id)
    if proposal is None:
        if not require_existing:
            return None
        raise ValueError("Bitget order requires an approved Proposal")
    approved_stop = proposal.get("stop_price")
    approved_take_profit = proposal.get("take_profit")
    if not _same_optional_price(stop_price, approved_stop) or not _same_optional_price(
        take_profit_price, approved_take_profit
    ):
        raise ValueError(
            "Bitget protection must exactly match the approved Proposal"
        )
    return approved_stop, approved_take_profit



def _ensure_mix_margin(client, required_usd: float, dry_run: bool = False) -> None:
    """Top up the mix (futures) wallet from UTA before a futures order."""
    if required_usd <= 0 or dry_run:
        return
    try:
        from aletheia.exchanges.bitget.client import DemoFundingUnavailable
        _run_coro(client.transfer(required_usd))
    except DemoFundingUnavailable:
        raise
    except Exception as e:
        logger.warning("mix margin precheck skipped: %s", type(e).__name__)

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
    """Place a real Bitget order and persist its acknowledgement locally.

    `execution_mode` selects both the authoritative Order record and the Bitget
    account (`bitget_live_limited` real funds, `bitget_demo` simulated). Mutable
    exchange config is not allowed to reroute an already-authorized Order. The
    aggregate submission response is never accounting evidence: private,
    order-matched fill history is the only path that may create Fill/Position.
    """
    _guard_unresolved_submission(proposal_id, execution_mode)
    approved_protection = _approved_protection_prices(
        proposal_id,
        stop_price,
        take_profit_price,
        require_existing=False,
    )
    if market_type != "spot" and leverage is None:
        raise ValueError(
            f"futures order for {symbol!r} ({market_type}) requires leverage but none was provided"
        )
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
    if approved_protection is None:
        # Some controlled callers create the authority atomically at the Order
        # persistence boundary. Production's insert_order fails closed if no
        # Proposal exists; this second read obtains exactly what it validated.
        approved_protection = _approved_protection_prices(
            proposal_id,
            stop_price,
            take_profit_price,
        )
    approved_stop_price, approved_take_profit_price = approved_protection
    try:
        client = BitgetClient(
            mode=_client_mode_for_execution_mode(order.execution_mode),
        )
    except Exception:
        # No exchange method has run yet, so this is a known pre-submission
        # failure.  Do not strand a retry-suppressing ``pending`` Order merely
        # because credentials/client setup could not be loaded.
        update_order(
            order.id,
            status="error",
            error_reason="order_client_initialization_failed_before_submission",
        )
        raise

    required_margin = 0.0
    if market_type != 'spot' and quantity and price and leverage:
        required_margin = float(quantity) * float(price) / float(leverage)
    if market_type != 'spot' and required_margin > 0:
        try:
            _ensure_mix_margin(client, required_margin)
        except Exception:
            # This happens before submission, so the outcome is known: no
            # exchange order exists.  Persist a stable, non-sensitive reason.
            update_order(
                order.id,
                status="error",
                error_reason="margin_preparation_failed_before_submission",
            )
            raise

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=exchange_symbol, market_type=market_type, side=side,
            order_type=order_type, quantity=quantity,
            price=price if order_type == "limit" else None,
            client_oid=proposal_id,
            preset_stop_loss_price=approved_stop_price,
            preset_take_profit_price=approved_take_profit_price,
            leverage=leverage,
        )))
    except Exception as e:
        if _is_ambiguous_submission_error(e):
            update_order(
                order.id,
                status="submission_unknown",
                error_reason="order_submission_transport_acknowledgement_unknown",
            )
            write_post_commit_audit({
                "actor": "system", "source": "bitget_broker",
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
            "actor": "system", "source": "bitget_broker",
            "event_type": "order_submission_unknown", "entity_type": "order",
            "entity_id": order.id,
            "reason": "missing_exchange_order_id",
            "before": {"status": "pending"},
            "after": {"status": "submission_unknown"},
            "message": "Exchange acknowledged submission without an order id; automatic retry suppressed",
        })
        raise SubmissionUnknownError(client_oid=proposal_id)
    # The create-order/detail response is only an acknowledgement handoff. It
    # does not carry authoritative per-fill identity, fee, currency, or time, so
    # even a reported fill must wait for exact private-history reconciliation.
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
            filled_at=None,
        )
    except Exception as e:
        raise PostSubmitDBError(
            "exchange acknowledgement could not be persisted", exchange_order_id,
        ) from e
    if exchange_reported_fill:
        write_post_commit_audit({
            "actor": "system", "source": "bitget_broker",
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


def _claim_close_order(
    position_id: str, reason: str,
) -> tuple[dict, Order | None, str | None]:
    """Atomically claim one Bitget close before any exchange side effect."""
    from aletheia.storage import repositories as repo

    with transaction(immediate=True):
        row = repo._select_one("positions", position_id)
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        if row.get("execution_mode") not in ("bitget_demo", "bitget_live_limited"):
            raise ValueError(
                f"close_demo_position only handles Bitget modes, got {row.get('execution_mode')!r}"
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


def close_demo_position(position_id: str, reason: str = "exit_rule") -> dict:
    """Submit one Bitget hedge-mode close and persist its acknowledgement.

    Hedge-mode close (DECISIONS.md 2026-06-09): tradeSide:"close" + holdSide (the
    position's side), with `side` SAME as the original open side — close a long =
    side:"buy", holdSide:"long". The aggregate response never closes the local
    Position or creates a Fill; exact private fill history performs that durable
    materialization later. Exchange rejection likewise leaves the Position open.
    """
    from aletheia.storage import repositories as repo

    row, close_order, close_requested_at = _claim_close_order(position_id, reason)
    if close_order is None:
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

    # Derive the client mode from the POSITION's execution_mode, not mutable config:
    # a bitget_live_limited position must close on the live account even if the operator
    # has since flipped the configured mode (mirrors close_testnet_position).
    try:
        client = BitgetClient(
            mode=_client_mode_for_execution_mode(row["execution_mode"])
        )
    except Exception:
        # The close claim was persisted before client construction. Since no
        # exchange call began, release its retry suppression as a known error.
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
    hold_side = "long" if is_long else "short"
    # Close `side` mirrors the OPEN side (verified empirically): close long = buy.
    side = "buy" if is_long else "sell"
    quantity = float(row["quantity"])
    exchange_symbol = to_bitget(row["symbol"])

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=exchange_symbol, market_type=row["market_type"], side=side,
            order_type="market", quantity=quantity,
            client_oid=f"close-{position_id}",
            hold_side=hold_side,
        )))
    except Exception as e:
        if _is_ambiguous_submission_error(e):
            # A transport timeout cannot tell us whether the exchange accepted
            # the close.  Keep it unresolved and suppress automatic resubmission.
            update_order(
                close_order.id,
                status="submission_unknown",
                error_reason="close_submission_transport_acknowledgement_unknown",
            )
            update_position(position_id, pending_close_status="submission_unknown")
            write_post_commit_audit({
                "actor": "system", "source": "bitget_broker",
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
            "actor": "system", "source": "bitget_broker",
            "event_type": "close_submission_unknown", "entity_type": "order",
            "entity_id": close_order.id,
            "reason": "missing_exchange_order_id",
            "before": {"status": "pending"},
            "after": {"status": "submission_unknown"},
            "message": "Exchange acknowledged close without an order id; automatic retry suppressed",
        })
        raise SubmissionUnknownError(client_oid=f"close-{position_id}")
    exchange_reported_fill = order_resp.status in {"filled", "partially_filled"}
    if order_resp.status == "filled":
        close_status = "materialization_missing"
    elif order_resp.status == "partially_filled":
        close_status = "partially_filled"
    else:
        close_status = "submitted"

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
            "actor": "system", "source": "bitget_broker",
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
        "close_order_id": close_order.id,
        "status": close_status,
        "closed": False,
        "position_id": position_id,
        "fill_price": None,
        "fill_quantity": 0,
    }


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
            logger.warning("TPSL SL placement failed for %s: %s", position_id, type(e).__name__)

    if take_profit_price:
        try:
            tp_id = _run_coro(client.place_tpsl_order(
                symbol=exchange_symbol, plan_type="pos_profit",
                trigger_price=take_profit_price, hold_side=hold_side, size=size,
            ))
            if tp_id:
                tpsl_ids["tp"] = tp_id
        except Exception as e:
            logger.warning("TPSL TP placement failed for %s: %s", position_id, type(e).__name__)

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
                logger.warning("cancel TPSL %s %s failed: %s", key, oid, type(e).__name__)


def replace_exchange_stop(position_id: str, new_stop_price: float) -> None:
    """Cancel old SL trigger and place a new one at the updated stop price."""
    import json as _json
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if not row or not row.get("exchange_tpsl_order_id"):
        return
    client = BitgetClient(
        mode=_client_mode_for_execution_mode(row.get("execution_mode"))
    )
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
            logger.warning("cancel old SL %s failed: %s", old_sl, type(e).__name__)

    try:
        new_sl_id = _run_coro(client.place_tpsl_order(
            symbol=exchange_symbol, plan_type="pos_loss",
            trigger_price=new_stop_price, hold_side=hold_side,
            size=float(row["quantity"]),
        ))
        tpsl_ids["sl"] = new_sl_id
        update_position(position_id, exchange_tpsl_order_id=_json.dumps(tpsl_ids))
    except Exception as e:
        logger.warning("replace SL for %s failed: %s", position_id, type(e).__name__)
