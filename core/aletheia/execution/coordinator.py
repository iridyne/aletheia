"""Durable local mechanics between the Gateway and one selected UTA account."""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

logger = logging.getLogger("aletheia.execution.coordinator")

from aletheia.async_utils import run_coro_from_sync
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
    transition_order,
    update_order_metadata,
)
from aletheia.storage.sqlite import get_db
from aletheia.uta.compat import instrument_from_local
from aletheia.uta.manager import create_uta_adapter
from aletheia.uta.models import (
    CancelOrderCommand,
    CommandEvidence,
    CapabilitySupport,
    ModifyOrderCommand,
    OrderLifecycle,
    PositionQuery,
    QueryScope,
    ReadCompleteness,
    SubmitOrderCommand,
    UTAAccountIdentity,
)


_RETRY_SUPPRESSING_ORDER_STATES = (
    "pending",
    "submitted",
    "partial",
    "partially_filled",
    "submission_unknown",
    "materialization_missing",
)


def _close_adapter(adapter) -> None:
    try:
        run_coro_from_sync(adapter.close())
    except Exception:
        # Adapter close is best-effort — the caller is often already in an
        # error path and the exchange session will eventually time out. But
        # a persistent close failure (network stall, adapter bug, credential
        # revoked mid-close) is worth surfacing so it does not hide as a
        # slow connection leak. Log at warning with a stack; do not re-raise.
        logger.warning("Adapter close failed", exc_info=True)


def _create_bound_adapter(execution_mode: str, expected_account: UTAAccountIdentity | None = None):
    adapter = create_uta_adapter(execution_mode)
    try:
        if expected_account is not None and adapter.identity != expected_account:
            raise ValueError("uta_account_identity_mismatch")
    except Exception:
        _close_adapter(adapter)
        raise
    return adapter


def _identity_generation_drift(persisted: UTAAccountIdentity, current: UTAAccountIdentity) -> bool:
    """Same configured account; only the durable credential generation differs."""
    return (
        persisted.credential_generation != current.credential_generation
        and persisted.account_id == current.account_id
        and persisted.execution_mode == current.execution_mode
        and persisted.venue == current.venue
        and persisted.environment == current.environment
        and persisted.profile == current.profile
    )


async def _venue_positions(adapter) -> list | None:
    """Read venue positions under current credentials; ``None`` when not complete.

    Uses ``_call_and_close`` so the adapter is torn down inside its owning loop —
    otherwise a subsequent ``_run(_reanchor_...)`` reuses the same adapter on a
    fresh loop and CCXT rejects it with ``cross_loop_adapter_reuse``.
    """
    report = await _call_and_close(adapter, "get_positions", PositionQuery(scope=QueryScope()))
    if report.completeness is not ReadCompleteness.COMPLETE:
        return None
    return list(report.positions)


def _venue_confirms_position(row: dict, venue_positions: list) -> str | None:
    """Return re-anchor evidence when the venue holds the exact local Position.

    The match is (canonical symbol, market type, side, quantity).  A fresh
    physical account cannot hold the same exact Position set, so this is the
    account-continuity proof required before any generation re-anchor.  The
    returned key is the venue position id when exposed, else the exact matched
    identity (Bitget position lists do not expose a venue position id).
    """
    expected = instrument_from_local(row["symbol"], row["market_type"])
    direction = str(row["direction"] or "").lower()
    sides = {"long", "buy"} if direction == "long" else {"short", "sell"}
    quantity = float(row["quantity"] or 0.0)
    for position in venue_positions:
        instrument = position.instrument
        if instrument.canonical_symbol != expected.canonical_symbol:
            continue
        if instrument.market_type != expected.market_type:
            continue
        if str(position.side or "").lower() not in sides:
            continue
        if abs(float(position.quantity) - quantity) > max(1e-9, quantity * 1e-6):
            continue
        # Bitget position lists do not expose a venue position id; keep the exact
        # matched identity as the re-anchor evidence key when that is the case.
        return position.venue_position_id or (
            f"venue-pos:{instrument.canonical_symbol}:{instrument.market_type}:"
            f"{str(position.side).lower()}:{float(position.quantity):g}"
        )
    return None


