"""Fail-closed recovery of *historical* local-paper accounting evidence.

This module exists because releases before the durable Order -> Position
backlink wrote a correct primary ``positions.order_id`` but omitted its reverse
link.  Later funding validation correctly required the reverse link.  Recovery
therefore has two deliberately separate results:

* an exact, idempotent backlink repair for an Order already named by a Position;
* an additive accounting reconciliation that is never a verified strategy
  outcome and never causes a paper-cash settlement.

Raw Position economics and external/demo history are intentionally immutable.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping

from aletheia.portfolio.local_funding import (
    _ENTRY_TIME_TOLERANCE,
    _EntryFill,
    _parse_utc,
    compute_local_funding_from_entry_fills,
)


RECONCILIATION_VERSION = "historical-local-paper-accounting-v1"
_METHOD = "deterministic_legacy_entry_ledger_plus_complete_local_funding"


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _same(left: Any, right: Any, *, tolerance: float = 1e-8) -> bool:
    a, b = _finite(left), _finite(right)
    if a is None or b is None:
        return False
    return abs(a - b) <= max(tolerance, abs(a) * 1e-9, abs(b) * 1e-9)


def _closed_rows(db) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(
        """
        SELECT p.*, o.id AS entry_order_id, o.position_id AS entry_order_position_id,
               o.proposal_id AS entry_order_proposal_id, o.risk_decision_id,
               o.symbol AS entry_order_symbol, o.market_type AS entry_order_market_type,
               o.direction AS entry_order_direction, o.execution_mode AS entry_order_execution_mode,
               o.quantity AS entry_order_quantity, o.status AS entry_order_status,
               o.source AS entry_order_source, o.close_reason AS entry_order_close_reason,
               tp.symbol AS proposal_symbol, tp.market_type AS proposal_market_type,
               tp.direction AS proposal_direction, tp.execution_mode AS proposal_execution_mode,
               tp.quantity AS proposal_quantity, tp.source AS proposal_source,
               rd.proposal_id AS risk_proposal_id, rd.approved AS risk_approved,
               rd.allowed_execution_mode AS risk_allowed_execution_mode,
               rd.decision_role AS risk_decision_role
        FROM positions p
        LEFT JOIN orders o ON o.id = p.order_id
        LEFT JOIN trade_proposals tp ON tp.id = o.proposal_id
        LEFT JOIN risk_decisions rd ON rd.id = o.risk_decision_id
        WHERE p.execution_mode='paper_internal' AND p.source='native' AND p.status='closed'
        ORDER BY p.rowid ASC
        """
    ).fetchall()]


def _link_plan(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a conservative plan for the missing reverse Order backlink."""
    position_id = str(row.get("id") or "")
    order_id = str(row.get("entry_order_id") or "")
    if not position_id or not order_id:
        return {"position_id": position_id, "order_id": order_id, "action": "unavailable", "reason": "missing_primary_entry_order"}
    current = row.get("entry_order_position_id")
    if current == position_id:
        return {"position_id": position_id, "order_id": order_id, "action": "already_linked", "reason": None}
    if current not in (None, ""):
        return {"position_id": position_id, "order_id": order_id, "action": "unavailable", "reason": "entry_order_linked_elsewhere"}
    identities = ("symbol", "market_type", "direction", "execution_mode")
    if any(row.get(f"entry_order_{field}") != row.get(field) for field in identities):
        return {"position_id": position_id, "order_id": order_id, "action": "unavailable", "reason": "order_position_identity_mismatch"}
    if (
        row.get("entry_order_proposal_id") != row.get("proposal_id")
        or row.get("entry_order_status") != "filled"
        or row.get("entry_order_source") != "native"
        or str(row.get("entry_order_close_reason") or "").strip()
    ):
        return {"position_id": position_id, "order_id": order_id, "action": "unavailable", "reason": "non_entry_primary_order"}
    return {"position_id": position_id, "order_id": order_id, "action": "repair", "reason": "exact_primary_order_backlink_missing"}


