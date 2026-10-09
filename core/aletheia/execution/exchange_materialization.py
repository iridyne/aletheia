"""Replay-safe materialization of exchange-confirmed order fills.

The exchange is the source of truth for fill economics.  This module converts
private Bitget fill rows into the same durable Order -> Fill -> Position chain
that the synchronous broker path writes.  It never places an order and never
falls back to requested price/quantity/fees when private evidence is incomplete.
"""

from __future__ import annotations

import json
import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from aletheia.data.freshness import MAX_FUTURE_SKEW
from aletheia.portfolio.pnl import fee_in_quote
from aletheia.execution.position_enrichment import liquidation_price_for
from aletheia.execution.stops import merge_protective_stop
from aletheia.execution.verified_exit import VerifiedExitResult
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent, Fill, Order, Position, new_id
from aletheia.uta.models import FillReport

logger = logging.getLogger("aletheia.execution.exchange_materialization")

def claim_observed_tpsl_close(
    position_id: str,
    *,
    execute_order_id: str,
    reason: str,
    expected_account,
):
    """Atomically register an observed native TPSL execution without placing it."""
    from aletheia.execution.coordinator import _position_account
    from aletheia.uta.identity import IdentityRelation, identity_relation
    from aletheia.uta.models import UTAAccountIdentity
    from aletheia.uta.manager import native_protection_execution_modes

    execute_order_id = str(execute_order_id or "").strip()
    if not execute_order_id:
        raise ValueError("TPSL execute order identity unavailable")
    with repo.transaction(immediate=True):
        position = repo._select_one("positions", position_id)
        if position is None or position.get("status") != "open":
            return None, False
        execution_mode = position.get("execution_mode")
        if execution_mode not in native_protection_execution_modes():
            raise ValueError(
                "automatic TPSL close registration requires a supported native reader"
            )
        # The observing reader is built from current configuration, but the claim
        # carries the entry Order's frozen identity. Only a reader authenticating
        # as that same account (contract §3.0b identity_relation) may produce or
        # advance this claim; reconcile() rejects the same mismatch, and this bridge
        # must not be the softer door.
        entry_account = _position_account(position)
        if expected_account is None or identity_relation(entry_account, expected_account) is not IdentityRelation.SAME:
            raise ValueError("uta_account_identity_mismatch")
        pending_id = position.get("pending_close_order_id")
        if pending_id:
            existing = repo.get_order(pending_id)
            if (
                existing is not None
                and existing.get("exchange_order_id") == execute_order_id
                and existing.get("position_id") == position_id
            ):
                # A claim written before this account proof existed carries a NULL
                # identity, and materialize_bitget_close enforces none of its own.
                # Advancing it would file evidence under absent or foreign
                # provenance, so the carrier proves itself or the claim waits.
                try:
                    claimed = UTAAccountIdentity.model_validate_json(
                        existing.get("uta_account_identity_json") or ""
                    )
                except ValueError as exc:
                    raise ValueError("uta_account_identity_unavailable") from exc
                if identity_relation(claimed, entry_account) is not IdentityRelation.SAME:
                    raise ValueError("uta_account_identity_mismatch")
                return existing, False
            raise RuntimeError("TPSL execute order conflicts with pending close claim")

        close_order = Order(
            uta_account_identity_json=entry_account.model_dump_json(),
            proposal_id=position.get("proposal_id"),
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason=reason,
            symbol=position["symbol"],
            market_type=position["market_type"],
            direction=position["direction"],
            order_type="market",
            quantity=float(position["quantity"]),
            price=None,
            execution_mode=execution_mode,
            exchange_order_id=execute_order_id,
            status="materialization_missing",
            error_reason="private_fill_evidence_pending",
        )
        repo.insert_order(close_order)
        repo.claim_position_close(
            position_id,
            order_id=close_order.id,
            status="materialization_missing",
            requested_at=datetime.now(timezone.utc).isoformat(),
        )
        return repo.get_order(close_order.id), True


def record_operator_confirmed_fill(fill: Fill) -> None:
    """Persist one operator-venue-confirmed Fill.

    Fill writes are owned by this module (``tests/test_architecture_fitness.py::
    test_fill_writes_stay_with_exchange_materialization``). The manual-close
    resolver is an operator surface (``execution_contract`` §10.2.4): it decides
    *whether* to record a venue-confirmed fill, and this module performs the
    write. The caller keeps its own ``transaction(immediate=True)`` and
    compare-and-swap, so the delegation adds no new authority.
    """
    repo.insert_fill(fill)


