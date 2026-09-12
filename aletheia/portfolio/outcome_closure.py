"""Honest split between verified economic and administrative position closure."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from typing import Any

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
)
from aletheia.data.symbols import to_canonical
from aletheia.execution.verified_exit import VerifiedExitResult
from aletheia.portfolio.pnl import build_exit_reasoning, fee_in_quote
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent, Fill, Order


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _close_provenance() -> dict:
    """Outcome-producer identity to stamp on the position at close.

    Records the session that CLOSED the position (closed_runtime_session_id, …),
    distinct from the session that opened it. Returns {} when no session is active
    so we never overwrite with NULLs. Best-effort — never blocks a real close.
    """
    try:
        from aletheia.agent_runtime.runtime_session import current_close_provenance

        return {k: v for k, v in current_close_provenance().items() if v is not None}
    except Exception:
        return {}


def _load_open(position_id: str) -> tuple[dict, dict | None]:
    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("status") == "open":
        return row, None
    return row, _already_closed_result(position_id, row)


def _materialize_observed_close_chain(
    position: dict,
    evidence: VerifiedExitResult,
    *,
    reason: str,
) -> list[str]:
    """Persist exact read-only exchange close facts inside the close transaction.

    A disappearance query observes an already-completed exchange action; it does
    not authorize or submit a new one.  Therefore an unknown remote Order is
    stored as ``source='adopted'`` and every Fill keeps its account-scoped venue
    identity.  Any conflicting local row aborts the whole Position close.
    """
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )

    grouped: dict[str, list] = {}
    for item in evidence.fills:
        remote_order_id = str(item.order_id or "").strip()
        if not remote_order_id:
            raise ValueError("observed close requires exact remote Order identity")
        grouped.setdefault(remote_order_id, []).append(item)

    db = repo.get_db()
    materialized_order_ids: list[str] = []
    for remote_order_id, items in grouped.items():
        remote_quantity = sum(float(item.quantity) for item in items)
        quantity_tolerance = max(1e-12, remote_quantity * 1e-9)
        matches = db.execute(
            "SELECT * FROM orders WHERE execution_mode=? AND exchange_order_id=? "
            "ORDER BY rowid ASC",
            (evidence.execution_mode, remote_order_id),
        ).fetchall()
        if len(matches) > 1:
            raise ValueError("remote close Order identity is ambiguous locally")

        if matches:
            order = dict(matches[0])
            expected = {
                "symbol": str(position["symbol"]),
                "market_type": str(position["market_type"]),
                "direction": str(position["direction"]),
                "execution_mode": str(position["execution_mode"]),
            }
            if any(str(order.get(key) or "") != value for key, value in expected.items()):
                raise ValueError("observed close Order identity conflicts with Position")
            if order.get("proposal_id") != position.get("proposal_id"):
                raise ValueError("observed close Order Proposal conflicts with Position")
            linked_position_id = str(order.get("position_id") or "")
            pending_owner = position.get("pending_close_order_id") == order["id"]
            if linked_position_id not in {"", str(position["id"])}:
                raise ValueError("observed close Order belongs to another Position")
            if not str(order.get("close_reason") or "").strip() and not pending_owner:
                raise ValueError("remote Order is not a proven local close Order")
            order_quantity = float(order.get("quantity") or 0.0)
            if abs(order_quantity - remote_quantity) > quantity_tolerance:
                raise ValueError("observed close Order quantity conflicts with private fills")
            local_order_id = str(order["id"])
        else:
            adopted = Order(
                proposal_id=position.get("proposal_id"),
                risk_decision_id=None,
                position_id=str(position["id"]),
                close_reason=reason,
                symbol=str(position["symbol"]),
                market_type=str(position["market_type"]),
                direction=str(position["direction"]),
                order_type="observed_close",
                quantity=remote_quantity,
                price=None,
                execution_mode=str(position["execution_mode"]),
                exchange_order_id=remote_order_id,
                status="filled",
                source="adopted",
                filled_at=datetime.fromtimestamp(
                    max(item.filled_at_ms for item in items) / 1000,
                    tz=timezone.utc,
                ).isoformat(),
            )
            repo.insert_order(adopted)
            local_order_id = adopted.id

        for item in items:
            durable_id = durable_private_fill_id(
                str(position["execution_mode"]), str(item.fill_id)
            )
            ids = (durable_id, str(item.fill_id))
            existing_rows = db.execute(
                "SELECT * FROM fills WHERE id IN (?, ?) ORDER BY rowid ASC",
                ids,
            ).fetchall()
            if len(existing_rows) > 1:
                raise ValueError("remote close Fill identity is duplicated locally")
            filled_at = datetime.fromtimestamp(
                item.filled_at_ms / 1000, tz=timezone.utc
            ).isoformat()
            if existing_rows:
                existing = dict(existing_rows[0])
                price_tolerance = max(1e-12, float(item.price) * 1e-9)
                fee_tolerance = max(1e-12, abs(item.fee) * 1e-9) if item.fee is not None else 0.0
                if (
                    str(existing.get("order_id") or "") != local_order_id
                    or abs(float(existing.get("fill_price") or 0.0) - item.price)
                    > price_tolerance
                    or abs(float(existing.get("fill_quantity") or 0.0) - item.quantity)
                    > quantity_tolerance
                    or (existing.get("fee") is None) != (item.fee is None)
                    or (item.fee is not None and abs(float(existing["fee"]) - item.fee) > fee_tolerance)
                    or str(existing.get("fee_currency") or "").upper()
                    != str(item.fee_currency or "").upper()
                    or str(existing.get("filled_at") or "") != filled_at
                ):
                    raise ValueError("remote close Fill conflicts with durable local fact")
                continue
            repo.insert_fill(Fill(
                id=durable_id,
                order_id=local_order_id,
                fill_price=float(item.price),
                fill_quantity=float(item.quantity),
                fee=item.fee,
                fee_currency=str(item.fee_currency or ""),
                fee_source="reconciled" if item.fee is not None else "unavailable",
                filled_at=filled_at,
            ))

        repo.transition_order(
            local_order_id,
            position_id=str(position["id"]),
            status="filled",
            error_reason=None,
            filled_at=datetime.fromtimestamp(
                max(item.filled_at_ms for item in items) / 1000,
                tz=timezone.utc,
            ).isoformat(),
        )
        materialized_order_ids.append(local_order_id)
    return materialized_order_ids


def _already_closed_result(position_id: str, row: dict) -> dict:
    return {
        "position_id": position_id,
        "closed": False,
        "already_closed": True,
        "closure_type": row.get("close_category") or "unknown",
    }


def close_position_administrative(
    position_id: str,
    *,
    reason: str,
    reconciliation_identity: dict[str, Any],
    reconciliation_evidence: dict[str, Any],
    actor: str = "system",
    source: str = "outcome_integrity",
) -> dict:
    """Converge lifecycle state without inventing any economic outcome."""

    row, already = _load_open(position_id)
    if not reason or not reconciliation_identity or not reconciliation_evidence:
        raise ValueError("administrative closure requires reason, identity, and evidence")
    _validate_administrative_identity(row, reconciliation_identity)
    if already is not None:
        return already

    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if current.get("status") != "open":
            return _already_closed_result(position_id, current)
        _validate_administrative_identity(current, reconciliation_identity)

        closed_at = datetime.now(timezone.utc).isoformat()
        updates = {
            "status": "closed",
            "unrealized_pnl": 0.0,
            "realized_pnl": None,
            "close_reason": reason,
            "closed_at": closed_at,
            "pending_close_order_id": None,
            "pending_close_status": None,
            "pending_close_requested_at": None,
            "exit_fee": None,
            "slippage_cost": None,
            "close_category": "administrative",
            "exit_price_source": "unavailable",
            "exit_fill_verified": False,
            "outcome_eligible": False,
            "outcome_exclusion_reason": "administrative_close",
            "reconciliation_identity": _json(reconciliation_identity),
            "reconciliation_evidence": _json(reconciliation_evidence),
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "exit_reasoning": _json(build_exit_reasoning(
                current,
                exit_price=None,
                reason=reason,
                actor=actor,
                source=source,
                is_ai_judgment=False,
                trigger={
                    "closure_type": "administrative",
                    "economic_outcome": "unavailable",
                },
            )),
        }
        updates.update(_close_provenance())
        repo.finalize_position_close(position_id, updates)
        repo.insert_audit_event(AuditEvent(
            actor=actor,
            source=source,
            event_type="position_administratively_closed",
            entity_type="position",
            entity_id=position_id,
            before_state=_json({
                "status": current.get("status"),
                "unrealized_pnl": current.get("unrealized_pnl"),
            }),
            after_state=_json({
                "status": "closed",
                "close_reason": reason,
                "close_category": "administrative",
                "economic_outcome": "unavailable",
            }),
            message="Position lifecycle converged without verified economic exit",
        ))
    return {
        "position_id": position_id,
        "closed": True,
        "already_closed": False,
        "closure_type": "administrative",
        "reason": reason,
        "realized_pnl": None,
        "exit_price": None,
    }


def close_position_verified(
    position_id: str,
    evidence: VerifiedExitResult,
    *,
    reason: str = "verified_exit",
    actor: str = "system",
    source: str = "verified_exit_protocol",
    reconciliation_context: dict[str, Any] | None = None,
    closure_type: str = "verified_strategy",
) -> dict:
    """Normalize disappearance evidence into the single economic-close owner."""
    from aletheia.portfolio.pnl import PositionNotOpenError, close_position

    if closure_type not in {"verified_strategy", "verified_external"}:
        raise ValueError("unsupported verified closure type")
    row, already = _load_open(position_id)
    _validate_verified_evidence(row, evidence)
    if already is not None:
        return already

    fill_quantity = sum(fill.quantity for fill in evidence.fills)
    exit_price = sum(fill.price * fill.quantity for fill in evidence.fills) / fill_quantity
    quote_fees = [
        fee_in_quote(fee=fill.fee, fee_currency=fill.fee_currency, fill_price=fill.price, symbol=evidence.symbol)
        for fill in evidence.fills
    ]
    exit_fee = sum(quote_fees) if all(fee is not None for fee in quote_fees) else None
    closed_at = datetime.fromtimestamp(
        max(fill.filled_at_ms for fill in evidence.fills) / 1000,
        tz=timezone.utc,
    ).isoformat()
    identity = {
        "broker_id": evidence.broker_id,
        "account_environment": evidence.account_environment,
        "account_scope": evidence.account_scope,
        "account_fingerprint": evidence.account_scope,
        "execution_mode": evidence.execution_mode,
        "position_id": evidence.position_id,
        "symbol": evidence.symbol,
        "market_type": evidence.market_type,
        "direction": evidence.direction,
    }
    materialized_order_ids: list[str] = []
    private_evidence = {
        **(reconciliation_context or {}),
        "source": source,
        "verified_exit": asdict(evidence),
        "fill_ids": [fill.fill_id for fill in evidence.fills],
        "fill_quantity": fill_quantity,
        "fee_in_quote": exit_fee,
        "fee_status": "complete" if exit_fee is not None else "unavailable",
        "materialized_close_order_ids": materialized_order_ids,
    }

    def materialize() -> None:
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        _validate_verified_evidence(current, evidence)
        if closure_type == "verified_external":
            materialized_order_ids.extend(
                _materialize_observed_close_chain(current, evidence, reason=reason)
            )

    try:
        result = close_position(
            position_id,
            exit_price,
            reason,
            actual_exit_fee=exit_fee,
            closed_at=closed_at,
            verified_exit_evidence=private_evidence,
            materialize_close=materialize,
            actor=actor,
            source=source,
            close_category=("strategy" if closure_type == "verified_strategy" else "administrative"),
            reconciliation_identity=identity,
            close_reasoning={
                "actor": actor,
                "source": source,
                "reason": reason,
                "is_ai_judgment": False,
                "trigger": {"closure_type": closure_type, "fill_count": len(evidence.fills)},
            },
        )
    except PositionNotOpenError:
        current = repo._select_one("positions", position_id)
        if current is None or current.get("status") != "closed":
            raise
        return _already_closed_result(position_id, current)
    return {**result, "closed": True, "already_closed": False, "closure_type": closure_type}


def close_position_from_verified_exit(
    position_id: str,
    evidence: VerifiedExitResult,
    *,
    reason: str = "reconciled_gone",
    actor: str = "position_reconciler",
    source: str = "verified_exit_protocol",
    candidate_id: str | None = None,
    verified_strategy_reason: str | None = None,
) -> dict:
    """Route the protocol's three states without weakening inconclusive safety."""

    if evidence.state == "inconclusive":
        return {
            "position_id": position_id,
            "closed": False,
            "already_closed": False,
            "closure_type": "inconclusive",
            "reason": evidence.reason,
        }
    if evidence.state == "confirmed_absent_without_fill":
        return close_position_administrative(
            position_id,
            reason=reason,
            reconciliation_identity={
                "broker_id": evidence.broker_id,
                "account_environment": evidence.account_environment,
                "account_scope": evidence.account_scope,
                "execution_mode": evidence.execution_mode,
                "position_id": evidence.position_id,
                "symbol": evidence.symbol,
                "market_type": evidence.market_type,
                "direction": evidence.direction,
            },
            reconciliation_evidence={
                "verified_exit": asdict(evidence),
                "candidate_id": candidate_id,
            },
            actor=actor,
            source=source,
        )
    return close_position_verified(
        position_id,
        evidence,
        reason=(verified_strategy_reason or "verified_external"),
        actor=actor,
        source=source,
        reconciliation_context={"candidate_id": candidate_id},
        closure_type=(
            "verified_strategy"
            if verified_strategy_reason
            else "verified_external"
        ),
    )