def _legacy_entry_fills(db, row: Mapping[str, Any]) -> tuple[list[_EntryFill] | None, str | None, dict[str, Any]]:
    """Validate old entry evidence without treating old Risk rows as current authority."""
    position_id = str(row.get("id") or "")
    order_id = str(row.get("entry_order_id") or "")
    evidence: dict[str, Any] = {
        "legacy_authority": str(row.get("risk_decision_role") or "") != "authoritative",
        "entry_order_id": order_id,
        "risk_decision_role": row.get("risk_decision_role"),
    }
    identities = ("symbol", "market_type", "direction", "execution_mode")
    if not position_id or not order_id:
        return None, "missing_primary_entry_order", evidence
    if (
        any(row.get(f"entry_order_{field}") != row.get(field) for field in identities)
        or any(row.get(f"proposal_{field}") != row.get(field) for field in identities)
        or row.get("entry_order_proposal_id") != row.get("proposal_id")
        or row.get("risk_proposal_id") != row.get("proposal_id")
        or row.get("entry_order_status") != "filled"
        or row.get("entry_order_source") != "native"
        or row.get("proposal_source") != "native"
        or not bool(row.get("risk_approved"))
        or row.get("risk_allowed_execution_mode") != row.get("execution_mode")
        or str(row.get("entry_order_close_reason") or "").strip()
    ):
        return None, "legacy_entry_identity_mismatch", evidence
    if not _same(row.get("entry_order_quantity"), row.get("proposal_quantity")):
        return None, "legacy_entry_quantity_mismatch", evidence
    fills = [dict(item) for item in db.execute(
        "SELECT id, fill_price, fill_quantity, filled_at FROM fills WHERE order_id=? ORDER BY rowid ASC",
        (order_id,),
    ).fetchall()]
    if not fills:
        return None, "missing_entry_fills", evidence
    validated: list[_EntryFill] = []
    for fill in fills:
        price, quantity, filled_at = _finite(fill.get("fill_price")), _finite(fill.get("fill_quantity")), _parse_utc(fill.get("filled_at"))
        if not fill.get("id") or price is None or quantity is None or price <= 0 or quantity <= 0 or filled_at is None:
            return None, "legacy_entry_fill_invalid", evidence
        validated.append(_EntryFill(order_id=order_id, proposal_id=str(row.get("proposal_id") or ""), fill_id=str(fill["id"]), fill_price=price, fill_quantity=quantity, filled_at=filled_at))
    position_quantity, entry_price = _finite(row.get("quantity")), _finite(row.get("entry_price"))
    if position_quantity is None or position_quantity <= 0 or entry_price is None or entry_price <= 0:
        return None, "invalid_position_entry", evidence
    if not _same(sum(item.fill_quantity for item in validated), position_quantity):
        return None, "legacy_entry_quantity_mismatch", evidence
    if not _same(sum(item.fill_price * item.fill_quantity for item in validated), entry_price * position_quantity):
        return None, "legacy_entry_price_mismatch", evidence
    opened_at = _parse_utc(row.get("opened_at"))
    if opened_at is None or any(item.filled_at < opened_at - _ENTRY_TIME_TOLERANCE for item in validated):
        return None, "legacy_entry_time_mismatch", evidence
    validated.sort(key=lambda item: (item.filled_at, item.fill_id))
    if abs(validated[0].filled_at - opened_at) > _ENTRY_TIME_TOLERANCE:
        return None, "legacy_primary_entry_time_mismatch", evidence
    evidence["entry_fill_ids"] = [item.fill_id for item in validated]
    return validated, None, evidence


