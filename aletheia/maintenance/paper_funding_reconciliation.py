"""Dry-run-first reconciliation for current paper funding economics."""

from __future__ import annotations

import hashlib
import json
import logging
import math
from typing import Any, Sequence

from aletheia.storage import repositories as repo


logger = logging.getLogger(__name__)


COMMAND_VERSION = "paper-funding-reconciliation-v2"
ACCOUNTING_VERSION = "local-paper-funding-v2"


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _position_rows(
    position_ids: Sequence[str] | None,
    *,
    limit: int | None,
) -> list[dict]:
    from aletheia.portfolio.paper_account import INVALID_EXIT_PRICE_EVIDENCE
    from aletheia.storage.sqlite import get_db

    clauses = [
        "status='closed'",
        "execution_mode='paper_internal'",
        "market_type='usdt_futures'",
        "(outcome_exclusion_reason IS NULL OR outcome_exclusion_reason != ?)",
    ]
    params: list[Any] = [INVALID_EXIT_PRICE_EVIDENCE]
    normalized = sorted(set(str(item) for item in position_ids or [] if str(item)))
    if normalized:
        clauses.append("id IN (" + ",".join("?" for _ in normalized) + ")")
        params.extend(normalized)
    sql = "SELECT * FROM positions WHERE " + " AND ".join(clauses) + " ORDER BY id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, int(limit)))
    rows = [dict(row) for row in get_db().execute(sql, params).fetchall()]
    if normalized:
        found = {str(row["id"]) for row in rows}
        missing = sorted(set(normalized) - found)
        if missing:
            raise ValueError(f"unknown or out-of-scope position ids: {', '.join(missing)}")
    return rows


def _causal_ids(position_id: str) -> dict[str, list[str] | str | None]:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    orders = [dict(row) for row in db.execute(
        "SELECT id,proposal_id,close_reason FROM orders WHERE position_id=? ORDER BY id",
        (position_id,),
    ).fetchall()]
    order_ids = [str(row["id"]) for row in orders]
    fills: list[str] = []
    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        fills = [str(row["id"]) for row in db.execute(
            f"SELECT id FROM fills WHERE order_id IN ({placeholders}) ORDER BY id",
            order_ids,
        ).fetchall()]
    close_events = [str(row["id"]) for row in db.execute(
        "SELECT id FROM position_close_events WHERE position_id=? ORDER BY id",
        (position_id,),
    ).fetchall()]
    proposal_ids = sorted({
        str(row["proposal_id"]) for row in orders if row.get("proposal_id")
    })
    return {
        "proposal_ids": proposal_ids,
        "entry_order_ids": [
            str(row["id"]) for row in orders if not row.get("close_reason")
        ],
        "close_order_ids": [
            str(row["id"]) for row in orders if row.get("close_reason")
        ],
        "fill_ids": fills,
        "close_event_ids": close_events,
    }


def _cash_total(position_id: str) -> tuple[float | None, list[dict]]:
    from aletheia.storage.sqlite import get_db

    rows = [dict(row) for row in get_db().execute(
        "SELECT id,action,amount_usdt,source,note FROM paper_account_events "
        "WHERE position_id=? AND action IN "
        "('position_closed','manual_credit','manual_debit') ORDER BY rowid",
        (position_id,),
    ).fetchall()]
    if not rows:
        return None, []
    return round(sum(float(row["amount_usdt"]) for row in rows), 8), rows


def _close_event_values(position_id: str) -> list[dict]:
    from aletheia.storage.sqlite import get_db

    return [dict(row) for row in get_db().execute(
        "SELECT id,realized_pnl,is_partial FROM position_close_events "
        "WHERE position_id=? ORDER BY id",
        (position_id,),
    ).fetchall()]