def _validate_verified_evidence(row: dict, evidence: VerifiedExitResult) -> None:
    from aletheia.uta.manager import UTA_EXECUTION_MODES

    if row.get("execution_mode") not in UTA_EXECUTION_MODES:
        raise ValueError("unsupported position execution mode")
    if evidence.state != "verified_fill" or not evidence.position_absent:
        raise ValueError("verified closure requires verified_fill absent evidence")
    expected = {
        "position_id": str(row["id"]),
        "execution_mode": str(row["execution_mode"]),
        "symbol": to_canonical(str(row["symbol"])),
        "market_type": str(row["market_type"]),
        "direction": str(row["direction"]).lower(),
    }
    actual = {
        "position_id": evidence.position_id,
        "execution_mode": evidence.execution_mode,
        "symbol": evidence.symbol,
        "market_type": evidence.market_type,
        "direction": evidence.direction,
    }
    if actual != expected:
        raise ValueError("verified closure identity mismatch")
    fill_ids = [fill.fill_id for fill in evidence.fills]
    if not fill_ids or len(fill_ids) != len(set(fill_ids)) or any(
        not fill.fill_id or not math.isfinite(fill.price) or fill.price <= 0
        or not math.isfinite(fill.quantity) or fill.quantity <= 0
        for fill in evidence.fills
    ):
        raise ValueError("verified closure requires exact fill identity, price and quantity")
    fill_quantity = sum(fill.quantity for fill in evidence.fills)
    quantity = float(row["quantity"])
    if abs(fill_quantity - quantity) > max(1e-10, quantity * 1e-8):
        raise ValueError("verified closure fill quantity mismatch")
    try:
        fees = [float(fill.fee) for fill in evidence.fills if fill.fee is not None]
    except (TypeError, ValueError):
        raise ValueError("verified closure has malformed exit fee") from None
    if any(not math.isfinite(fee) or fee < 0 for fee in fees):
        raise ValueError("verified closure has malformed exit fee")
    if any(not str(fill.order_id or "").strip() for fill in evidence.fills):
        raise ValueError("verified closure requires exact remote Order identity")


def _validate_administrative_identity(
    row: dict, reconciliation_identity: dict[str, Any]
) -> None:
    expected = {
        "execution_mode": str(row["execution_mode"]),
        "position_id": str(row["id"]),
        "symbol": to_canonical(str(row["symbol"])),
        "market_type": str(row["market_type"]),
        "direction": str(row["direction"]).lower(),
    }
    actual = {
        "execution_mode": str(reconciliation_identity.get("execution_mode") or ""),
        "position_id": str(reconciliation_identity.get("position_id") or ""),
        "symbol": to_canonical(str(reconciliation_identity.get("symbol") or "")),
        "market_type": str(reconciliation_identity.get("market_type") or ""),
        "direction": str(reconciliation_identity.get("direction") or "").lower(),
    }
    if actual != expected or any(
        not reconciliation_identity.get(key)
        for key in ("broker_id", "account_environment", "account_scope")
    ):
        raise ValueError("administrative closure identity mismatch")