def _economic_result(db, row: Mapping[str, Any]) -> dict[str, Any]:
    position_id = str(row.get("id") or "")
    result: dict[str, Any] = {"position_id": position_id, "status": "unavailable", "reason": None, "funding_cost": None, "reconstructed_realized_pnl": None, "method": _METHOD, "evidence": {}}
    if str(row.get("status") or "") != "closed":
        result["reason"] = "position_not_closed"
        return result
    fills, error, evidence = _legacy_entry_fills(db, row)
    result["evidence"] = evidence
    if fills is None:
        result["reason"] = error
        return result
    exit_price = _finite(row.get("current_price") if row.get("current_price") is not None else row.get("exit_price"))
    quantity, entry_price = _finite(row.get("quantity")), _finite(row.get("entry_price"))
    entry_fee, exit_fee = _finite(row.get("entry_fee")), _finite(row.get("exit_fee"))
    if any(value is None for value in (exit_price, quantity, entry_price, entry_fee, exit_fee)) or exit_price <= 0 or quantity <= 0 or entry_price <= 0 or entry_fee < 0 or exit_fee < 0:
        result["reason"] = "incomplete_close_economics"
        return result
    direction = str(row.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        gross = (exit_price - entry_price) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry_price - exit_price) * quantity
    else:
        result["reason"] = "invalid_direction"
        return result
    market_type = str(row.get("market_type") or "").lower()
    funding_cost = 0.0
    if market_type == "usdt_futures":
        funding = compute_local_funding_from_entry_fills(row, closed_at=str(row.get("closed_at") or ""), entry_fills=fills)
        result["evidence"]["funding"] = funding.evidence
        if not funding.complete or funding.funding_cost is None:
            result["reason"] = str(funding.evidence.get("reason") or "incomplete_funding_economics")
            return result
        funding_cost = funding.funding_cost
    elif market_type != "spot":
        result["reason"] = "unsupported_market_type"
        return result
    reconstructed = gross - entry_fee - exit_fee - funding_cost
    stored = _finite(row.get("realized_pnl"))
    result.update({
        "status": "reconstructed",
        "reason": None,
        "funding_cost": funding_cost,
        "reconstructed_realized_pnl": round(reconstructed, 8),
    })
    result["evidence"].update({
        "source": "historical_local_paper_reconstruction",
        "stored_realized_pnl": stored,
        "stored_pnl_delta": None if stored is None else round(reconstructed - stored, 8),
        "excluded_from_verified_outcomes": True,
        "excluded_from_paper_cash_settlement": True,
    })
    return result


def _audit_id(kind: str, position_id: str) -> str:
    return "haa-" + hashlib.sha256(f"{RECONCILIATION_VERSION}:{kind}:{position_id}".encode()).hexdigest()[:24]