def _project_result(
    row: dict,
    *,
    evidence: dict,
    funding_cost: float,
) -> tuple[dict, float, float | None, bool, str]:
    from aletheia.analytics.outcome_integrity import outcome_write_eligibility
    direction = str(row.get("direction") or "").lower()
    quantity = float(row["quantity"])
    entry = float(row["entry_price"])
    exit_price = float(row["current_price"])
    if direction in {"long", "buy"}:
        gross = (exit_price - entry) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry - exit_price) * quantity
    else:
        return evidence, funding_cost, None, False, "invalid_direction"
    realized = round(
        gross
        - float(row["entry_fee"])
        - float(row["exit_fee"])
        - funding_cost,
        8,
    )
    check = {
        **row,
        "realized_pnl": realized,
        "funding_cost": funding_cost,
        "funding_source": "local_simulation",
        "exit_fill_verified": True,
    }
    eligible, reason = outcome_write_eligibility(check, exit_fill_verified=True)
    return evidence, funding_cost, realized, eligible, reason


def _proposed_result(row: dict) -> tuple[dict, float | None, float | None, bool, str]:
    from aletheia.portfolio.local_funding import compute_local_funding

    funding = compute_local_funding(
        row,
        closed_at=row["closed_at"],
        allow_historical_ledger=True,
    )
    if not funding.complete or funding.funding_cost is None:
        return funding.evidence, None, None, False, str(
            funding.evidence.get("reason") or "incomplete_funding_economics"
        )
    return _project_result(
        row,
        evidence=funding.evidence,
        funding_cost=float(funding.funding_cost),
    )


def _stored_funding_identity(row: dict) -> dict[str, Any]:
    """Parse and validate the exact funding evidence persisted by the close."""
    evidence_root: dict[str, Any] = {}
    raw_evidence = row.get("reconciliation_evidence")
    if raw_evidence:
        try:
            parsed = json.loads(str(raw_evidence))
            if isinstance(parsed, dict):
                evidence_root = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    funding_evidence = evidence_root.get("funding")
    nested_fingerprint = (
        _fingerprint(funding_evidence)
        if isinstance(funding_evidence, dict)
        else None
    )
    claimed_complete = (
        isinstance(funding_evidence, dict)
        and funding_evidence.get("status") == "complete"
        and funding_evidence.get("accounting_version") == ACCOUNTING_VERSION
    )
    validated = False
    if isinstance(funding_evidence, dict) and claimed_complete:
        from aletheia.portfolio.pnl import _validate_complete_funding_evidence

        try:
            _validate_complete_funding_evidence(
                row=row,
                position_id=str(row["id"]),
                closed_at=str(row.get("closed_at") or ""),
                funding_cost=row.get("funding_cost"),
                funding_source=row.get("funding_source"),
                evidence=funding_evidence,
            )
        except (TypeError, ValueError):
            pass
        else:
            validated = True
    return {
        "evidence_root": evidence_root,
        "funding_evidence": funding_evidence,
        "nested_fingerprint": nested_fingerprint,
        "root_accounting_version": evidence_root.get(
            "funding_accounting_version",
        ),
        "root_fingerprint": evidence_root.get(
            "funding_economics_fingerprint",
        ),
        "claimed_complete": claimed_complete,
        "validated": validated,
    }


