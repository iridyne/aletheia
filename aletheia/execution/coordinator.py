"""Durable local mechanics between the Gateway and one selected UTA account."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from aletheia.execution.broker_utils import (
    close_acknowledgement_result,
    entry_acknowledgement_result,
)
from aletheia.execution.close_convergence import has_unresolved_close_claim
from aletheia.execution.errors import PostSubmitDBError, SubmissionUnknownError
from aletheia.execution.post_commit_audit import write_post_commit_audit
from aletheia.storage.models import Order
from aletheia.storage.repositories import (
    claim_position_close,
    finalize_exchange_acknowledgement,
    insert_order,
    transaction,
    transition_entry_plan,
    transition_order,
)
from aletheia.storage.sqlite import get_db
from aletheia.uta.compat import instrument_from_local
from aletheia.uta.manager import (
    create_selected_uta_mutation_adapter,
    require_selected_uta_mutation,
)
from aletheia.uta.models import (
    CancelOrderCommand,
    CommandEvidence,
    CapabilitySupport,
    ModifyOrderCommand,
    OrderLifecycle,
    SubmitOrderCommand,
)


_RETRY_SUPPRESSING_ORDER_STATES = (
    "pending",
    "submitted",
    "partial",
    "partially_filled",
    "submission_unknown",
    "materialization_missing",
)


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _create_qualified_adapter(
    execution_mode: str,
    *,
    expected_account=None,
    expected_generation: int | None = None,
):
    if expected_account is not None:
        adapter = create_selected_uta_mutation_adapter(
            execution_mode,
            expected_generation=expected_account.credential_generation,
        )
    elif expected_generation is not None:
        adapter = create_selected_uta_mutation_adapter(
            execution_mode, expected_generation=expected_generation
        )
    else:
        adapter = create_selected_uta_mutation_adapter(execution_mode)
    if expected_account is not None and adapter.identity != expected_account:
        from aletheia.trust.uta_qualification import UTAQualificationError

        raise UTAQualificationError("uta_account_generation_changed")
    return adapter


async def _command(execution_mode: str, method: str, command):
    adapter = _create_qualified_adapter(
        execution_mode, expected_account=command.expected_account
    )
    try:
        return await getattr(adapter, method)(command)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


def _guard_unresolved_submission(proposal_id: str, execution_mode: str) -> None:
    placeholders = ",".join("?" for _ in _RETRY_SUPPRESSING_ORDER_STATES)
    row = (
        get_db()
        .execute(
            f"""
        SELECT exchange_order_id, status FROM orders
        WHERE proposal_id=? AND execution_mode=? AND status IN ({placeholders})
        ORDER BY rowid LIMIT 1
        """,
            (proposal_id, execution_mode, *_RETRY_SUPPRESSING_ORDER_STATES),
        )
        .fetchone()
    )
    if row is None:
        return
    if row["status"] == "materialization_missing":
        raise PostSubmitDBError("exchange fill awaits local materialization", row["exchange_order_id"])
    raise SubmissionUnknownError(exchange_order_id=row["exchange_order_id"], client_oid=proposal_id)


def _status(report, *, close_position_id: str | None = None) -> tuple[str, str | None]:
    if report.evidence is CommandEvidence.AMBIGUOUS:
        return "submission_unknown", "command_acknowledgement_unknown"
    if report.evidence is CommandEvidence.NOT_SENT:
        return "error", report.reason_code or "command_not_sent"
    if report.evidence is CommandEvidence.VENUE_REJECTED:
        if close_position_id and not getattr(report, "venue_order_id", None):
            return "error", report.reason_code or "venue_rejected_command"
        return "submission_rejected", report.reason_code or "venue_rejected_command"
    state = report.order.state if report.order is not None else OrderLifecycle.ACCEPTED
    if state is OrderLifecycle.FILLED:
        return "materialization_missing", "private_fill_evidence_pending"
    if state is OrderLifecycle.PARTIALLY_FILLED:
        return "partially_filled", "private_fill_evidence_pending"
    if state in {OrderLifecycle.CANCELED, OrderLifecycle.EXPIRED, OrderLifecycle.REJECTED}:
        return "cancelled", "explicit_venue_rejection"
    if state in {OrderLifecycle.ACCEPTED, OrderLifecycle.OPEN}:
        return "submitted", None
    return "submission_unknown", "unrecognized_venue_order_status"


def _apply_report(order: Order, report, *, close_position_id: str | None = None) -> dict:
    status, reason = _status(report, close_position_id=close_position_id)
    pending_close_status = (
        "submission_rejected" if close_position_id and report.evidence is CommandEvidence.VENUE_REJECTED else status
    )
    try:
        acknowledgement = finalize_exchange_acknowledgement(
            order.id,
            exchange_order_id=report.venue_order_id,
            status=status,
            error_reason=reason,
            close_position_id=close_position_id,
            pending_close_status=pending_close_status if close_position_id else None,
        )
    except Exception as exc:
        raise PostSubmitDBError("UTA acknowledgement could not be persisted", report.venue_order_id) from exc
    if acknowledgement["applied"]:
        write_post_commit_audit(
            {
                "actor": "system",
                "source": "execution_coordinator",
                "event_type": "uta_command_applied",
                "entity_type": "order",
                "entity_id": order.id,
                "reason": reason,
                "before": {"status": "pending"},
                "after": {"status": status, "command_evidence": report.evidence.value},
            }
        )
    else:
        return acknowledgement
    if report.evidence is CommandEvidence.AMBIGUOUS or status == "submission_unknown":
        raise SubmissionUnknownError(
            exchange_order_id=report.venue_order_id,
            client_oid=order.client_order_id,
        )
    if report.evidence in {CommandEvidence.NOT_SENT, CommandEvidence.VENUE_REJECTED}:
        raise RuntimeError(reason or "UTA command failed")
    return acknowledgement


def execute_order(
    *,
    proposal_id: str,
    risk_decision_id: str | None,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    execution_mode: str,
    leverage: int | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    time_in_force: str | None = None,
    post_only: bool = False,
    entry_plan_id: str | None = None,
    order_tactic: str | None = None,
    **_unused,
) -> dict:
    """Commit pending intent, send one UTA command, then CAS the report."""
    _guard_unresolved_submission(proposal_id, execution_mode)
    if market_type != "spot" and leverage is None:
        raise ValueError("futures UTA order requires leverage")
    selected = require_selected_uta_mutation(execution_mode)
    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        client_order_id=proposal_id,
        entry_plan_id=entry_plan_id,
        order_tactic=order_tactic,
        time_in_force=time_in_force,
        post_only=post_only,
        requested_liquidity=(
            "maker"
            if post_only
            else "taker"
            if str(time_in_force or "").lower() == "ioc"
            else None
        ),
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        status="pending",
    )
    with transaction(immediate=True):
        insert_order(order)
        if entry_plan_id and not transition_entry_plan(
            entry_plan_id,
            expected_status="risk_reserved",
            status="ordered",
            order_id=order.id,
        ):
            raise RuntimeError("entry_plan_order_transition_conflict")
    instrument = instrument_from_local(symbol, market_type)
    try:
        adapter = _create_qualified_adapter(
            execution_mode,
            expected_generation=(
                selected.identity.credential_generation if selected else None
            ),
        )
    except Exception:
        finalize_exchange_acknowledgement(
            order.id,
            exchange_order_id=None,
            status="error",
            error_reason="uta_adapter_unavailable_before_submission",
        )
        raise
    command = SubmitOrderCommand(
        command_id=str(uuid4()),
        expected_account=adapter.identity,
        instrument=instrument,
        purpose="entry",
        proposal_id=proposal_id,
        client_order_id=proposal_id,
        side="buy" if direction in {"long", "buy"} else "sell",
        quantity=Decimal(str(quantity)),
        order_type=order_type,
        limit_price=Decimal(str(price)) if order_type == "limit" else None,
        stop_loss_price=(
            Decimal(str(stop_price))
            if stop_price is not None
            and adapter.capabilities.native_tpsl is CapabilitySupport.SUPPORTED
            else None
        ),
        take_profit_price=(
            Decimal(str(take_profit_price))
            if take_profit_price is not None
            and adapter.capabilities.native_tpsl is CapabilitySupport.SUPPORTED
            else None
        ),
        time_in_force=time_in_force,
        post_only=post_only,
        leverage=Decimal(leverage) if leverage is not None else None,
    )
    try:
        report = _run(_call_and_close(adapter, "submit_order", command))
    except Exception:
        report = _fallback_report(adapter, command, CommandEvidence.AMBIGUOUS)
    acknowledgement = _apply_report(order, report)
    return entry_acknowledgement_result(acknowledgement)


async def _call_and_close(adapter, method: str, command):
    try:
        return await getattr(adapter, method)(command)
    finally:
        try:
            await adapter.close()
        except Exception:
            pass


def _fallback_report(adapter, command, evidence):
    from aletheia.uta.errors import UTAErrorCode
    from aletheia.uta.models import SubmissionReport

    ambiguous = evidence is CommandEvidence.AMBIGUOUS
    return SubmissionReport(
        identity=adapter.identity,
        command_id=command.command_id,
        evidence=evidence,
        received_at=datetime.now(timezone.utc),
        client_order_id=command.client_order_id,
        error_code=(UTAErrorCode.AMBIGUOUS_COMMAND.value if ambiguous else UTAErrorCode.NETWORK.value),
        reason_code=("command_acknowledgement_unknown" if ambiguous else "command_not_sent"),
        profile_version="1",
    )


def _claim_close(position_id: str, reason: str, close_reasoning: str | None):
    from aletheia.storage import repositories as repo

    with transaction(immediate=True):
        row = repo._select_one("positions", position_id)
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        if row.get("status") != "open":
            raise ValueError(f"position {position_id} is not open")
        if has_unresolved_close_claim(row):
            return row, None
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
        claim_position_close(
            position_id,
            order_id=close_order.id,
            status="pending",
            requested_at=datetime.now(timezone.utc).isoformat(),
        )
        return row, close_order


def close_position(
    position_id: str,
    reason: str = "exit_rule",
    *,
    close_reasoning: str | None = None,
) -> dict:
    from aletheia.storage import repositories as repo

    current = repo.get_position(position_id)
    if current is None:
        raise ValueError(f"position not found: {position_id}")
    if current.get("status") != "open":
        raise ValueError(f"position {position_id} is not open")
    row, order = _claim_close(position_id, reason, close_reasoning)
    if order is None:
        existing = repo.get_order(row["pending_close_order_id"])
        return {
            "order_id": row["pending_close_order_id"],
            "exchange_order_id": (existing or {}).get("exchange_order_id"),
            "status": row.get("pending_close_status"),
            "closed": False,
            "fill_price": None,
            "fill_quantity": 0,
            "already_pending": True,
        }
    try:
        adapter = _create_qualified_adapter(row["execution_mode"])
    except Exception:
        finalize_exchange_acknowledgement(
            order.id,
            exchange_order_id=None,
            status="error",
            error_reason="uta_adapter_unavailable_before_submission",
            close_position_id=position_id,
            pending_close_status="error",
        )
        raise
    command = SubmitOrderCommand(
        command_id=str(uuid4()),
        expected_account=adapter.identity,
        instrument=instrument_from_local(row["symbol"], row["market_type"]),
        purpose="exit",
        position_id=position_id,
        client_order_id=order.client_order_id,
        side="sell" if row["direction"] in {"long", "buy"} else "buy",
        quantity=Decimal(str(row["quantity"])),
        order_type="market",
        reduce_only=True,
    )
    try:
        report = _run(_call_and_close(adapter, "submit_order", command))
    except Exception:
        report = _fallback_report(adapter, command, CommandEvidence.AMBIGUOUS)
    acknowledgement = _apply_report(order, report, close_position_id=position_id)
    return close_acknowledgement_result(acknowledgement)


def modify_order(command: ModifyOrderCommand):
    return _run(_command(command.expected_account.execution_mode, "modify_order", command))


def cancel_order(command: CancelOrderCommand):
    return _run(_command(command.expected_account.execution_mode, "cancel_order", command))


def _submit_protection_leg(position: dict, purpose: str, trigger_price: float) -> str:
    """Persist one protection child before submitting it through the selected UTA."""
    suffix = "sl" if purpose == "protective_stop" else "tp"
    order = Order(
        proposal_id=position.get("proposal_id"),
        client_order_id=f"protect-{position['id']}-{suffix}",
        position_id=position["id"],
        close_reason=purpose,
        symbol=position["symbol"],
        market_type=position["market_type"],
        direction=position["direction"],
        order_type="market",
        quantity=float(position["quantity"]),
        price=trigger_price,
        execution_mode=position["execution_mode"],
        status="pending",
    )
    insert_order(order)
    adapter = None
    command = None
    try:
        adapter = _create_qualified_adapter(position["execution_mode"])
        command = SubmitOrderCommand(
            command_id=str(uuid4()),
            expected_account=adapter.identity,
            instrument=instrument_from_local(position["symbol"], position["market_type"]),
            purpose=purpose,
            position_id=position["id"],
            parent_order_id=None,
            client_order_id=order.client_order_id,
            side="sell" if position["direction"] in {"long", "buy"} else "buy",
            quantity=Decimal(str(position["quantity"])),
            order_type="market",
            trigger_price=Decimal(str(trigger_price)),
            reduce_only=True,
        )
        report = _run(_call_and_close(adapter, "submit_order", command))
    except Exception:
        if adapter is None or command is None:
            transition_order(order.id, status="error", error_reason="protection_not_sent")
            raise
        report = _fallback_report(adapter, command, CommandEvidence.AMBIGUOUS)
    acknowledgement = _apply_report(order, report)
    venue_order_id = acknowledgement.get("exchange_order_id")
    if not venue_order_id:
        raise SubmissionUnknownError(client_oid=order.client_order_id)
    return str(venue_order_id)


def place_protection(position_id: str) -> dict[str, str]:
    """Place configured protection children through the selected UTA."""
    from aletheia.storage import repositories as repo

    position = repo.get_position(position_id)
    if position is None or position.get("status") != "open":
        raise ValueError(f"open position not found: {position_id}")
    requested = (
        ("sl", "protective_stop", position.get("stop_price")),
        ("tp", "take_profit", position.get("take_profit_price")),
    )
    placed: dict[str, str] = {}
    for key, purpose, price in requested:
        if price is None:
            continue
        try:
            placed[key] = _submit_protection_leg(position, purpose, float(price))
        except Exception:
            continue
    if placed:
        repo.update_open_position_exit_plan(
            position_id,
            exchange_tpsl_order_id=json.dumps(placed, sort_keys=True),
        )
    return placed


def replace_protective_stop(position_id: str, new_stop_price: float) -> bool:
    """Cancel the proven old stop before creating its tighter replacement."""
    from aletheia.storage import repositories as repo

    position = repo.get_position(position_id)
    if position is None or position.get("status") != "open":
        return False
    try:
        identifiers = json.loads(position.get("exchange_tpsl_order_id") or "{}")
    except (TypeError, ValueError):
        return False
    old_stop = identifiers.get("sl") if isinstance(identifiers, dict) else None
    if not old_stop:
        return False
    adapter = _create_qualified_adapter(position["execution_mode"])
    command = CancelOrderCommand(
        command_id=str(uuid4()),
        expected_account=adapter.identity,
        instrument=instrument_from_local(position["symbol"], position["market_type"]),
        purpose="protective_stop",
        position_id=position_id,
        target_venue_order_id=str(old_stop),
        reason="replace_protective_stop",
    )
    try:
        cancelled = _run(_call_and_close(adapter, "cancel_order", command))
    except Exception:
        return False
    if cancelled.evidence is not CommandEvidence.ACKNOWLEDGED:
        return False
    try:
        new_stop = _submit_protection_leg(position, "protective_stop", float(new_stop_price))
    except Exception:
        return False
    identifiers["sl"] = new_stop
    repo.update_open_position_exit_plan(
        position_id,
        exchange_tpsl_order_id=json.dumps(identifiers, sort_keys=True),
    )
    return True