def _reanchor_order_identity(
    order_id: str,
    *,
    persisted: UTAAccountIdentity,
    current: UTAAccountIdentity,
    position_id: str | None,
    venue_position_id: str | None,
) -> None:
    """Re-bind one Order's account provenance to the current generation.

    The venue already proved the same physical account holds the exact open
    Position; this updates the local provenance label only, never the venue
    state.  The audit event keeps the prior generation as explicit migration
    evidence, so history is preserved, not silently relabeled.
    """
    update_order_metadata(order_id, uta_account_identity_json=current.model_dump_json())
    write_post_commit_audit(
        {
            "actor": "system",
            "source": "execution_coordinator",
            "event_type": "uta_account_reanchored",
            "entity_type": "order",
            "entity_id": order_id,
            "reason": "credential_generation_drift_venue_proven",
            "position_id": position_id,
            "venue_position_id": venue_position_id,
            "before": {
                "account_id": persisted.account_id,
                "credential_generation": persisted.credential_generation,
            },
            "after": {
                "account_id": current.account_id,
                "credential_generation": current.credential_generation,
            },
        }
    )


def fence_identity_after_order_proof(
    order_row: dict,
    current: UTAAccountIdentity,
) -> UTAAccountIdentity:
    """Return the account identity to fence a command with, after venue proof.

    `current` is the identity of the adapter that just returned this exact Order
    from the venue (same venue order id or client order id, same instrument), so
    the same physical account holds it. A credential-generation-only difference
    is then an audited re-anchor; anything else — an unbound identity, a hard
    mismatch, or a caller that did not read the venue — fails closed with
    ``uta_account_identity_mismatch`` and no relabel.
    """
    raw = order_row.get("uta_account_identity_json") or ""
    if not raw:
        raise ValueError("uta_account_identity_unavailable")
    persisted = UTAAccountIdentity.model_validate_json(raw)
    if persisted == current:
        return current
    if not _identity_generation_drift(persisted, current):
        raise ValueError("uta_account_identity_mismatch")
    _reanchor_order_identity(
        order_id=order_row["id"],
        persisted=persisted,
        current=current,
        position_id=None,
        venue_position_id=None,
    )
    return current


async def _reanchor_generation_drift_rows(execution_mode: str, adapter, rows) -> bool:
    """Re-bind drifted Order/Position identities after exact venue proof.

    Fail-closed when any drifted row is unbound, hard-mismatched, or cannot be
    proven against the venue.  Returns ``True`` only when every drifted open
    Position matched the venue exactly and the whole drifted set was re-anchored.
    """
    drifted = []
    for existing in rows:
        try:
            persisted = UTAAccountIdentity.model_validate_json(existing["uta_account_identity_json"] or "")
        except (ValueError, TypeError):
            # Unbound/malformed identity cannot be proven; fail closed.
            return False
        if persisted == adapter.identity:
            continue
        if not _identity_generation_drift(persisted, adapter.identity):
            return False
        drifted.append((existing, persisted))
    if not drifted:
        return True
    venue_positions = await _venue_positions(adapter)
    if venue_positions is None:
        return False
    for existing, _persisted in drifted:
        if existing["source_kind"] != "position":
            continue
        if _venue_confirms_position(existing, venue_positions) is None:
            return False
    if any(existing["source_kind"] == "order" for existing, _persisted in drifted) and not any(
        existing["source_kind"] == "position" for existing, _persisted in drifted
    ):
        # No open Position to prove account continuity; refuse to relabel in-flight Orders.
        return False
    for existing, persisted in drifted:
        _reanchor_order_identity(
            order_id=existing["order_id"] or existing["id"],
            persisted=persisted,
            current=adapter.identity,
            position_id=existing["id"] if existing["source_kind"] == "position" else None,
            venue_position_id=(
                _venue_confirms_position(existing, venue_positions)
                if existing["source_kind"] == "position"
                else None
            ),
        )
    return True


def _all_rows_match_current_identity(rows, current: UTAAccountIdentity) -> bool:
    for existing in rows:
        try:
            persisted = UTAAccountIdentity.model_validate_json(existing["uta_account_identity_json"] or "")
        except (ValueError, TypeError):
            # Unbound/malformed identity is never trusted as the current account.
            return False
        if persisted != current:
            return False
    return True