def reconcile_historical_local_paper_accounting(*, dry_run: bool = True) -> dict[str, Any]:
    """Plan or apply deterministic local-paper historical accounting recovery."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    owns_transaction = False
    was_in_tx = db.in_transaction
    if not was_in_tx:
        db.execute("BEGIN" if dry_run else "BEGIN IMMEDIATE")
        owns_transaction = True
    try:
        rows = _closed_rows(db)
        # Open rows only need the exact reverse-link repair, not economics.
        open_rows = [dict(row) for row in db.execute(
            "SELECT p.*, o.id AS entry_order_id, o.position_id AS entry_order_position_id, o.proposal_id AS entry_order_proposal_id, o.symbol AS entry_order_symbol, o.market_type AS entry_order_market_type, o.direction AS entry_order_direction, o.execution_mode AS entry_order_execution_mode, o.status AS entry_order_status, o.source AS entry_order_source, o.close_reason AS entry_order_close_reason FROM positions p LEFT JOIN orders o ON o.id=p.order_id WHERE p.execution_mode='paper_internal' AND p.source='native' AND p.status!='closed' ORDER BY p.rowid ASC"
        ).fetchall()]
        links = [_link_plan(row) for row in [*rows, *open_rows]]
        reconciliations = [_economic_result(db, row) for row in rows]
        result: dict[str, Any] = {
            "version": RECONCILIATION_VERSION,
            "dry_run": dry_run,
            "scope": "native paper_internal positions only",
            "links": links,
            "reconciliations": reconciliations,
            "summary": {
                "link_repairs_planned": sum(item["action"] == "repair" for item in links),
                "reconstructed": sum(item["status"] == "reconstructed" for item in reconciliations),
                "unavailable": sum(item["status"] == "unavailable" for item in reconciliations),
            },
        }
        if dry_run:
            if owns_transaction:
                db.rollback()
            return result
        now = datetime.now(timezone.utc).isoformat()
        applied_links = 0
        for item in links:
            if item["action"] != "repair":
                continue
            cursor = db.execute("UPDATE orders SET position_id=? WHERE id=? AND position_id IS NULL", (item["position_id"], item["order_id"]))
            if cursor.rowcount:
                applied_links += 1
                db.execute("INSERT OR IGNORE INTO audit_events (id,timestamp,actor,source,event_type,entity_type,entity_id,before_state,after_state,message) VALUES (?,?,?,?,?,?,?,?,?,?)", (
                    _audit_id("link", item["position_id"]), now, "historical_accounting_repair", "maintenance", "repair_order_position_backlink", "position", item["position_id"], json.dumps({"order_position_id": None}), json.dumps({"order_position_id": item["position_id"]}), "Recovered exact missing reverse entry-order backlink.",
                ))
        applied_reconciliations = 0
        for item in reconciliations:
            serialized = json.dumps(item["evidence"], sort_keys=True, separators=(",", ":"))
            cursor = db.execute(
                """INSERT INTO historical_accounting_reconciliations
                   (position_id,reconciliation_version,status,reconstructed_realized_pnl,funding_cost,method,reason,evidence_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(position_id) DO UPDATE SET
                     reconciliation_version=excluded.reconciliation_version,status=excluded.status,
                     reconstructed_realized_pnl=excluded.reconstructed_realized_pnl,funding_cost=excluded.funding_cost,
                     method=excluded.method,reason=excluded.reason,evidence_json=excluded.evidence_json,updated_at=excluded.updated_at
                   WHERE historical_accounting_reconciliations.reconciliation_version IS NOT excluded.reconciliation_version
                      OR historical_accounting_reconciliations.status IS NOT excluded.status
                      OR historical_accounting_reconciliations.reconstructed_realized_pnl IS NOT excluded.reconstructed_realized_pnl
                      OR historical_accounting_reconciliations.funding_cost IS NOT excluded.funding_cost
                      OR historical_accounting_reconciliations.reason IS NOT excluded.reason
                      OR historical_accounting_reconciliations.evidence_json IS NOT excluded.evidence_json""",
                (item["position_id"], RECONCILIATION_VERSION, item["status"], item["reconstructed_realized_pnl"], item["funding_cost"], item["method"], item["reason"], serialized, now, now),
            )
            if cursor.rowcount:
                applied_reconciliations += 1
        result["summary"].update({"link_repairs_applied": applied_links, "reconciliations_applied": applied_reconciliations})
        if owns_transaction:
            db.commit()
        return result
    except Exception:
        if owns_transaction:
            db.rollback()
        raise


def historical_accounting_summary(*, execution_mode: str | None = None) -> dict[str, Any]:
    """Read-only reconciliation totals, deliberately separate from verified PnL."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    params: tuple[Any, ...] = ()
    mode_filter = ""
    if execution_mode:
        mode_filter = " AND p.execution_mode=?"
        params = (execution_mode,)
    rows = db.execute(
        "SELECT r.status,r.reconstructed_realized_pnl FROM historical_accounting_reconciliations r JOIN positions p ON p.id=r.position_id WHERE 1=1" + mode_filter,
        params,
    ).fetchall()
    reconstructed = [float(row["reconstructed_realized_pnl"]) for row in rows if row["status"] == "reconstructed" and row["reconstructed_realized_pnl"] is not None]
    return {
        "historical_reconciled_pnl": round(sum(reconstructed), 8) if reconstructed else None,
        "historical_reconciled_count": len(reconstructed),
        "historical_reconciliation_unavailable_count": sum(row["status"] == "unavailable" for row in rows),
    }


def historical_accounting_for_position(position_id: str) -> dict[str, Any] | None:
    from aletheia.storage.sqlite import get_db

    row = get_db().execute("SELECT * FROM historical_accounting_reconciliations WHERE position_id=?", (position_id,)).fetchone()
    if row is None:
        return None
    result = dict(row)
    try:
        result["evidence"] = json.loads(result.pop("evidence_json"))
    except json.JSONDecodeError:
        result["evidence"] = {"status": "unreadable_evidence"}
    return result
