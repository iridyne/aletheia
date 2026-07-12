"""Honest split between verified economic and administrative position closure."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from typing import Any

from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
from aletheia.data.symbols import to_canonical
from aletheia.execution.verified_exit import VerifiedExitResult
from aletheia.portfolio.pnl import build_exit_reasoning
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _load_open(position_id: str) -> tuple[dict, dict | None]:
    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("status") == "open":
        return row, None
    return row, {
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

    closed_at = datetime.now(timezone.utc).isoformat()
    updates = {
        "status": "closed",
        "unrealized_pnl": 0.0,
        "realized_pnl": None,
        "close_reason": reason,
        "closed_at": closed_at,
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
            row,
            exit_price=None,
            reason=reason,
            actor=actor,
            source=source,
            is_ai_judgment=False,
            trigger={"closure_type": "administrative", "economic_outcome": "unavailable"},
        )),
    }
    with repo.transaction():
        repo.update_position(position_id, **updates)
        repo.insert_audit_event(AuditEvent(
            actor=actor,
            source=source,
            event_type="position_administratively_closed",
            entity_type="position",
            entity_id=position_id,
            before_state=_json({
                "status": row.get("status"),
                "unrealized_pnl": row.get("unrealized_pnl"),
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
) -> dict:
    """Settle exact fill/fee economics once after broker evidence is verified."""

    row, already = _load_open(position_id)
    _validate_verified_evidence(row, evidence)
    if already is not None:
        return already

    fill_quantity = sum(fill.quantity for fill in evidence.fills)
    exit_price = sum(
        fill.price * fill.quantity for fill in evidence.fills
    ) / fill_quantity
    exit_fee = sum(float(fill.fee) for fill in evidence.fills if fill.fee is not None)
    entry_fee = row.get("entry_fee")
    if entry_fee is None:
        raise ValueError("verified closure requires known entry fee")
    gross = _realized_pnl(
        str(row["direction"]), float(row["entry_price"]), exit_price,
        float(row["quantity"]),
    )
    realized = gross - float(entry_fee) - exit_fee
    closed_at = datetime.now(timezone.utc).isoformat()
    price_source = "local_fill" if row.get("execution_mode") == "paper_internal" else "broker_fill"
    identity = {
        "broker_id": evidence.broker_id,
        "account_environment": evidence.account_environment,
        "account_scope": evidence.account_scope,
        "execution_mode": evidence.execution_mode,
        "position_id": evidence.position_id,
        "symbol": evidence.symbol,
        "market_type": evidence.market_type,
        "direction": evidence.direction,
    }
    updates = {
        "status": "closed",
        "current_price": exit_price,
        "unrealized_pnl": 0.0,
        "realized_pnl": realized,
        "close_reason": reason,
        "closed_at": closed_at,
        "entry_fee": float(entry_fee),
        "exit_fee": exit_fee,
        # No reliable decision/arrival reference exists in VerifiedExitResult.
        # Unknown is honest; config-derived slippage would be fabricated here.
        "slippage_cost": None,
        "close_category": "strategy",
        "exit_price_source": price_source,
        "exit_fill_verified": True,
        "outcome_eligible": True,
        "outcome_exclusion_reason": "eligible",
        "reconciliation_identity": _json(identity),
        "reconciliation_evidence": _json({
            "verified_exit": asdict(evidence),
            **(reconciliation_context or {}),
        }),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        "exit_reasoning": _json(build_exit_reasoning(
            row,
            exit_price=exit_price,
            reason=reason,
            actor=actor,
            source=source,
            is_ai_judgment=False,
            trigger={"closure_type": "verified_strategy", "fill_count": len(evidence.fills)},
        )),
    }
    closed_row = {**row, **updates}
    with repo.transaction():
        repo.update_position(position_id, **updates)
        if row.get("execution_mode") == "paper_internal":
            from aletheia.portfolio.paper_account import settle_position_closed
            settle_position_closed(position_id, realized)
        repo.insert_audit_event(AuditEvent(
            actor=actor,
            source=source,
            event_type="position_verified_closed",
            entity_type="position",
            entity_id=position_id,
            before_state=_json({"status": row.get("status")}),
            after_state=_json({
                "status": "closed", "close_category": "strategy",
                "exit_fill_verified": True, "fill_ids": [
                    fill.fill_id for fill in evidence.fills
                ],
                "candidate_id": (reconciliation_context or {}).get("candidate_id"),
                "realized_pnl": realized,
            }),
            message="Position settled from verified private fill evidence",
        ))
    _run_outcome_hooks(closed_row, realized, reason)
    return {
        "position_id": position_id,
        "closed": True,
        "already_closed": False,
        "closure_type": "verified_strategy",
        "reason": reason,
        "realized_pnl": realized,
        "exit_price": exit_price,
        "exit_fee": exit_fee,
    }


def close_position_from_verified_exit(
    position_id: str,
    evidence: VerifiedExitResult,
    *,
    reason: str = "reconciled_gone",
    actor: str = "position_reconciler",
    source: str = "verified_exit_protocol",
    candidate_id: str | None = None,
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
        position_id, evidence, reason=reason, actor=actor, source=source,
        reconciliation_context={"candidate_id": candidate_id},
    )


def _validate_verified_evidence(row: dict, evidence: VerifiedExitResult) -> None:
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
    fill_quantity = sum(fill.quantity for fill in evidence.fills)
    quantity = float(row["quantity"])
    if abs(fill_quantity - quantity) > max(1e-10, quantity * 1e-8):
        raise ValueError("verified closure fill quantity mismatch")
    _, _, quote = evidence.symbol.partition("/")
    if any(fill.fee is None for fill in evidence.fills):
        raise ValueError("verified closure requires exact exit fee")
    if any((fill.fee_currency or "").upper() != quote.upper() for fill in evidence.fills):
        raise ValueError("verified closure fee currency is not economically usable")


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


def _realized_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    return (
        (exit_price - entry) * qty
        if direction in {"long", "buy"}
        else (entry - exit_price) * qty
    )


def _run_outcome_hooks(row: dict, realized: float, reason: str) -> None:
    """Run only hooks whose inputs are a verified strategy outcome."""

    try:
        from aletheia.analytics.shadow_trades import resolve_shadow_trades_for_position
        resolve_shadow_trades_for_position(row)
    except Exception:
        pass
    try:
        if row.get("trade_school") == "serenity" and row.get("opportunity_id"):
            from aletheia.portfolio.pnl import _close_serenity_feedback
            _close_serenity_feedback(row, realized, reason)
    except Exception:
        pass