def _plan_row(row: dict) -> dict:
    position_id = str(row["id"])
    recomputed = _proposed_result(row)
    recomputed_evidence = recomputed[0]
    recomputed_fingerprint = (
        _fingerprint(recomputed_evidence)
        if isinstance(recomputed_evidence, dict)
        and recomputed_evidence.get("status") == "complete"
        else None
    )
    stored_identity = _stored_funding_identity(row)
    if stored_identity["validated"] and row.get("funding_cost") is not None:
        evidence, funding_cost, proposed_pnl, eligible, reason = _project_result(
            row,
            evidence=stored_identity["funding_evidence"],
            funding_cost=float(row["funding_cost"]),
        )
    else:
        evidence, funding_cost, proposed_pnl, eligible, reason = recomputed
    close_events = _close_event_values(position_id)
    cash_total, cash_events = _cash_total(position_id)
    hooks = repo.list_outcome_hook_jobs(position_id=position_id, limit=20)
    current_pnl = row.get("realized_pnl")
    categories: list[str] = []

    if proposed_pnl is not None and (
        not stored_identity["claimed_complete"]
        or not stored_identity["validated"]
        or stored_identity["root_accounting_version"] != ACCOUNTING_VERSION
        or stored_identity["root_fingerprint"]
        != stored_identity["nested_fingerprint"]
    ):
        categories.append("funding_identity_mismatch")

    if proposed_pnl is None:
        categories.append("still_incomplete")
    elif current_pnl is None:
        categories.append(
            "terminal_now_recoverable"
            if row.get("outcome_exclusion_reason") == "funding_permanently_unrecoverable"
            else "recoverable_null_pnl"
        )
    elif (
        funding_cost is None
        or row.get("funding_cost") is None
        or not math.isclose(
            float(row["funding_cost"]), funding_cost,
            rel_tol=0.0, abs_tol=1e-12,
        )
        or not math.isclose(
            float(current_pnl), proposed_pnl,
            rel_tol=0.0, abs_tol=1e-8,
        )
    ):
        categories.append("duplicate_settlement_numeric_mismatch")

    full_close_values = [
        event.get("realized_pnl") for event in close_events
        if not bool(event.get("is_partial"))
    ]
    if proposed_pnl is not None and (
        len(full_close_values) != 1
        or full_close_values[0] is None
        or not math.isclose(
            float(full_close_values[0]), proposed_pnl,
            rel_tol=0.0, abs_tol=1e-8,
        )
    ):
        categories.append("close_event_projection_mismatch")
    if current_pnl is not None and (
        cash_total is None
        or not math.isclose(float(current_pnl), cash_total, rel_tol=0.0, abs_tol=1e-8)
    ):
        categories.append("paper_cash_ledger_mismatch")
    if proposed_pnl is not None and (
        bool(row.get("outcome_eligible")) != bool(eligible)
        or row.get("outcome_exclusion_reason") != reason
    ):
        categories.append("outcome_projection_mismatch")
    expected_hooks: set[str] = set()
    if eligible:
        expected_hooks.add("shadow_trades")
        if row.get("trade_school") == "serenity" and row.get("opportunity_id"):
            expected_hooks.add("serenity_post_mortem")
    actual_hooks = {str(item["hook_type"]) for item in hooks}
    if not expected_hooks.issubset(actual_hooks):
        categories.append("missing_outcome_hooks")

    category_set = set(categories)
    if not category_set:
        repair_mode = "already_current"
    elif (
        category_set == {"funding_identity_mismatch"}
        and stored_identity["validated"]
    ):
        repair_mode = "identity_only"
    else:
        repair_mode = "economics_materialization"
    preserve_stored_evidence = repair_mode in {
        "already_current",
        "identity_only",
    } and stored_identity["validated"]
    proposed_evidence = (
        stored_identity["funding_evidence"]
        if preserve_stored_evidence
        else evidence
    )
    proposed_fingerprint = (
        _fingerprint(proposed_evidence)
        if isinstance(proposed_evidence, dict)
        and proposed_evidence.get("status") == "complete"
        else None
    )

    proposed = {
        "funding_accounting_version": ACCOUNTING_VERSION,
        "funding_economics_fingerprint": proposed_fingerprint,
        "recomputed_funding_economics_fingerprint": recomputed_fingerprint,
        "funding_cost": funding_cost,
        "realized_pnl": proposed_pnl,
        "outcome_eligible": bool(eligible),
        "outcome_exclusion_reason": reason,
        "funding_evidence": proposed_evidence,
        "required_cash_compensation": (
            None if proposed_pnl is None or cash_total is None
            else round(proposed_pnl - cash_total, 8)
        ),
    }
    current = {
        "funding_accounting_version": stored_identity[
            "root_accounting_version"
        ],
        "funding_economics_fingerprint": stored_identity[
            "root_fingerprint"
        ],
        "nested_funding_economics_fingerprint": stored_identity[
            "nested_fingerprint"
        ],
        "funding_cost": row.get("funding_cost"),
        "realized_pnl": current_pnl,
        "outcome_eligible": bool(row.get("outcome_eligible")),
        "outcome_exclusion_reason": row.get("outcome_exclusion_reason"),
        "close_events": close_events,
        "cash_events": cash_events,
        "outcome_hooks": [
            {"hook_type": item["hook_type"], "status": item["status"]}
            for item in hooks
        ],
    }
    guarded = {
        "command_version": COMMAND_VERSION,
        "accounting_version": ACCOUNTING_VERSION,
        "position_id": position_id,
        "causal_ids": _causal_ids(position_id),
        "repair_mode": repair_mode,
        "current": current,
        "proposed": proposed,
    }
    return {
        "position_id": position_id,
        "symbol": row.get("symbol"),
        "categories": sorted(set(categories)),
        "repair_mode": repair_mode,
        "causal_ids": guarded["causal_ids"],
        "current": current,
        "proposed": proposed,
        "repair_fingerprint": _fingerprint(guarded),
    }