def materialize_observed_close_chain(
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
                _fill_totals([existing], symbol=str(position["symbol"]))
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


def _ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


class MaterializationIntegrityError(RuntimeError):
    """Private evidence conflicts with the durable local causal chain."""


class PrivateFillEvidenceInvalid(MaterializationIntegrityError):
    """An order-matched private row is unusable before evidence acceptance."""


def _close_reasoning_for_order(order: dict) -> dict | None:
    """Load an optional pending-close rationale without inventing provenance."""

    raw = order.get("close_reasoning")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise MaterializationIntegrityError("close reasoning carrier is malformed")
    try:
        payload = json.loads(raw)
        json.dumps(payload, allow_nan=False)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MaterializationIntegrityError("close reasoning carrier is malformed") from exc
    if not isinstance(payload, dict):
        raise MaterializationIntegrityError("close reasoning carrier is not an object")
    required_text = ("actor", "source", "reason_code", "reason")
    if payload.get("version") != "trade_decision_rationale.v1" or any(
        not isinstance(payload.get(field), str) or not payload[field].strip() for field in required_text
    ):
        raise MaterializationIntegrityError("close reasoning carrier fields are invalid")
    if payload["reason_code"] != order.get("close_reason"):
        raise MaterializationIntegrityError("close reasoning carrier does not match Order reason")
    if len(payload["reason"]) > 1200:
        raise MaterializationIntegrityError("close reasoning carrier exceeds reason limit")
    if type(payload.get("is_ai_judgment")) is not bool:
        raise MaterializationIntegrityError("close reasoning carrier judgment flag is invalid")
    if not isinstance(payload.get("trigger"), dict):
        raise MaterializationIntegrityError("close reasoning carrier trigger is invalid")
    allowed_actors = {"system_operator", "system", "human", "position_reconciler"}
    if payload["actor"] not in allowed_actors:
        raise MaterializationIntegrityError("close reasoning carrier actor is invalid")
    if payload["is_ai_judgment"] != (payload["actor"] == "system_operator"):
        raise MaterializationIntegrityError("close reasoning carrier actor and judgment flag conflict")
    reserved_source_actors = {
        "portfolio_review": {"system_operator"},
        "operator_proactive_exit": {"system_operator"},
        "dashboard_manual_close": {"human"},
        "position_lifecycle": {"system"},
        "verified_exit_protocol": {"system", "position_reconciler"},
    }
    expected_actors = reserved_source_actors.get(payload["source"])
    if expected_actors is not None and payload["actor"] not in expected_actors:
        raise MaterializationIntegrityError("close reasoning carrier actor and source conflict")
    return payload


@dataclass(frozen=True)
class PrivateFill:
    fill_id: str
    exchange_order_id: str
    client_order_id: str | None
    price: float
    quantity: float
    fee: float | None
    fee_currency: str
    filled_at: str
    filled_at_ms: int
    realized_liquidity: str | None = None

    def __post_init__(self) -> None:
        if (
            not math.isfinite(self.price)
            or not math.isfinite(self.quantity)
            or (self.fee is not None and (not math.isfinite(self.fee) or self.fee < 0))
            or self.price <= 0
            or self.quantity <= 0
            or self.filled_at_ms <= 0
        ):
            raise PrivateFillEvidenceInvalid("private fill row is incomplete or non-finite")


@dataclass(frozen=True)
class MaterializationResult:
    state: str
    new_fill_count: int = 0
    position_id: str | None = None
    exchange_order_id: str | None = None


def durable_private_fill_id(execution_mode: str, remote_fill_id: str) -> str:
    """Namespace a venue fill identity by its exact logical exchange account.

    ``fills.id`` is globally unique, while venue trade ids are only guaranteed
    inside one account. Keeping both strings directly in this deterministic id
    avoids a schema migration and leaves the original remote id auditable.
    """
    execution_mode = str(execution_mode or "").strip()
    remote_fill_id = str(remote_fill_id or "").strip()
    if not execution_mode or not remote_fill_id:
        raise MaterializationIntegrityError("private Fill identity requires account scope and remote id")
    return f"private-fill:{execution_mode}:{remote_fill_id}"


def _private_fill_already_local(
    item: PrivateFill,
    local_ids: set[str],
    *,
    execution_mode: str,
) -> bool:
    """Recognize new scoped ids plus exact legacy raw-id rows."""
    return durable_private_fill_id(execution_mode, item.fill_id) in local_ids or item.fill_id in local_ids


@contextmanager
def _atomic_materialization():
    """Own a rollback boundary even when the caller already has a transaction."""
    db = repo.get_db()
    if not db.in_transaction:
        with repo.transaction(immediate=True):
            yield
        return
    # Repository transactions intentionally join an existing transaction.  A
    # reconciler must be stricter: Fill + Position + terminal statuses are one
    # indivisible unit, so use a nested savepoint when a caller already owns the
    # outer transaction.
    savepoint = "exchange_fill_materialization"
    db.execute(f"SAVEPOINT {savepoint}")
    try:
        yield
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        db.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def _positive_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _timestamp_ms(value) -> int | None:
    """Normalize an ISO timestamp for exact exchange-time comparison."""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(round(parsed.timestamp() * 1000))


def _private_fill_from_report(report: FillReport) -> PrivateFill:
    """Convert one validated UTA FillReport into storage-safe materialization data."""
    if not isinstance(report, FillReport):
        raise PrivateFillEvidenceInvalid("private fill evidence must be a UTA FillReport")
    try:
        if report.executed_at.tzinfo is None or report.executed_at.utcoffset() is None:
            raise ValueError("fill timestamp is naive")
        timestamp_ms = int(round(report.executed_at.timestamp() * 1000))
        item = PrivateFill(
            fill_id=str(report.venue_fill_id).strip(),
            exchange_order_id=str(report.venue_order_id or "").strip(),
            client_order_id=(str(report.client_order_id).strip() if report.client_order_id else None),
            price=float(report.price),
            quantity=float(report.quantity),
            fee=(float(report.fee) if report.fee is not None else None),
            fee_currency=str(report.fee_currency or ""),
            realized_liquidity=report.liquidity_side,
            filled_at=report.executed_at.astimezone(timezone.utc).isoformat(),
            filled_at_ms=timestamp_ms,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise PrivateFillEvidenceInvalid("private fill report has incomplete or non-finite economics") from exc
    if not item.fill_id or not item.exchange_order_id or item.filled_at_ms <= 0:
        raise PrivateFillEvidenceInvalid("private fill report identity or timestamp is incomplete")
    if report.executed_at > datetime.now(timezone.utc) + MAX_FUTURE_SKEW:
        raise PrivateFillEvidenceInvalid("private fill report timestamp is in the future")
    return item


def _fill_reports(rows) -> list[FillReport]:
    if isinstance(rows, FillReport):
        return [rows]
    if not isinstance(rows, (list, tuple)):
        return []
    if any(not isinstance(row, FillReport) for row in rows):
        raise PrivateFillEvidenceInvalid("private fill evidence must use normalized UTA reports")
    return list(rows)


def evidence_for_order(order: dict, rows) -> tuple[str | None, list[PrivateFill]]:
    """Match normalized fills by venue order id or stable client order id."""
    exchange_order_id = str(order.get("exchange_order_id") or "").strip() or None
    client_order_id = str(order.get("client_order_id") or "").strip() or None
    reports = _fill_reports(rows)
    if exchange_order_id:
        matched = [
            row for row in reports if str(row.venue_order_id or "").strip() == exchange_order_id
        ]
        return exchange_order_id, [_private_fill_from_report(row) for row in matched]
    if not client_order_id:
        return None, []
    matched = [
        row for row in reports if str(row.client_order_id or "").strip() == client_order_id
    ]
    evidence = [_private_fill_from_report(row) for row in matched]
    remote_ids = {item.exchange_order_id for item in evidence}
    if len(remote_ids) > 1:
        raise MaterializationIntegrityError("one client order identity maps to multiple exchange orders")
    return (next(iter(remote_ids)) if remote_ids else None), evidence




def _attribution_fields(proposal: dict) -> dict:
    raw = proposal.get("attribution_snapshot")
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except (TypeError, ValueError):
        # A persisted `attribution_snapshot` that fails JSON decoding
        # is a data-integrity signal, not "no context". Log so the two
        # cases are distinguishable in the downstream Position record;
        # keep the `{}` fallback so materialization proceeds.
        logger.warning(
            "exchange_materialization: attribution_snapshot JSON invalid "
            "(proposal=%s, using empty snapshot)",
            proposal.get("id"),
            exc_info=True,
        )
        snapshot = {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    market_context = snapshot.get("market_context")
    sizing_manifest = None
    try:
        from aletheia.trading.persona_rollout import (
            extract_sizing_manifest,
            serialize_sizing_manifest,
        )

        sizing_manifest = serialize_sizing_manifest(
            extract_sizing_manifest(
                {
                    "agent_rationale": proposal.get("agent_rationale"),
                    "attribution_snapshot": snapshot,
                }
            )
        )
    except Exception:
        # `extract/serialize_sizing_manifest` failure used to be silently
        # mapped to `None`, and the downstream `insert_position` still
        # went through — leaving Position rows with `sizing_manifest=NULL`
        # for reasons the operator could not distinguish. Log with a
        # stack so a real defect (schema drift, contract change in the
        # rollout module) is visible.
        logger.warning(
            "exchange_materialization: sizing_manifest extraction failed "
            "(proposal=%s, Position will have NULL sizing_manifest)",
            proposal.get("id"),
            exc_info=True,
        )
        sizing_manifest = None
    regime_value = snapshot.get("regime_tag", "unknown")
    return {
        "btc_entry_price": snapshot.get("btc_entry_price"),
        "regime_tag": None if regime_value is None else str(regime_value),
        "entry_market_summary": (
            json.dumps(market_context, sort_keys=True) if isinstance(market_context, dict) else None
        ),
        "decision_reasoning": proposal.get("agent_rationale"),
        "sizing_manifest": sizing_manifest,
    }


def _require_compatible_entry_position(order: dict, position: dict) -> dict:
    """Fail closed when a durable entry backlink crosses position identity."""
    from aletheia.execution.coordinator import _position_account
    from aletheia.uta.models import UTAAccountIdentity

    for field in ("symbol", "market_type", "direction", "execution_mode"):
        if position.get(field) != order.get(field):
            raise MaterializationIntegrityError(f"entry Order {field} does not match linked Position")
    if position.get("status") != "open":
        raise MaterializationIntegrityError("entry Order references a linked Position that is not open")
    try:
        account = UTAAccountIdentity.model_validate_json(order.get("uta_account_identity_json") or "")
        if _position_account(position) != account:
            raise ValueError("account mismatch")
    except ValueError as exc:
        raise MaterializationIntegrityError("entry Order account does not match linked Position") from exc
    return position


def _linked_open_position(order: dict, proposal: dict) -> dict | None:
    db = repo.get_db()
    if order.get("position_id"):
        position = repo._select_one("positions", order["position_id"])
        if position is None:
            raise MaterializationIntegrityError("Order references a missing Position")
        return _require_compatible_entry_position(order, position)
    row = db.execute(
        "SELECT * FROM positions WHERE order_id=? ORDER BY rowid ASC LIMIT 1",
        (order["id"],),
    ).fetchone()
    if row is not None:
        return _require_compatible_entry_position(order, dict(row))

    # Exchange brokers intentionally aggregate compatible entry legs into one
    # open Position.  The Order.position_id backlink records the exact later leg.
    rows = db.execute(
        """
        SELECT * FROM positions
        WHERE status='open' AND symbol=? AND market_type=? AND direction=?
          AND execution_mode=?
        ORDER BY rowid ASC
        """,
        (
            order["symbol"],
            order["market_type"],
            order["direction"],
            order["execution_mode"],
        ),
    ).fetchall()
    strategy_id = proposal.get("strategy_id")
    proposal_school = proposal.get("trade_school") or "short_term"
    for row in rows:
        candidate = dict(row)
        if strategy_id is not None and candidate.get("strategy_id") not in (
            None,
            "",
            strategy_id,
        ):
            continue
        if (candidate.get("trade_school") or "short_term") != proposal_school:
            continue
        try:
            return _require_compatible_entry_position(order, candidate)
        except MaterializationIntegrityError:
            # An unlinked candidate in another or unknown account cannot absorb
            # this private Fill. Its historical identity is never inferred.
            continue
    return None


def _tail_delta(evidence: list[PrivateFill], quantity: float) -> list[PrivateFill]:
    """Require whole unseen private Fills to explain the exact local delta.

    A venue trade id names one indivisible economic fact.  Splitting it into a
    synthetic ``:delta:`` id makes the next replay unable to match that durable
    row back to the original private Fill.  Any non-whole quantity relationship
    is therefore conflicting evidence, not a value to approximate locally.
    """
    evidence_quantity = sum(item.quantity for item in evidence)
    tolerance = max(1e-12, abs(quantity) * 1e-9)
    if quantity < -tolerance or abs(evidence_quantity - quantity) > tolerance:
        raise MaterializationIntegrityError("whole private fills cannot explain the local/exchange quantity delta")
    return list(evidence)


def _private_fill_totals(
    evidence: Iterable[PrivateFill], *, symbol: str
) -> tuple[float, float, float | None]:
    """Reject finite inputs whose derived Order economics are not finite."""
    quantity = 0.0
    notional = 0.0
    fee: float | None = 0.0
    for item in evidence:
        item_notional = item.quantity * item.price
        item_fee = fee_in_quote(
            fee=item.fee,
            fee_currency=item.fee_currency,
            fill_price=item.price,
            symbol=symbol,
        )
        quantity += item.quantity
        notional += item_notional
        if fee is not None and item_fee is not None:
            fee += item_fee
        else:
            fee = None
        finite_values = (item_notional, quantity, notional)
        if item_fee is not None:
            finite_values += (item_fee,)
        if fee is not None:
            finite_values += (fee,)
        if item_notional <= 0 or not all(math.isfinite(value) for value in finite_values):
            raise PrivateFillEvidenceInvalid("private fill derived economics must be finite")
    return quantity, notional, fee


def _materialize_partial_close_accounting(
    position: dict,
    evidence: list[PrivateFill],
    *,
    reference_price: float | None,
    close_reason: str | None,
    closed_at: str,
) -> None:
    """Persist one exact private-fill reduction and its allocated economics."""
    quantity, notional, exit_fee = _private_fill_totals(
        evidence,
        symbol=position["symbol"],
    )
    old_quantity = float(position.get("quantity") or 0.0)
    tolerance = max(1e-12, old_quantity * 1e-9)
    if old_quantity <= 0 or quantity <= 0 or quantity >= old_quantity - tolerance:
        raise MaterializationIntegrityError("partial close quantity is not a strict reduction")
    exit_price = notional / quantity
    entry_fee_value = position.get("entry_fee")
    legacy_fee_gap = False
    if entry_fee_value is not None:
        legacy_fee_gap = (
            repo.get_db()
            .execute(
                "SELECT 1 FROM position_close_events "
                "WHERE position_id=? AND is_partial=1 AND entry_fee IS NULL LIMIT 1",
                (position["id"],),
            )
            .fetchone()
            is not None
        )
        if legacy_fee_gap:
            # Older materialization reduced quantity but left the original fee
            # attached to the remaining Position. Do not allocate it again.
            entry_fee_value = None
    entry_fee_portion: float | None = None
    realized_pnl: float | None = None
    remaining_entry_fee: float | None = None
    unavailable_reasons: list[str] = []
    if legacy_fee_gap:
        unavailable_reasons.append("legacy_partial_entry_fee_unallocated")
    if entry_fee_value is None:
        unavailable_reasons.append("entry_fee_unavailable")
    if exit_fee is None:
        unavailable_reasons.append("exit_fee_conversion_unavailable")
    if str(position.get("market_type") or "") == "usdt_futures":
        unavailable_reasons.append("partial_funding_unavailable")
    if entry_fee_value is not None:
        try:
            entry_fee_total = float(entry_fee_value)
        except (TypeError, ValueError) as exc:
            raise MaterializationIntegrityError("position entry fee is invalid") from exc
        if not math.isfinite(entry_fee_total) or entry_fee_total < 0:
            raise MaterializationIntegrityError("position entry fee is invalid")
        from aletheia.portfolio.pnl import _realized_pnl

        entry_fee_portion = round(entry_fee_total * quantity / old_quantity, 8)
        remaining_entry_fee = round(entry_fee_total - entry_fee_portion, 8)
        if not unavailable_reasons:
            realized_pnl = round(
                _realized_pnl(
                    position["direction"],
                    float(position["entry_price"]),
                    exit_price,
                    quantity,
                )
                - entry_fee_portion
                - exit_fee,
                8,
            )

    # Each exit leg adds its |fill VWAP - close Order price| drag; an unknown
    # leg makes the round-trip total unavailable rather than understated.
    old_slippage = position.get("slippage_cost")
    slippage_cost = (
        round(float(old_slippage) + abs(exit_price - float(reference_price)) * quantity, 8)
        if old_slippage is not None and reference_price is not None and float(reference_price) > 0
        else None
    )
    new_quantity = round(old_quantity - quantity, 12)
    repo.update_open_position_materialization(
        position["id"],
        quantity=new_quantity,
        entry_fee=remaining_entry_fee,
        slippage_cost=slippage_cost,
    )
    repo.insert_position_close_event(
        {
            "id": new_id(),
            "position_id": position["id"],
            "symbol": position.get("symbol"),
            "direction": position.get("direction"),
            "execution_mode": position.get("execution_mode"),
            "close_reason": close_reason,
            "exit_price": exit_price,
            "closed_qty": quantity,
            "realized_pnl": realized_pnl,
            "entry_fee": entry_fee_portion,
            "exit_fee": round(exit_fee, 8) if exit_fee is not None else None,
            "is_partial": 1,
            "closed_at": closed_at,
        }
    )
    repo.insert_audit_event(
        AuditEvent(
            actor="system",
            source="exchange_materialization",
            event_type="position_partial_close_materialized",
            entity_type="position",
            entity_id=position["id"],
            before_state=json.dumps(
                {
                    "quantity": old_quantity,
                    "entry_fee": position.get("entry_fee"),
                    "pending_close_order_id": position.get("pending_close_order_id"),
                },
                sort_keys=True,
            ),
            after_state=json.dumps(
                {
                    "quantity": new_quantity,
                    "entry_fee": remaining_entry_fee,
                    "closed_qty": quantity,
                    "exit_price": exit_price,
                    "entry_fee_allocated": entry_fee_portion,
                    "exit_fee": exit_fee,
                    "realized_pnl": realized_pnl,
                    "economics_status": "complete" if realized_pnl is not None else "unavailable",
                    "unavailable_reasons": unavailable_reasons,
                },
                sort_keys=True,
            ),
            message=(
                f"Materialized partial close for Position {position['id']} "
                f"qty={quantity}; economics="
                f"{'complete' if realized_pnl is not None else 'unavailable'}"
            ),
        )
    )


def _fill_totals(
    rows: Iterable[dict], *, symbol: str
) -> tuple[float, float, float | None]:
    quantity = 0.0
    notional = 0.0
    fee: float | None = 0.0
    for row in rows:
        try:
            row_quantity = float(row["fill_quantity"])
            row_price = float(row["fill_price"])
            raw_fee = row.get("fee")
            row_fee = None if raw_fee is None else float(raw_fee)
        except (KeyError, TypeError, ValueError) as exc:
            raise MaterializationIntegrityError("durable private Fill economics are incomplete") from exc
        if (
            not math.isfinite(row_quantity)
            or not math.isfinite(row_price)
            or (row_fee is not None and (not math.isfinite(row_fee) or row_fee < 0))
            or row_quantity <= 0
            or row_price <= 0
        ):
            raise MaterializationIntegrityError("durable private Fill economics must be finite")
        row_notional = row_quantity * row_price
        row_quote_fee = fee_in_quote(
            fee=row_fee,
            fee_currency=str(row.get("fee_currency") or ""),
            fill_price=row_price,
            symbol=symbol,
        )
        if row_quote_fee is not None and fee is not None:
            fee += row_quote_fee
        else:
            fee = None
        finite_values = (row_notional, quantity, notional)
        if row_quote_fee is not None:
            finite_values += (row_quote_fee,)
        if fee is not None:
            finite_values += (fee,)
        if row_notional <= 0 or not all(math.isfinite(value) for value in finite_values):
            raise MaterializationIntegrityError("durable private Fill derived economics must be finite")
        quantity += row_quantity
        notional += row_notional
    finite_totals = (quantity, notional)
    if fee is not None:
        finite_totals += (fee,)
    if not all(math.isfinite(value) for value in finite_totals):
        raise MaterializationIntegrityError("durable private Fill totals must be finite")
    return quantity, notional, fee


def _verify_durable_fills(
    db,
    local_rows: list[dict],
    evidence: list[PrivateFill],
    *,
    quantity_tolerance: float,
    lifecycle: str,
    execution_mode: str,
    upgrade_source: bool = True,
) -> None:
    """Require every local Fill to be the same private exchange fact."""
    for local in local_rows:
        local_id = str(local["id"])
        matches = [
            item
            for item in evidence
            if local_id
            in {
                item.fill_id,
                durable_private_fill_id(execution_mode, item.fill_id),
            }
        ]
        if len(matches) != 1:
            raise MaterializationIntegrityError(f"durable {lifecycle} Fill has no same-id private evidence")
        matched = matches[0]
        if (
            abs(float(local["fill_quantity"]) - matched.quantity) > quantity_tolerance
            or abs(float(local["fill_price"]) - matched.price) > max(1e-12, matched.price * 1e-9)
            or (local.get("fee") is None) != (matched.fee is None)
            or (matched.fee is not None and abs(float(local["fee"]) - matched.fee) > max(1e-12, matched.fee * 1e-9))
            or str(local.get("fee_currency") or "").upper() != matched.fee_currency.upper()
            or _timestamp_ms(local.get("filled_at")) != matched.filled_at_ms
        ):
            raise MaterializationIntegrityError(f"durable {lifecycle} Fill conflicts with same-id private evidence")
        fee_source = "reconciled" if matched.fee is not None else "unavailable"
        if upgrade_source and local.get("fee_source") != fee_source:
            # Exact identity, economics, and exchange time allow an older row's
            # provenance to be upgraded. Any mismatch above remains fail-closed.
            db.execute(
                "UPDATE fills SET fee_source=? WHERE id=?",
                (fee_source, local["id"]),
            )


def materialize_bitget_entry(
    order_id: str,
    rows,
    *,
    historical_gap_recovery: bool = False,
) -> MaterializationResult:
    """Atomically converge one native entry Order to complete private evidence.

    Historical recovery is deliberately stricter than ordinary unresolved-order
    replay: it must prove the entire authoritative quantity in one exact private
    response and must create a new Position.  It may never reinterpret an old
    filled Order as a later averaging leg.
    """
    with _atomic_materialization():
        order = repo.get_order(order_id)
        if order is None:
            raise MaterializationIntegrityError("Order disappeared before materialization")
        if order.get("position_id") and repo._select_one("positions", order["position_id"]):
            # A linked open Position still represents an entry Order.  Close
            # Orders are linked to a Position *and* are its pending close id.
            pending = (
                repo.get_db()
                .execute(
                    "SELECT 1 FROM positions WHERE id=? AND pending_close_order_id=?",
                    (order["position_id"], order_id),
                )
                .fetchone()
            )
            if pending is not None:
                return MaterializationResult(state="not_entry", position_id=order["position_id"])

        repo.validate_native_order_authority(order)

        proposal_id = order.get("proposal_id")
        proposal = repo.get_trade_proposal(proposal_id) if proposal_id else None
        if proposal is None or proposal.get("source") != "native":
            return MaterializationResult(state="authority_missing")

        exchange_order_id, evidence = evidence_for_order(order, rows)
        if exchange_order_id is None or not evidence:
            return MaterializationResult(state="evidence_missing")
        if any(item.fee is None for item in evidence):
            raise PrivateFillEvidenceInvalid("private entry fill row is missing fee")
        if not order.get("exchange_order_id"):
            repo.update_order_metadata(order_id, exchange_order_id=exchange_order_id)
            order["exchange_order_id"] = exchange_order_id

        db = repo.get_db()
        local_rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                (order_id,),
            ).fetchall()
        ]
        local_quantity, _, _ = _fill_totals(local_rows, symbol=order["symbol"])
        exchange_quantity, _, _ = _private_fill_totals(
            evidence,
            symbol=order["symbol"],
        )
        tolerance = max(1e-12, float(order["quantity"]) * 1e-9)
        if historical_gap_recovery and abs(exchange_quantity - float(order["quantity"])) > tolerance:
            raise MaterializationIntegrityError("historical filled-gap recovery requires complete private quantity")
        if local_quantity > exchange_quantity + tolerance:
            raise MaterializationIntegrityError("local fill quantity exceeds complete private evidence")

        _verify_durable_fills(
            db,
            local_rows,
            evidence,
            quantity_tolerance=tolerance,
            lifecycle="entry",
            execution_mode=str(order.get("execution_mode") or ""),
        )
        local_ids = {str(row["id"]) for row in local_rows}
        execution_mode = str(order.get("execution_mode") or "")
        unseen_evidence = [
            item
            for item in evidence
            if not _private_fill_already_local(
                item,
                local_ids,
                execution_mode=execution_mode,
            )
        ]
        missing_quantity = max(0.0, exchange_quantity - local_quantity)
        delta_evidence = _tail_delta(unseen_evidence, missing_quantity)

        delta_quantity, delta_notional, delta_fee = _private_fill_totals(
            delta_evidence,
            symbol=order["symbol"],
        )

        # Keep the exposure check inside the same BEGIN IMMEDIATE transaction as
        # the Fill/Position writes.  The read-only preflight in reconciliation is
        # useful for an explicit reason, but cannot by itself close the race with
        # another worker opening a compatible Position.
        if historical_gap_recovery:
            compatible = db.execute(
                """
                SELECT 1
                FROM positions
                WHERE status='open' AND symbol=? AND market_type=? AND direction=?
                  AND execution_mode=?
                LIMIT 1
                """,
                (
                    order["symbol"],
                    order["market_type"],
                    order["direction"],
                    order["execution_mode"],
                ),
            ).fetchone()
            if compatible is not None:
                raise MaterializationIntegrityError("historical filled-gap recovery cannot merge into an open Position")

        position = _linked_open_position(order, proposal)
        if historical_gap_recovery and position is not None:
            raise MaterializationIntegrityError("historical filled-gap recovery cannot attach to an existing Position")
        for item in delta_evidence:
            repo.insert_fill(
                Fill(
                    id=durable_private_fill_id(execution_mode, item.fill_id),
                    order_id=order_id,
                    fill_price=item.price,
                    fill_quantity=item.quantity,
                    fee=item.fee,
                    fee_currency=item.fee_currency,
                    fee_source="reconciled",
                    realized_liquidity=item.realized_liquidity,
                    filled_at=item.filled_at,
                )
            )

        if position is None:
            all_local_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            total_quantity, total_notional, total_fee = _fill_totals(
                all_local_rows,
                symbol=order["symbol"],
            )
            if total_quantity <= 0:
                return MaterializationResult(state="evidence_missing", exchange_order_id=exchange_order_id)
            entry_price = total_notional / total_quantity
            requested_price = float(order.get("price") or entry_price)
            slippage_cost = abs(entry_price - requested_price) * total_quantity
            liquidation_price = liquidation_price_for(
                entry_price,
                order["direction"],
                proposal.get("leverage"),
                order["symbol"],
            )
            if (
                entry_price <= 0
                or not all(math.isfinite(value) for value in (entry_price, requested_price, slippage_cost))
                or (liquidation_price is not None and (not math.isfinite(liquidation_price) or liquidation_price <= 0))
            ):
                raise MaterializationIntegrityError("materialized entry economics must be finite")
            position_model = Position(
                symbol=order["symbol"],
                market_type=order["market_type"],
                direction=order["direction"],
                quantity=total_quantity,
                entry_price=entry_price,
                current_price=entry_price,
                execution_mode=order["execution_mode"],
                strategy_id=proposal.get("strategy_id"),
                proposal_id=proposal_id,
                order_id=order_id,
                opportunity_id=proposal.get("opportunity_id"),
                leverage=proposal.get("leverage"),
                liquidation_price=liquidation_price,
                stop_price=proposal.get("stop_price"),
                take_profit_price=proposal.get("take_profit"),
                trade_school=proposal.get("trade_school") or "short_term",
                entry_fee=total_fee,
                entry_price_source="broker_fill",
                entry_fill_verified=True,
                opened_at=min(evidence, key=lambda item: item.filled_at_ms).filled_at,
                slippage_cost=slippage_cost,
                **_attribution_fields(proposal),
            )
            repo.insert_position(position_model)
            position_id = position_model.id
            _exit_intent: dict = {}
            if proposal.get("expected_horizon_hours") is not None:
                _exit_intent["expected_horizon_hours"] = proposal["expected_horizon_hours"]
            if proposal.get("stop_source"):
                _exit_intent["stop_source"] = proposal["stop_source"]
            if _exit_intent:
                _exit_intent["exit_plan_version"] = 1
                repo.update_open_position_exit_plan(position_id, **_exit_intent)
        else:
            if position.get("status") != "open":
                raise MaterializationIntegrityError("entry fill maps to a non-open Position")
            position_id = position["id"]
            if delta_quantity > 0:
                old_quantity = float(position["quantity"])
                old_entry = float(position["entry_price"])
                old_entry_fee = position.get("entry_fee")
                if old_entry_fee is not None:
                    old_entry_fee = float(old_entry_fee)
                old_slippage = float(position.get("slippage_cost") or 0.0)
                new_quantity = old_quantity + delta_quantity
                current_price = delta_notional / delta_quantity
                new_entry = (old_quantity * old_entry + delta_notional) / new_quantity
                new_entry_fee = (
                    old_entry_fee + delta_fee
                    if old_entry_fee is not None and delta_fee is not None
                    else None
                )
                new_slippage = (
                    old_slippage + abs(current_price - float(order.get("price") or current_price)) * delta_quantity
                )
                finite_fee_values = (
                    (old_entry_fee, new_entry_fee)
                    if old_entry_fee is not None and new_entry_fee is not None
                    else ()
                )
                if (
                    new_quantity <= 0
                    or new_entry <= 0
                    or current_price <= 0
                    or not all(
                        math.isfinite(value)
                        for value in (
                            old_quantity,
                            old_entry,
                            old_slippage,
                            new_quantity,
                            new_entry,
                            current_price,
                            new_slippage,
                        )
                    )
                    or any(not math.isfinite(value) for value in finite_fee_values)
                ):
                    raise MaterializationIntegrityError("averaged entry economics must be finite")
                update_fields = {
                    "quantity": new_quantity,
                    "entry_price": new_entry,
                    "current_price": current_price,
                    "entry_fee": new_entry_fee,
                    "slippage_cost": new_slippage,
                }
                # The new leg is privately verified, but it cannot retroactively
                # prove how an older entry leg was priced.  Only a Position whose
                # existing aggregate was already verified can remain verified
                # after this weighted-average update.
                existing_entry_verified = (
                    bool(position.get("entry_fill_verified")) and position.get("entry_price_source") == "broker_fill"
                )
                update_fields["entry_fill_verified"] = existing_entry_verified
                if existing_entry_verified:
                    update_fields["entry_price_source"] = "broker_fill"
                merged_stop = merge_protective_stop(
                    order["direction"],
                    proposal.get("stop_price"),
                    position.get("stop_price"),
                )
                if merged_stop is not None:
                    update_fields["stop_price"] = merged_stop
                if proposal.get("expected_horizon_hours") is not None:
                    update_fields["expected_horizon_hours"] = proposal["expected_horizon_hours"]
                    update_fields["exit_plan_version"] = 1
                if proposal.get("stop_source"):
                    update_fields["stop_source"] = proposal["stop_source"]
                    update_fields["exit_plan_version"] = 1
                if proposal.get("trade_school"):
                    update_fields["trade_school"] = proposal["trade_school"]
                position_leverage = position.get("leverage") or proposal.get("leverage")
                if position_leverage and float(position_leverage) > 1:
                    update_fields["liquidation_price"] = liquidation_price_for(
                        new_entry,
                        order["direction"],
                        int(position_leverage),
                        order["symbol"],
                    )
                exit_fields = {
                    name: update_fields.pop(name)
                    for name in tuple(update_fields)
                    if name in {
                        "stop_price", "expected_horizon_hours", "stop_source",
                        "exit_plan_version",
                    }
                }
                repo.update_open_position_materialization(position_id, **update_fields)
                if exit_fields:
                    repo.update_open_position_exit_plan(position_id, **exit_fields)

        requested_quantity = float(order["quantity"])
        if abs(exchange_quantity - requested_quantity) <= tolerance:
            state = "filled"
            proposal_state = "executed"
            error_reason = None
            filled_at = max(item.filled_at for item in evidence)
        elif exchange_quantity < requested_quantity - tolerance:
            state = "partially_filled"
            proposal_state = "partially_filled"
            error_reason = None
            filled_at = None
        else:
            # #2084 (owner 2026-10-09): the real exposure is an ordinary Position;
            # the Order keeps `overfilled` and the Position carries the flag.
            state = "overfilled"
            proposal_state = "executed"
            error_reason = "exchange_quantity_exceeds_authoritative_order"
            filled_at = max(item.filled_at for item in evidence)
            _flag_entry_quantity_divergence(position_id, order_id, requested_quantity, exchange_quantity)
        repo.transition_order(
            order_id,
            exchange_order_id=exchange_order_id,
            position_id=position_id,
            status=state,
            error_reason=error_reason,
            filled_at=filled_at,
        )
        repo.update_trade_proposal(proposal_id, status=proposal_state)
        return MaterializationResult(
            state=state,
            new_fill_count=len(delta_evidence),
            position_id=position_id,
            exchange_order_id=exchange_order_id,
        )


def _flag_entry_quantity_divergence(
    position_id: str, order_id: str, planned: float, filled: float
) -> None:
    """Mark an overfilled entry's Position as not exact (#2084); once per Order."""
    position = repo._select_one("positions", position_id) or {}
    current = position.get("entry_quantity_divergence")
    if current and json.loads(current).get("order_id") == order_id:
        return
    divergence = json.dumps(
        {"order_id": order_id, "planned_quantity": planned, "filled_quantity": filled},
        sort_keys=True,
    )
    repo.update_open_position_materialization(position_id, entry_quantity_divergence=divergence)
    repo.insert_audit_event(
        AuditEvent(
            actor="system",
            source="exchange_materialization",
            event_type="entry_fill_quantity_diverged",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps({"entry_quantity_divergence": current}, sort_keys=True),
            after_state=divergence,
            message=(
                f"Entry Order {order_id} filled {filled} against planned {planned}; "
                "the Position is open but its quantity is not the planned quantity."
            ),
        )
    )
    logger.warning(
        "entry fill quantity diverged: position=%s order=%s planned=%s filled=%s",
        position_id, order_id, planned, filled,
    )


def _position_for_close_order(order: dict) -> dict | None:
    position_id = order.get("position_id")
    if position_id:
        position = repo._select_one("positions", position_id)
        if position is None:
            raise MaterializationIntegrityError("close Order references a missing Position")
        if position.get("pending_close_order_id") not in (None, "", order["id"]):
            raise MaterializationIntegrityError("close Order does not own the Position pending-close claim")
        return position
    row = (
        repo.get_db()
        .execute(
            "SELECT * FROM positions WHERE pending_close_order_id=? ORDER BY rowid ASC LIMIT 1",
            (order["id"],),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def funding_request_for_complete_close(order_id: str, rows) -> dict | None:
    """Return the exact account/symbol/window needed for close funding evidence."""
    order = repo.get_order(order_id)
    if order is None or order.get("market_type") != "usdt_futures":
        return None
    position = _position_for_close_order(order)
    if position is None or position.get("status") != "open":
        return None
    try:
        exchange_order_id, evidence = evidence_for_order(order, rows)
        if exchange_order_id is None or not evidence:
            return None
        exchange_quantity, _, _ = _private_fill_totals(
            evidence,
            symbol=order["symbol"],
        )
    except MaterializationIntegrityError:
        # Funding is optional pre-work. The materializer below still classifies
        # the malformed exact-order evidence and leaves the close unresolved.
        return None
    requested_quantity = float(order["quantity"])
    tolerance = max(1e-12, requested_quantity * 1e-9)
    if abs(exchange_quantity - requested_quantity) > tolerance:
        return None
    closed_at = max(item.filled_at for item in evidence)
    from aletheia.execution.broker_funding import (
        BrokerFundingIntegrityError,
        funding_query_window,
    )

    try:
        start_ms, end_ms = funding_query_window(position, closed_at)
    except BrokerFundingIntegrityError:
        # Fill materialization remains operationally authoritative even when an
        # old fixture/legacy row has no usable holding window. Funding stays
        # unavailable and the recovery path can retry only after valid evidence.
        return None
    return {
        "position": position,
        "closed_at": closed_at,
        "start_time_ms": start_ms,
        "end_time_ms": end_ms,
    }


_PRIVATE_CLOSE_EVIDENCE = {
    "bitget_demo": ("bitget_private_close_fills", "private_fills", "demo"),
    "bitget_live_limited": (
        "bitget_private_close_fills",
        "private_fills",
        "live",
    ),
    "okx_demo": (
        "okx_demo_private_close_fills",
        "/api/v5/trade/fills-history",
        "demo",
    ),
    "okx_live_limited": (
        "okx_live_limited_private_close_fills",
        "/api/v5/trade/fills-history",
        "live",
    ),
    "binance_testnet": (
        "binance_testnet_private_close_fills",
        "/fapi/v1/userTrades",
        "testnet",
    ),
    "binance_live_limited": (
        "binance_live_limited_private_close_fills",
        "/fapi/v1/userTrades",
        "live",
    ),
}


def _materialize_complete_private_close(
    order_id: str, rows, funding_ledger: dict | None = None
) -> MaterializationResult | None:
    """Close only when private evidence exactly covers the requested quantity.

    ``close_position`` owns the transaction and runs our database-only callback
    before committing. Its audit, notification, and feedback side effects happen
    only after that commit, so a late Order/Fill write failure cannot announce a
    close that SQLite rolled back.
    """
    order = repo.get_order(order_id)
    if order is None:
        raise MaterializationIntegrityError("close Order disappeared before materialization")
    position = _position_for_close_order(order)
    if position is None:
        return None
    # Gate: clipped Order (qty < Position qty) → atomic partial-reduction path
    position_quantity = float(position["quantity"])
    order_quantity = float(order["quantity"])
    _pos_tolerance = max(1e-12, order_quantity * 1e-9)
    if order_quantity < position_quantity - _pos_tolerance:
        return None  # Let atomic branch handle exact-clip-fill partial reduction

    # A filled order (from partial-reduce or full-close path) must not re-process.
    if order.get("status") == "filled":
        return MaterializationResult(
            state="filled",
            position_id=position["id"],
            exchange_order_id=order.get("exchange_order_id"),
        )
    if position.get("status") != "open":
        raise MaterializationIntegrityError("close fill maps to a Position that is neither open nor closed")
    repo.validate_native_order_authority(order)
    exchange_order_id, evidence = evidence_for_order(order, rows)
    if exchange_order_id is None or not evidence:
        return None
    requested_quantity = float(order["quantity"])
    exchange_quantity, total_notional, total_fee = _private_fill_totals(
        evidence,
        symbol=order["symbol"],
    )
    tolerance = max(1e-12, requested_quantity * 1e-9)
    close_reasoning = _close_reasoning_for_order(order)
    if abs(exchange_quantity - requested_quantity) > tolerance:
        return None

    db = repo.get_db()
    local_rows = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
            (order_id,),
        ).fetchall()
    ]
    execution_mode = str(order.get("execution_mode") or "")
    _verify_durable_fills(
        db,
        local_rows,
        evidence,
        quantity_tolerance=tolerance,
        lifecycle="close",
        execution_mode=execution_mode,
        upgrade_source=False,
    )
    local_quantity, _, _ = _fill_totals(local_rows, symbol=order["symbol"])
    if local_quantity > exchange_quantity + tolerance:
        raise MaterializationIntegrityError("local close quantity exceeds complete private evidence")

    exit_price = total_notional / exchange_quantity
    if not math.isfinite(exit_price) or exit_price <= 0:
        raise PrivateFillEvidenceInvalid("private close exit price must be finite")
    closed_at = max(item.filled_at for item in evidence)
    evidence_identity = _PRIVATE_CLOSE_EVIDENCE.get(execution_mode)
    if evidence_identity is None:
        raise MaterializationIntegrityError("close private evidence account is unsupported")
    evidence_source, source_endpoint, account_environment = evidence_identity
    inserted_count = 0
    funding = None
    close_authority_fields = (
        "proposal_id",
        "position_id",
        "symbol",
        "market_type",
        "direction",
        "quantity",
        "execution_mode",
        "source",
        "client_order_id",
        "close_reason",
        "close_reasoning",
    )

    def _materialize_close_rows() -> None:
        nonlocal inserted_count
        current_order = repo.get_order(order_id)
        if current_order is None:
            raise MaterializationIntegrityError("close Order disappeared during materialization")
        if any(current_order.get(field) != order.get(field) for field in close_authority_fields):
            raise MaterializationIntegrityError("close Order authority changed during materialization")
        repo.validate_native_order_authority(current_order)
        if _close_reasoning_for_order(current_order) != close_reasoning:
            raise MaterializationIntegrityError("close reasoning changed during materialization")
        current_position = _position_for_close_order(current_order)
        if current_position is None or current_position["id"] != position["id"]:
            raise MaterializationIntegrityError("close Order lost its Position claim")
        current_exchange_id, current_evidence = evidence_for_order(current_order, rows)
        if current_exchange_id != exchange_order_id or current_evidence != evidence:
            raise MaterializationIntegrityError("private close evidence changed during materialization")
        if funding_ledger is not None and funding is not None:
            from aletheia.execution.broker_funding import (
                BrokerFundingIntegrityError,
                compute_stored_broker_funding,
            )

            try:
                current_funding = compute_stored_broker_funding(
                    repo.get_db(),
                    position=current_position,
                    closed_at=closed_at,
                    ledger=funding_ledger,
                )
            except BrokerFundingIntegrityError as exc:
                raise MaterializationIntegrityError(str(exc)) from exc
            if current_funding != funding:
                raise MaterializationIntegrityError("broker funding attribution changed during close commit")
        current_rows = [
            dict(row)
            for row in repo.get_db()
            .execute(
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                (order_id,),
            )
            .fetchall()
        ]
        _verify_durable_fills(
            repo.get_db(),
            current_rows,
            evidence,
            quantity_tolerance=tolerance,
            lifecycle="close",
            execution_mode=execution_mode,
        )
        current_ids = {str(row["id"]) for row in current_rows}
        unseen = [
            item
            for item in evidence
            if not _private_fill_already_local(
                item,
                current_ids,
                execution_mode=execution_mode,
            )
        ]
        current_quantity, _, _ = _fill_totals(current_rows, symbol=order["symbol"])
        missing_quantity = exchange_quantity - current_quantity
        if missing_quantity < -tolerance or abs(sum(item.quantity for item in unseen) - missing_quantity) > tolerance:
            raise MaterializationIntegrityError("durable close Fill quantity conflicts with private evidence")
        for item in unseen:
            repo.insert_fill(
                Fill(
                    id=durable_private_fill_id(execution_mode, item.fill_id),
                    order_id=order_id,
                    fill_price=item.price,
                    fill_quantity=item.quantity,
                    fee=item.fee,
                    fee_currency=item.fee_currency,
                    fee_source="reconciled" if item.fee is not None else "unavailable",
                    realized_liquidity=item.realized_liquidity,
                    filled_at=item.filled_at,
                )
            )
        inserted_count = len(unseen)
        repo.transition_order(
            order_id,
            exchange_order_id=exchange_order_id,
            position_id=position["id"],
            status="filled",
            error_reason=None,
            filled_at=max(item.filled_at for item in evidence),
        )
        repo.release_position_close_claim(position["id"], order_id=order_id)

    from aletheia.portfolio.pnl import close_position

    funding_kwargs: dict = {}
    if position.get("market_type") == "usdt_futures" and funding_ledger is not None:
        from aletheia.execution.broker_funding import compute_stored_broker_funding

        funding = compute_stored_broker_funding(
            repo.get_db(),
            position=position,
            closed_at=closed_at,
            ledger=funding_ledger,
        )
        funding_kwargs = {
            "funding_cost": funding.funding_cost,
            "funding_source": funding.funding_source,
            "funding_evidence": funding.evidence,
        }

    close_position(
        position["id"],
        exit_price=exit_price,
        exit_reference_price=order.get("price"),
        reason=order.get("close_reason") or "verified_exit",
        actual_exit_fee=total_fee,
        closed_at=max(item.filled_at for item in evidence),
        verified_exit_evidence={
            "source": evidence_source,
            "source_endpoint": source_endpoint,
            "broker_id": execution_mode,
            "account_environment": account_environment,
            "execution_mode": execution_mode,
            "exchange_order_id": exchange_order_id,
            "client_order_id": order.get("client_order_id"),
            "fill_ids": [item.fill_id for item in evidence],
            "durable_fill_ids": [durable_private_fill_id(execution_mode, item.fill_id) for item in evidence],
            "fill_quantity": exchange_quantity,
            "fee_in_quote": total_fee,
            "fee_status": "complete" if total_fee is not None else "unavailable",
            "fee_unavailable_reason": (
                None if total_fee is not None else "fee_amount_or_conversion_unavailable"
            ),
            "fills": [
                {
                    "fill_id": item.fill_id,
                    "remote_fill_id": item.fill_id,
                    "durable_fill_id": durable_private_fill_id(execution_mode, item.fill_id),
                    "price": item.price,
                    "quantity": item.quantity,
                    "fee": item.fee,
                    "fee_currency": item.fee_currency,
                    "filled_at": item.filled_at,
                }
                for item in evidence
            ],
        },
        materialize_close=_materialize_close_rows,
        close_reasoning=close_reasoning,
        **funding_kwargs,
    )
    return MaterializationResult(
        state="filled",
        new_fill_count=inserted_count,
        position_id=position["id"],
        exchange_order_id=exchange_order_id,
    )


def materialize_bitget_close(order_id: str, rows, funding_ledger: dict | None = None) -> MaterializationResult:
    """Converge an asynchronously filled exchange close without invention."""
    try:
        complete = _materialize_complete_private_close(order_id, rows, funding_ledger=funding_ledger)
        if complete is not None:
            return complete
        with _atomic_materialization():
            order = repo.get_order(order_id)
            if order is None:
                raise MaterializationIntegrityError("close Order disappeared before materialization")
            position = _position_for_close_order(order)
            if position is None:
                return MaterializationResult(state="not_close")
            if position.get("status") == "closed" and order.get("status") == "filled":
                return MaterializationResult(
                    state="filled",
                    position_id=position["id"],
                    exchange_order_id=order.get("exchange_order_id"),
                )
            if position.get("status") != "open":
                raise MaterializationIntegrityError("close fill maps to a Position that is neither open nor closed")
            repo.validate_native_order_authority(order)
            _close_reasoning_for_order(order)

            exchange_order_id, evidence = evidence_for_order(order, rows)
            if exchange_order_id is None or not evidence:
                return MaterializationResult(state="evidence_missing")
            if not order.get("exchange_order_id"):
                repo.update_order_metadata(order_id, exchange_order_id=exchange_order_id)
                order["exchange_order_id"] = exchange_order_id

            db = repo.get_db()
            local_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (order_id,),
                ).fetchall()
            ]
            local_quantity, _, _ = _fill_totals(
                local_rows,
                symbol=order["symbol"],
            )
            exchange_quantity, _, _ = _private_fill_totals(
                evidence,
                symbol=order["symbol"],
            )
            requested_quantity = float(order["quantity"])
            tolerance = max(1e-12, requested_quantity * 1e-9)
            execution_mode = str(order.get("execution_mode") or "")
            if local_quantity > exchange_quantity + tolerance:
                raise MaterializationIntegrityError("local close quantity exceeds complete private evidence")
            _verify_durable_fills(
                db,
                local_rows,
                evidence,
                quantity_tolerance=tolerance,
                lifecycle="close",
                execution_mode=execution_mode,
            )
            local_ids = {str(row["id"]) for row in local_rows}
            unseen_evidence = [
                item
                for item in evidence
                if not _private_fill_already_local(
                    item,
                    local_ids,
                    execution_mode=execution_mode,
                )
            ]
            delta_evidence = _tail_delta(unseen_evidence, max(0.0, exchange_quantity - local_quantity))
            for item in delta_evidence:
                repo.insert_fill(
                    Fill(
                        id=durable_private_fill_id(execution_mode, item.fill_id),
                        order_id=order_id,
                        fill_price=item.price,
                        fill_quantity=item.quantity,
                        fee=item.fee,
                        fee_currency=item.fee_currency,
                        fee_source="reconciled" if item.fee is not None else "unavailable",
                        realized_liquidity=item.realized_liquidity,
                        filled_at=item.filled_at,
                    )
                )

            repo.update_order_metadata(
                order_id,
                exchange_order_id=exchange_order_id,
                position_id=position["id"],
            )
            old_qty = float(position.get("quantity") or 0.0)
            position_tolerance = max(1e-12, old_qty * 1e-9)
            remaining_qty = max(0.0, old_qty - exchange_quantity)
            if remaining_qty > position_tolerance:
                # PARTIAL CLOSE (venue evidence): the fills so far do not cover
                # the remaining Position. Reduce the Position's quantity by the
                # filled amount so risk/PnL see the correct (reduced) exposure,
                # and record a per-close ledger row (is_partial=1) so the
                # daily-loss breaker sees the partial reduce's economics. Two
                # shapes land here: an order the exchange only partially
                # filled (claim retained for continued polling), and a
                # clipped close Order that filled exactly but intentionally
                # covered less than the whole Position (order done, claim
                # released so the next protective cycle submits the next clip).
                order_fully_filled = exchange_quantity >= requested_quantity - tolerance
                if not delta_evidence:
                    if order_fully_filled:
                        # Replay after the clip completed (e.g. the Order row's
                        # quantity was aligned with the venue-booked clip):
                        # converge the Order and release the claim without
                        # touching quantities — every fill is already applied.
                        repo.transition_order(
                            order_id,
                            status="filled",
                            error_reason=None,
                            filled_at=_ms_to_iso(max(item.filled_at_ms for item in evidence)),
                        )
                        repo.release_position_close_claim(
                            position["id"], order_id=order_id
                        )
                        return MaterializationResult(
                            state="filled_clip_partial_position",
                            new_fill_count=0,
                            position_id=position["id"],
                            exchange_order_id=exchange_order_id,
                        )
                    # A no-new-fills poll: idempotent no-op. Reducing again by
                    # the order's total evidence would double-count fills the
                    # Position already reflects.
                    return MaterializationResult(
                        state="partially_filled",
                        new_fill_count=0,
                        position_id=position["id"],
                        exchange_order_id=exchange_order_id,
                    )
                # The Position may already reflect earlier fills of this same
                # order (multi-batch partials); only the delta may reduce it.
                closed_at_ms = max(item.filled_at_ms for item in delta_evidence)
                if order_fully_filled:
                    repo.transition_order(
                        order_id,
                        status="filled",
                        error_reason=None,
                        filled_at=_ms_to_iso(closed_at_ms),
                    )
                    repo.release_position_close_claim(
                        position["id"], order_id=order_id
                    )
                else:
                    repo.transition_order(
                        order_id,
                        status="partially_filled",
                        error_reason=None,
                        filled_at=None,
                    )
                    repo.update_position_close_claim(
                        position["id"],
                        order_id=order_id,
                        status="partially_filled",
                    )
                _materialize_partial_close_accounting(
                    position,
                    delta_evidence,
                    reference_price=order.get("price"),
                    close_reason=order.get("close_reason"),
                    closed_at=_ms_to_iso(closed_at_ms),
                )
                return MaterializationResult(
                    state=("filled_clip_partial_position" if order_fully_filled else "partially_filled"),
                    new_fill_count=len(delta_evidence),
                    position_id=position["id"],
                    exchange_order_id=exchange_order_id,
                )
            if exchange_quantity > requested_quantity + tolerance:
                repo.transition_order(
                    order_id,
                    status="overfilled",
                    error_reason="exchange_close_quantity_exceeds_position",
                    filled_at=max(item.filled_at for item in evidence),
                )
                repo.update_position_close_claim(
                    position["id"], order_id=order_id, status="overfilled"
                )
                return MaterializationResult(
                    state="overfilled",
                    new_fill_count=len(delta_evidence),
                    position_id=position["id"],
                    exchange_order_id=exchange_order_id,
                )

            # Exact fill of a clipped Order: idempotent partial position reduction.
            # _materialize_complete_private_close returned None (order_qty < pos_qty),
            # so we own the update here inside the atomic transaction.
            new_delta = exchange_quantity - local_quantity
            if new_delta < -tolerance:
                raise MaterializationIntegrityError("partial close local qty exceeds evidence")
            # The guard `new_delta > tolerance` ensures we only write when there are
            # genuinely new fills to account for (idempotent on pure replay).
            if new_delta > tolerance:
                _materialize_partial_close_accounting(
                    position,
                    delta_evidence,
                    reference_price=order.get("price"),
                    close_reason=order.get("close_reason") or "clipped_partial",
                    closed_at=max(i.filled_at for i in delta_evidence),
                )
            repo.transition_order(
                order_id,
                status="filled",
                error_reason=None,
                filled_at=max(i.filled_at for i in evidence),
                exchange_order_id=exchange_order_id,
            )
            repo.release_position_close_claim(position["id"], order_id=order_id)
            return MaterializationResult(
                state="filled_clip_partial_position",
                new_fill_count=len(delta_evidence),
                position_id=position["id"],
                exchange_order_id=exchange_order_id,
            )
    except PrivateFillEvidenceInvalid:
        raise
    except Exception:
        # If any local write fails after private evidence was accepted, preserve
        # an explicit retry-suppressing handoff state in a fresh transaction.
        try:
            with repo.transaction(immediate=True):
                order = repo.get_order(order_id)
                if order is not None:
                    repo.transition_order(
                        order_id,
                        status="materialization_missing",
                        error_reason="exchange_fill_local_materialization_missing",
                        filled_at=None,
                    )
                    position = _position_for_close_order(order)
                    if position is not None and position.get("status") == "open":
                        repo.update_position_close_claim(
                            position["id"],
                            order_id=order_id,
                            status="materialization_missing",
                        )
        except Exception:
            logger.warning("exchange_materialization: handoff audit write failed (re-raising original)", exc_info=True)
        raise