async def _command(execution_mode: str, method: str, command):
    adapter = _create_bound_adapter(execution_mode, command.expected_account)
    try:
        return await getattr(adapter, method)(command)
    finally:
        try:
            await adapter.close()
        except Exception:
            # Best-effort close in a finally; see `_close_adapter` for the
            # rationale for logging rather than silently swallowing.
            logger.warning("Adapter close failed after %s", method, exc_info=True)


def record_entry_cancel_state(
    order_id: str,
    *,
    attempts: int | None = None,
    retry_after: str | None = None,
) -> None:
    """Persist Entry Plan cancel convergence bookkeeping on the Order row.

    The Gateway decides the re-attempt schedule; the Order owner writes Order
    metadata (tests/test_order_writer_boundary.py). Nothing here touches the
    lifecycle fields — status/error_reason stay with transition_order().
    """
    if attempts is None and retry_after is None:
        raise ValueError("entry_cancel_state_requires_a_field")
    fields: dict[str, object] = {}
    if attempts is not None:
        fields["entry_cancel_attempts"] = attempts
    if retry_after is not None:
        fields["entry_cancel_retry_after"] = retry_after
    update_order_metadata(order_id, **fields)


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


def _apply_report(
    order: Order,
    report,
    *,
    close_position_id: str | None = None,
    audit_context: dict | None = None,
) -> dict:
    status, reason = _status(report, close_position_id=close_position_id)
    pending_close_status = status
    if close_position_id and report.evidence is CommandEvidence.VENUE_REJECTED:
        pending_close_status = (
            "position_absence_unverified"
            if report.reason_code == "close_position_absence_unverified"
            else "submission_rejected"
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
                "after": {
                    "status": status,
                    "command_evidence": report.evidence.value,
                    **(audit_context or {}),
                },
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
    margin_mode: str | None = None,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    native_tpsl_enabled: bool = False,
    time_in_force: str | None = None,
    post_only: bool = False,
    entry_plan_id: str | None = None,
    order_tactic: str | None = None,
    before_submission=None,
    validate_submission=None,
    submission_started=None,
    **_unused,
) -> dict:
    """Commit pending intent, send one UTA command, then CAS the report."""
    _guard_unresolved_submission(proposal_id, execution_mode)
    if market_type != "spot" and leverage is None:
        raise ValueError("futures UTA order requires leverage")
    from aletheia.storage.repositories import get_trade_proposal
    proposal = get_trade_proposal(proposal_id)
    if proposal is None:
        raise ValueError("UTA order requires an approved Proposal")
    if stop_price != proposal["stop_price"] or take_profit_price != proposal["take_profit"]:
        raise ValueError("UTA protection must exactly match the approved Proposal")
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
    if before_submission is None:
        with transaction(immediate=True):
            insert_order(order)
    instrument = instrument_from_local(symbol, market_type)
    try:
        adapter = _create_bound_adapter(execution_mode)
    except Exception:
        if before_submission is None:
            finalize_exchange_acknowledgement(order.id, exchange_order_id=None, status="error",
                error_reason="uta_adapter_unavailable_before_submission")
        raise
    try:
        placeholders = ",".join("?" for _ in _RETRY_SUPPRESSING_ORDER_STATES)
        rows = get_db().execute(
            f"SELECT id, NULL AS order_id, uta_account_identity_json, 'order' AS source_kind, NULL AS symbol, NULL AS market_type, NULL AS direction, NULL AS quantity "
            f"FROM orders WHERE execution_mode=? AND id<>? AND status IN ({placeholders}) "
            "UNION ALL SELECT p.id, p.order_id, o.uta_account_identity_json, 'position' AS source_kind, p.symbol, p.market_type, p.direction, p.quantity "
            "FROM positions p LEFT JOIN orders o ON o.id=p.order_id "
            "WHERE p.execution_mode=? AND p.status='open'",
            (execution_mode, order.id, *_RETRY_SUPPRESSING_ORDER_STATES, execution_mode),
        ).fetchall()
        if not _all_rows_match_current_identity(rows, adapter.identity):
            if not run_coro_from_sync(_reanchor_generation_drift_rows(execution_mode, adapter, rows)):
                raise ValueError("uta_account_identity_mismatch")
            # _reanchor_...'s venue read closes the adapter inside its own loop;
            # rebind for the subsequent submit_order call so we do not reuse a
            # closed adapter across loops.
            adapter = _create_bound_adapter(execution_mode, adapter.identity)
        order.uta_account_identity_json = adapter.identity.model_dump_json()
        if before_submission is None:
            update_order_metadata(order.id, uta_account_identity_json=order.uta_account_identity_json)
    except Exception:
        _close_adapter(adapter)
        if before_submission is None:
            finalize_exchange_acknowledgement(order.id, exchange_order_id=None, status="error", error_reason="uta_account_identity_unavailable")
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
        reference_price=Decimal(str(price)) if price is not None else None,
        stop_loss_price=(
            Decimal(str(stop_price))
            if stop_price is not None
            and native_tpsl_enabled
            and adapter.capabilities.native_tpsl is CapabilitySupport.SUPPORTED
            else None
        ),
        take_profit_price=(
            Decimal(str(take_profit_price))
            if take_profit_price is not None
            and native_tpsl_enabled
            and adapter.capabilities.native_tpsl is CapabilitySupport.SUPPORTED
            else None
        ),
        time_in_force=time_in_force,
        post_only=post_only,
        leverage=Decimal(leverage) if leverage is not None else None,
        # The mode the financing was resolved for; the profile submits exactly
        # this (#3729 §8). None keeps legacy rows on the venue default.
        margin_mode=margin_mode,
    )
    if before_submission is not None:
        try:
            validate_submission()
            with transaction(immediate=True):
                before_submission()
                insert_order(order)
        except BaseException:
            _close_adapter(adapter)
            raise
    try:
        if submission_started is not None:
            submission_started()
        report = run_coro_from_sync(_call_and_close(adapter, "submit_order", command))
    except Exception:
        report = _fallback_report(adapter, command, CommandEvidence.AMBIGUOUS)
    acknowledgement = _apply_report(
        order,
        report,
        audit_context={
            "native_tpsl_enabled": native_tpsl_enabled,
        },
    )
    return entry_acknowledgement_result(acknowledgement)


