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
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from aletheia.data.symbols import to_bitget
from aletheia.exchanges.bitget.client import BitgetClient, OrderRequest
from aletheia.exchanges.submission_errors import (
    ClosePositionAbsentError,
    is_ambiguous_submission_error as _is_ambiguous_submission_error,
)
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.broker_utils import (
    close_acknowledgement_result,
    entry_acknowledgement_result,
)
from aletheia.execution.close_convergence import (
    CLOSE_CLAIM_STATES,
    has_unresolved_close_claim,
)
from aletheia.execution.post_commit_audit import write_post_commit_audit
from aletheia.storage.models import Order
from aletheia.storage.repositories import (
    finalize_exchange_acknowledgement,
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
_OPEN_CLOSE_STATES = set(CLOSE_CLAIM_STATES)
_RETRY_SUPPRESSING_ORDER_STATES = (
    "pending", "submitted", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)

_CLOSE_CLIP_MIN_FRACTION = 1.0 / 64.0
_VENUE_MATCH_CAP_CANCEL_REASONS = frozenset({
    "trade_too_many_times_cancel",
    "operator_authorized_cleanup_trade_rate_limited",
})
# Marketable-limit fallback bound through the last price once a symbol's demo
# market path is confirmed broken (XRPUSDT 2026-08-22: every market order at
# any size — including a 5-unit diagnostic entry — was zero-fill cancelled with
# trade_too_many_times_cancel while limit orders filled normally).
_MARKETABLE_LIMIT_TOLERANCE = 0.01


def _venue_match_cap_cancel_count(position_id: str) -> int:
    from aletheia.storage import repositories as repo

    row = repo.get_db().execute(
        "SELECT COUNT(*) AS n FROM orders "
        "WHERE position_id = ? AND close_reason IS NOT NULL "
        "AND status = 'cancelled' AND error_reason IN (?, ?)",
        (position_id, *_VENUE_MATCH_CAP_CANCEL_REASONS),
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def _quantize_to_reference_precision(bound: float, reference: float, *, up: bool) -> float:
    """Quantize a price bound onto the reference price's own tick precision."""
    text = f"{reference:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    scale = 10 ** decimals
    scaled = bound * scale
    scaled = math.ceil(scaled - 1e-9) if up else math.floor(scaled + 1e-9)
    return scaled / scale


def _quantize_quantity_to_precision(clip: float, reference: float) -> float:
    """Floor a clip quantity onto the reference quantity's lot precision."""
    text = f"{reference:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    scale = 10 ** decimals
    return math.floor(clip * scale + 1e-9) / scale


def _clipped_close_quantity(position_id: str, remaining: float) -> float:
    """Clip the next close order below sizes the venue has already refused."""
    clip = float(remaining)
    for _ in range(_venue_match_cap_cancel_count(position_id)):
        clip /= 2.0
    clip = max(clip, remaining * _CLOSE_CLIP_MIN_FRACTION)
    return round(clip, 8)


def _client_mode_for_execution_mode(execution_mode: str) -> str:
    """Map authoritative Order mode to the matching Bitget account."""
    if execution_mode == "bitget_demo":
        return "demo"
    if execution_mode == "bitget_live_limited":
        return "live"
    raise ValueError(f"unsupported Bitget execution mode: {execution_mode!r}")


def _configured_bitget_profile() -> str:
    """Return the configured Bitget profile (classic_v2 | uta_v3).

    Delegates to the shared resolver in ``exchanges.bitget.profile_config`` so
    every construction site (execution, sync, reconciliation, adapters) reads
    one source of truth and never drifts apart.
    """
    from aletheia.exchanges.bitget.profile_config import configured_bitget_profile

    return configured_bitget_profile()


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
            profile=_configured_bitget_profile(),
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
            acknowledgement = finalize_exchange_acknowledgement(
                order.id,
                exchange_order_id=None,
                status="submission_unknown",
                error_reason="order_submission_transport_acknowledgement_unknown",
            )
            if not acknowledgement["applied"]:
                return entry_acknowledgement_result(acknowledgement)
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
        acknowledgement = finalize_exchange_acknowledgement(
            order.id,
            exchange_order_id=None,
            status="error",
            error_reason="order_submission_rejected",
        )
        if not acknowledgement["applied"]:
            return entry_acknowledgement_result(acknowledgement)
        raise

    exchange_order_id = order_resp.order_id or ""
    if not exchange_order_id:
        acknowledgement = finalize_exchange_acknowledgement(
            order.id,
            exchange_order_id=None,
            status="submission_unknown",
            error_reason="order_submission_missing_exchange_id",
        )
        if not acknowledgement["applied"]:
            return entry_acknowledgement_result(acknowledgement)
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
        acknowledgement = finalize_exchange_acknowledgement(
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
    if exchange_reported_fill and acknowledgement["applied"]:
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

    return entry_acknowledgement_result(acknowledgement)


def _claim_close_order(
    position_id: str,
    reason: str,
    *,
    close_reasoning: str | None = None,
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
        if has_unresolved_close_claim(row):
            return row, None, None

        requested_at = datetime.now(timezone.utc).isoformat()
        close_order = Order(
            proposal_id=row.get("proposal_id"),
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason=reason,
            close_reasoning=close_reasoning,
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


def close_demo_position(
    position_id: str,
    reason: str = "exit_rule",
    *,
    close_reasoning: str | None = None,
) -> dict:
    """Submit one Bitget hedge-mode close and persist its acknowledgement.

    Hedge-mode close (DECISIONS.md 2026-06-09): tradeSide:"close" + holdSide (the
    position's side), with `side` SAME as the original open side — close a long =
    side:"buy", holdSide:"long". The aggregate response never closes the local
    Position or creates a Fill; exact private fill history performs that durable
    materialization later. Exchange rejection likewise leaves the Position open.
    """
    from aletheia.storage import repositories as repo

    row, close_order, close_requested_at = _claim_close_order(
        position_id,
        reason,
        close_reasoning=close_reasoning,
    )
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
            mode=_client_mode_for_execution_mode(row["execution_mode"]),
            profile=_configured_bitget_profile(),
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
    remaining_quantity = float(row["quantity"])
    exchange_symbol = to_bitget(row["symbol"])
    order_type = "market"
    limit_price = None
    quantity = remaining_quantity
    match_cap_cancels = _venue_match_cap_cancel_count(position_id)
    if match_cap_cancels > 0:
        # The demo venue can break an entire symbol's market path while its
        # limit path stays healthy (XRPUSDT 2026-08-22). After the first
        # zero-fill match-cap cancel, submit the close as a marketable GTC
        # limit: the bound caps price like a market order would, the unfilled
        # remainder rests, and partial-close materialization converges it —
        # so the FULL remaining quantity is safe here; clipping is only a
        # mitigation for runaway market sweeps.
        try:
            ticker = _run_coro(client.get_ticker(
                exchange_symbol, market_type=row["market_type"],
            ))
            reference = float(getattr(ticker, "last_price", 0.0) or 0.0)
            if reference > 0:
                # The UTA client flips hedge-mode closes to the side OPPOSITE
                # the position (close short = venue buy), so bound the limit
                # through the far touch on the venue side, not the broker-side
                # mirror passed in OrderRequest.side. The bound is quantized
                # onto the reference price's tick precision — the venue rejects
                # prices off its tick grid ("multiple of 0.0001").
                venue_buy = not is_long
                factor = (
                    1.0 + _MARKETABLE_LIMIT_TOLERANCE if venue_buy
                    else 1.0 - _MARKETABLE_LIMIT_TOLERANCE
                )
                limit_price = _quantize_to_reference_precision(
                    reference * factor, reference, up=not venue_buy,
                )
                order_type = "limit"
                logger.info(
                    "marketable-limit Bitget close for %s at %s covering %s "
                    "remaining (last=%s, market path venue-cancelled previously)",
                    position_id, limit_price, remaining_quantity, reference,
                )
        except Exception as exc:
            logger.warning(
                "marketable-limit pricing unavailable for %s close; falling "
                "back to clipped market order: %s", position_id, exc,
            )
    if order_type == "market" and match_cap_cancels > 0:
        # Market fallback on a venue with match-cap history: clip below sizes
        # the venue has already refused, lot-quantized like the venue floors.
        # The claimed Order row was written with the full remaining quantity;
        # align it with the clip actually submitted so materialization's
        # order-completeness check compares against the real clip.
        quantity = _quantize_quantity_to_precision(
            _clipped_close_quantity(position_id, remaining_quantity),
            remaining_quantity,
        )
        if quantity < remaining_quantity:
            with transaction():
                update_order(close_order.id, quantity=quantity)
            logger.info(
                "clipped Bitget close for %s: submitting %s of %s remaining "
                "(venue match-cap cancel history)",
                position_id, quantity, remaining_quantity,
            )

    try:
        order_resp = _run_coro(client.place_order(OrderRequest(
            symbol=exchange_symbol, market_type=row["market_type"], side=side,
            order_type=order_type, quantity=quantity, price=limit_price,
            client_oid=f"close-{position_id}",
            hold_side=hold_side,
        )))
    except Exception as e:
        if isinstance(e, ClosePositionAbsentError):
            # The new order is definitely rejected, but the local Position now
            # conflicts with venue evidence. Retain one durable claim while the
            # read-only position reconciler establishes whether this was a prior
            # fill, an external close, an identity mismatch, or unavailable evidence.
            acknowledgement = finalize_exchange_acknowledgement(
                close_order.id,
                exchange_order_id=None,
                status="error",
                error_reason="close_position_absence_unverified",
                close_position_id=position_id,
                pending_close_status="position_absence_unverified",
            )
            if not acknowledgement["applied"]:
                return close_acknowledgement_result(acknowledgement)
            write_post_commit_audit({
                "actor": "system", "source": "bitget_broker",
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
            # A transport timeout cannot tell us whether the exchange accepted
            # the close.  Keep it unresolved and suppress automatic resubmission.
            acknowledgement = finalize_exchange_acknowledgement(
                close_order.id,
                exchange_order_id=None,
                status="submission_unknown",
                error_reason="close_submission_transport_acknowledgement_unknown",
                close_position_id=position_id,
                pending_close_status="submission_unknown",
            )
            if not acknowledgement["applied"]:
                return close_acknowledgement_result(acknowledgement)
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
            acknowledgement = finalize_exchange_acknowledgement(
                close_order.id,
                exchange_order_id=None,
                status="error",
                error_reason="close_submission_rejected",
                close_position_id=position_id,
                pending_close_status="submission_rejected",
            )
            if not acknowledgement["applied"]:
                return close_acknowledgement_result(acknowledgement)
        raise

    exchange_order_id = order_resp.order_id or ""
    if not exchange_order_id:
        acknowledgement = finalize_exchange_acknowledgement(
            close_order.id,
            exchange_order_id=None,
            status="submission_unknown",
            error_reason="close_submission_missing_exchange_id",
            close_position_id=position_id,
            pending_close_status="submission_unknown",
        )
        if not acknowledgement["applied"]:
            return close_acknowledgement_result(acknowledgement)
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
        acknowledgement = finalize_exchange_acknowledgement(
            close_order.id,
            exchange_order_id=exchange_order_id,
            status=close_status,
            error_reason=(
                "private_fill_evidence_pending" if exchange_reported_fill else None
            ),
            close_position_id=position_id,
            pending_close_status=close_status,
        )
    except Exception as e:
        raise PostSubmitDBError(
            "exchange close acknowledgement could not be persisted", exchange_order_id,
        ) from e
    if exchange_reported_fill and acknowledgement["applied"]:
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
    return close_acknowledgement_result(acknowledgement)


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
        mode=_client_mode_for_execution_mode(row.get("execution_mode")),
        profile=_configured_bitget_profile(),
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