def inspect_paper_funding_reconciliation(
    *,
    position_ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    rows = [_plan_row(row) for row in _position_rows(position_ids, limit=limit)]
    category_names = (
        "funding_identity_mismatch",
        "recoverable_null_pnl",
        "terminal_now_recoverable",
        "duplicate_settlement_numeric_mismatch",
        "close_event_projection_mismatch",
        "paper_cash_ledger_mismatch",
        "outcome_projection_mismatch",
        "missing_outcome_hooks",
        "still_incomplete",
    )
    summary = {
        name: sum(name in row["categories"] for row in rows)
        for name in category_names
    }
    scope = {
        "execution_mode": "paper_internal",
        "market_type": "usdt_futures",
        "position_ids": sorted(str(item) for item in position_ids or []),
    }
    report = {
        "command_version": COMMAND_VERSION,
        "accounting_version": ACCOUNTING_VERSION,
        "scope": scope,
        "summary": summary,
        "rows": rows,
    }
    report["report_fingerprint"] = _fingerprint(report)
    return report


def _apply_funding_identity_repair(item: dict[str, Any]) -> dict[str, Any]:
    """Add root identity without touching close-time evidence or projections."""
    from aletheia.files.jsonl import write_audit
    from aletheia.storage.models import AuditEvent

    position_id = str(item["position_id"])
    audit_payload: dict[str, Any] | None = None
    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        fresh = _plan_row(current)
        if fresh["repair_fingerprint"] != item["repair_fingerprint"]:
            raise ValueError(
                f"position {position_id} funding identity facts changed"
            )
        if fresh["repair_mode"] != "identity_only":
            raise ValueError(
                f"position {position_id} is not an identity-only repair"
            )

        raw_evidence = current.get("reconciliation_evidence")
        try:
            evidence_root = json.loads(str(raw_evidence))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stored reconciliation evidence is invalid") from exc
        if not isinstance(evidence_root, dict):
            raise ValueError("stored reconciliation evidence is invalid")
        stored_funding = evidence_root.get("funding")
        proposed = fresh["proposed"]
        if stored_funding != proposed["funding_evidence"]:
            raise ValueError("stored funding evidence changed before identity repair")
        expected_fingerprint = proposed["funding_economics_fingerprint"]
        if (
            not isinstance(expected_fingerprint, str)
            or _fingerprint(stored_funding) != expected_fingerprint
        ):
            raise ValueError("stored funding evidence fingerprint does not match")

        before = {
            "funding_accounting_version": evidence_root.get(
                "funding_accounting_version",
            ),
            "funding_economics_fingerprint": evidence_root.get(
                "funding_economics_fingerprint",
            ),
        }
        evidence_root["funding_accounting_version"] = ACCOUNTING_VERSION
        evidence_root["funding_economics_fingerprint"] = expected_fingerprint
        repo.update_position(
            position_id,
            reconciliation_evidence=json.dumps(
                evidence_root,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        )
        after = {
            "funding_accounting_version": ACCOUNTING_VERSION,
            "funding_economics_fingerprint": expected_fingerprint,
        }
        repo.insert_audit_event(AuditEvent(
            actor="human",
            source="paper_funding_reconciliation",
            event_type="paper_position_funding_identity_repaired",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Repaired paper funding evidence identity for {position_id}; "
                "economic and ledger projections unchanged"
            ),
        ))
        audit_payload = {
            "actor": "human",
            "source": "paper_funding_reconciliation",
            "event_type": "paper_position_funding_identity_repaired",
            "entity_type": "position",
            "entity_id": position_id,
            "before": before,
            "after": after,
            "message": f"Repaired paper funding evidence identity for {position_id}",
        }

    if audit_payload is not None:
        try:
            write_audit(audit_payload, mirror_db=False)
        except Exception as exc:
            logger.error(
                "paper funding identity JSONL audit sink failed (%s)",
                type(exc).__name__,
            )
    return {
        "position_id": position_id,
        "status": "identity_repaired",
        "realized_pnl": item["current"]["realized_pnl"],
        "funding_cost": item["current"]["funding_cost"],
        "fingerprint": item["proposed"]["funding_economics_fingerprint"],
        "cash_compensation": 0.0,
    }


def apply_paper_funding_reconciliation(
    *,
    position_ids: Sequence[str],
    expected_fingerprint: str,
) -> dict[str, Any]:
    from aletheia.portfolio.paper_account import materialize_position_economics

    normalized = sorted(set(str(item) for item in position_ids if str(item)))
    if not normalized:
        raise ValueError("apply requires at least one position id")
    report = inspect_paper_funding_reconciliation(position_ids=normalized)
    if report["report_fingerprint"] != expected_fingerprint:
        raise ValueError("paper funding reconciliation fingerprint is stale")
    results: list[dict] = []
    for item in report["rows"]:
        proposed = item["proposed"]
        if item["repair_mode"] == "already_current":
            results.append({
                "position_id": item["position_id"],
                "status": "already_current",
                "realized_pnl": item["current"]["realized_pnl"],
                "funding_cost": item["current"]["funding_cost"],
                "fingerprint": proposed["funding_economics_fingerprint"],
                "cash_compensation": 0.0,
            })
            continue
        if item["repair_mode"] == "identity_only":
            results.append(_apply_funding_identity_repair(item))
            continue
        if proposed["realized_pnl"] is None or proposed["funding_cost"] is None:
            raise ValueError(f"position {item['position_id']} remains incomplete")
        results.append(materialize_position_economics(
            item["position_id"],
            realized_pnl=float(proposed["realized_pnl"]),
            funding_cost=float(proposed["funding_cost"]),
            funding_evidence=proposed["funding_evidence"],
            outcome_eligible=bool(proposed["outcome_eligible"]),
            outcome_exclusion_reason=str(proposed["outcome_exclusion_reason"]),
            actor="human",
            source="paper_funding_reconciliation",
            allow_correction=item["current"]["realized_pnl"] is not None,
        ))
    return {
        "applied": True,
        "report_fingerprint": expected_fingerprint,
        "results": results,
    }


def paper_funding_diagnostic_summary(*, limit: int = 100) -> dict[str, Any]:
    bounded_limit = max(1, int(limit))
    report = inspect_paper_funding_reconciliation(limit=bounded_limit + 1)
    rows = report["rows"][:bounded_limit]
    summary = {
        name: sum(name in row["categories"] for row in rows)
        for name in report["summary"]
    }
    return {
        "accounting_version": report["accounting_version"],
        "summary": summary,
        "scanned": len(rows),
        "truncated": len(report["rows"]) > bounded_limit,
    }