async def _call_and_close(adapter, method: str, command):
    try:
        return await getattr(adapter, method)(command)
    finally:
        try:
            await adapter.close()
        except Exception:
            # Best-effort close in a finally; see `_close_adapter` for the
            # rationale for logging rather than silently swallowing.
            logger.warning("Adapter close failed after %s", method, exc_info=True)


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

_CLOSE_CLIP_MIN_FRACTION = 1.0 / 64.0
_VENUE_MATCH_CAP_CANCEL_REASONS = frozenset(
    {
        "trade_too_many_times_cancel",
        "operator_authorized_cleanup_trade_rate_limited",
    }
)
# Marketable-limit fallback bound through the last price once a symbol's demo
# market path is confirmed broken (XRPUSDT 2026-08-22: every market order at
# any size — including a 5-unit diagnostic entry — was zero-fill cancelled with
# trade_too_many_times_cancel while limit orders filled normally).
_MARKETABLE_LIMIT_TOLERANCE = 0.01


def _venue_match_cap_cancel_count(position_id: str) -> int:
    from aletheia.storage import repositories as repo

    row = (
        repo.get_db()
        .execute(
            "SELECT COUNT(*) AS n FROM orders "
            "WHERE position_id = ? AND close_reason IS NOT NULL "
            "AND status = 'cancelled' AND error_reason IN (?, ?)",
            (position_id, *_VENUE_MATCH_CAP_CANCEL_REASONS),
        )
        .fetchone()
    )
    return int(row["n"] or 0) if row else 0


def _quantize_to_reference_precision(bound: float, reference: float, *, up: bool) -> float:
    """Quantize a price bound onto the reference price's own tick precision."""
    text = f"{reference:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    scale = 10**decimals
    scaled = bound * scale
    scaled = math.ceil(scaled - 1e-9) if up else math.floor(scaled + 1e-9)
    return scaled / scale


def _quantize_quantity_to_precision(clip: float, reference: float) -> float:
    """Floor a clip quantity onto the reference quantity's lot precision."""
    text = f"{reference:.10f}".rstrip("0")
    decimals = len(text.split(".")[1]) if "." in text else 0
    scale = 10**decimals
    return math.floor(clip * scale + 1e-9) / scale


def _clipped_close_quantity(position_id: str, remaining: float) -> float:
    """Clip the next close order below sizes the venue has already refused."""
    clip = float(remaining)
    for _ in range(_venue_match_cap_cancel_count(position_id)):
        clip /= 2.0
    clip = max(clip, remaining * _CLOSE_CLIP_MIN_FRACTION)
    return round(clip, 8)


def _position_account(position: dict) -> UTAAccountIdentity:
    from aletheia.storage import repositories as repo

    entry = repo.get_order(position["order_id"]) if position.get("order_id") else None
    if entry is None or any(entry[key] != position[key] for key in ("execution_mode", "symbol", "market_type", "proposal_id")):
        raise ValueError("uta_position_account_unavailable")
    identity = UTAAccountIdentity.model_validate_json(entry.get("uta_account_identity_json") or "")
    if identity.execution_mode != position["execution_mode"]:
        raise ValueError("uta_account_identity_mismatch")
    return identity

def _claim_close(position_id: str, reason: str, close_reasoning: str | None):
    from aletheia.execution.pending_close_recovery import next_close_client_oid
    from aletheia.storage import repositories as repo

    with transaction(immediate=True):
        row = repo._select_one("positions", position_id)
        if row is None:
            raise ValueError(f"position not found: {position_id}")
        if row.get("status") != "open":
            raise ValueError(f"position {position_id} is not open")
        if has_unresolved_close_claim(row):
            return row, None
        client_order_id = next_close_client_oid(position_id)
        close_order = Order(
            uta_account_identity_json=_position_account(row).model_dump_json(),
            proposal_id=row.get("proposal_id"),
            client_order_id=client_order_id,
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


async def _close_reference_price(row, venue: str):
    from aletheia.data.providers import get_market_data_provider
    from aletheia.data.symbols import InstrumentId

    instrument = InstrumentId.from_parts(
        venue, row["symbol"], "spot" if row["market_type"] == "spot" else "perp",
    )
    provider = get_market_data_provider(exchange=venue, exact_venue=True)
    ticker = await _call_and_close(provider, "get_ticker", instrument)
    return ticker.last_price


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
    persisted = UTAAccountIdentity.model_validate_json(order.uta_account_identity_json or "")
    try:
        adapter = _create_bound_adapter(row["execution_mode"])
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
    if adapter.identity != persisted:
        # Credential/config generation drift: the persisted identity predates the
        # current generation.  Re-bind only after the venue proves the exact open
        # Position still exists under current credentials; otherwise fail closed.
        try:
            venue_positions = run_coro_from_sync(_venue_positions(adapter))
        except Exception:
            venue_positions = None
        venue_position_id = (
            _venue_confirms_position(row, venue_positions)
            if _identity_generation_drift(persisted, adapter.identity) and venue_positions
            else None
        )
        if venue_position_id is None:
            _close_adapter(adapter)
            finalize_exchange_acknowledgement(
                order.id,
                exchange_order_id=None,
                status="error",
                error_reason="uta_account_identity_mismatch",
                close_position_id=position_id,
                pending_close_status="error",
            )
            raise ValueError("uta_account_identity_mismatch")
        _reanchor_order_identity(
            order_id=row["order_id"] or order.id,
            persisted=persisted,
            current=adapter.identity,
            position_id=position_id,
            venue_position_id=venue_position_id,
        )
        _reanchor_order_identity(
            order_id=order.id,
            persisted=persisted,
            current=adapter.identity,
            position_id=position_id,
            venue_position_id=venue_position_id,
        )
        adapter = _create_bound_adapter(row["execution_mode"], adapter.identity)
    quantity = float(row["quantity"])
    order_type = "market"
    limit_price = None
    if (
        _venue_match_cap_cancel_count(position_id)
        and adapter.capabilities.match_cap_close_fallback is CapabilitySupport.SUPPORTED
    ):
        try:
            reference = run_coro_from_sync(_close_reference_price(row, adapter.identity.venue))
            venue_buy = row["direction"] not in {"long", "buy"}
            factor = 1.0 + _MARKETABLE_LIMIT_TOLERANCE if venue_buy else 1.0 - _MARKETABLE_LIMIT_TOLERANCE
            limit_price = _quantize_to_reference_precision(reference * factor, reference, up=not venue_buy)
            order_type = "limit"
        except Exception:
            quantity = _quantize_quantity_to_precision(_clipped_close_quantity(position_id, quantity), quantity)
        update_order_metadata(
            order.id, quantity=quantity, order_type=order_type, price=limit_price or order.price
        )
    command = SubmitOrderCommand(
        command_id=str(uuid4()),
        expected_account=adapter.identity,
        instrument=instrument_from_local(row["symbol"], row["market_type"]),
        purpose="exit",
        position_id=position_id,
        client_order_id=order.client_order_id,
        side="sell" if row["direction"] in {"long", "buy"} else "buy",
        quantity=Decimal(str(quantity)),
        order_type=order_type,
        limit_price=Decimal(str(limit_price)) if limit_price is not None else None,
        reduce_only=True,
    )
    try:
        report = run_coro_from_sync(_call_and_close(adapter, "submit_order", command))
    except Exception:
        report = _fallback_report(adapter, command, CommandEvidence.AMBIGUOUS)
    acknowledgement = _apply_report(order, report, close_position_id=position_id)
    return close_acknowledgement_result(acknowledgement)


def modify_order(command: ModifyOrderCommand):
    return run_coro_from_sync(_command(command.expected_account.execution_mode, "modify_order", command))


def cancel_order(command: CancelOrderCommand):
    return run_coro_from_sync(_command(command.expected_account.execution_mode, "cancel_order", command))


def _submit_protection_leg(position: dict, purpose: str, trigger_price: float) -> str:
    """Persist one protection child before submitting it through the selected UTA."""
    identity = _position_account(position)
    suffix = "sl" if purpose == "protective_stop" else "tp"
    order = Order(
        uta_account_identity_json=identity.model_dump_json(),
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
        adapter = _create_bound_adapter(position["execution_mode"], identity)
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
        report = run_coro_from_sync(_call_and_close(adapter, "submit_order", command))
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
            # A failing leg used to be silently skipped, which left the
            # caller with an incomplete `placed` dict and no way to know
            # whether the required stop-loss was actually sent to the
            # venue. execution_contract.md §4 requires "未决 child 保留"
            # (pending children must be retained) and "失败不报告替换成功"
            # (do not report a replacement as successful on failure). A
            # silent `continue` here failed both: it did not retain any
            # trace of the failed leg, and the caller received a partial
            # `placed` map as if it were complete. Keeping the fail-open
            # semantics for now (do not close the main position on a
            # protection failure — that is a separate D1 decision — see
            # followup in `project_shit_cleanup_batch1_2026-09-26.md`
            # segment 3 #5), but a real defect (wrong price, adapter
            # bug, credentials revoked) is now visible.
            logger.error(
                "Protection leg submission failed: position=%s purpose=%s (main position remains open without this leg)",
                position.get("id"),
                purpose,
                exc_info=True,
            )
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
    adapter = _create_bound_adapter(position["execution_mode"], _position_account(position))
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
        cancelled = run_coro_from_sync(_call_and_close(adapter, "cancel_order", command))
    except Exception as exc:
        logger.warning(
            "replace_protective_stop: cancel of old stop %s failed for %s (%s)",
            old_stop, position_id, type(exc).__name__,
        )
        return False
    if cancelled.evidence is not CommandEvidence.ACKNOWLEDGED:
        return False
    try:
        new_stop = _submit_protection_leg(position, "protective_stop", float(new_stop_price))
    except Exception as exc:
        logger.warning(
            "replace_protective_stop: submit of new stop for %s failed after cancel (%s)",
            position_id, type(exc).__name__,
        )
        return False
    identifiers["sl"] = new_stop
    repo.update_open_position_exit_plan(
        position_id,
        exchange_tpsl_order_id=json.dumps(identifiers, sort_keys=True),
    )
    return True
