"""Dry-run-first reconciliation for current paper funding economics."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Sequence

from aletheia.storage import repositories as repo


COMMAND_VERSION = "paper-funding-reconciliation-v1"
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
    from aletheia.storage.sqlite import get_db

    clauses = [
        "status='closed'",
        "execution_mode='paper_internal'",
        "market_type='usdt_futures'",
    ]
    params: list[Any] = []
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


def _proposed_result(row: dict) -> tuple[dict, float | None, float | None, bool, str]:
    from aletheia.analytics.outcome_integrity import outcome_write_eligibility
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
    direction = str(row.get("direction") or "").lower()
    quantity = float(row["quantity"])
    entry = float(row["entry_price"])
    exit_price = float(row["current_price"])
    if direction in {"long", "buy"}:
        gross = (exit_price - entry) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry - exit_price) * quantity
    else:
        return funding.evidence, None, None, False, "invalid_direction"
    realized = round(
        gross
        - float(row["entry_fee"])
        - float(row["exit_fee"])
        - float(funding.funding_cost),
        8,
    )
    check = {
        **row,
        "realized_pnl": realized,
        "funding_cost": funding.funding_cost,
        "funding_source": "local_simulation",
        "exit_fill_verified": True,
    }
    eligible, reason = outcome_write_eligibility(check, exit_fill_verified=True)
    return funding.evidence, float(funding.funding_cost), realized, eligible, reason


def _plan_row(row: dict) -> dict:
    position_id = str(row["id"])
    evidence, funding_cost, proposed_pnl, eligible, reason = _proposed_result(row)
    close_events = _close_event_values(position_id)
    cash_total, cash_events = _cash_total(position_id)
    hooks = repo.list_outcome_hook_jobs(position_id=position_id, limit=20)
    current_pnl = row.get("realized_pnl")
    current_accounting_version: str | None = None
    if row.get("reconciliation_evidence"):
        try:
            parsed_evidence = json.loads(str(row["reconciliation_evidence"]))
            if isinstance(parsed_evidence, dict):
                current_accounting_version = parsed_evidence.get(
                    "funding_accounting_version",
                )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    categories: list[str] = []

    if proposed_pnl is None:
        categories.append("still_incomplete")
    elif current_pnl is None:
        categories.append(
            "terminal_now_recoverable"
            if row.get("outcome_exclusion_reason") == "funding_permanently_unrecoverable"
            else "recoverable_null_pnl"
        )
    elif not math.isclose(
        float(current_pnl), proposed_pnl, rel_tol=0.0, abs_tol=1e-8,
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
    if bool(row.get("outcome_eligible")) and not hooks:
        categories.append("missing_outcome_hooks")

    proposed = {
        "funding_accounting_version": ACCOUNTING_VERSION,
        "funding_cost": funding_cost,
        "realized_pnl": proposed_pnl,
        "outcome_eligible": bool(eligible),
        "outcome_exclusion_reason": reason,
        "funding_evidence": evidence,
        "required_cash_compensation": (
            None if proposed_pnl is None or cash_total is None
            else round(proposed_pnl - cash_total, 8)
        ),
    }
    current = {
        "funding_accounting_version": current_accounting_version,
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
        "current": current,
        "proposed": proposed,
    }
    return {
        "position_id": position_id,
        "symbol": row.get("symbol"),
        "categories": sorted(set(categories)),
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
        "recoverable_null_pnl",
        "terminal_now_recoverable",
        "duplicate_settlement_numeric_mismatch",
        "close_event_projection_mismatch",
        "paper_cash_ledger_mismatch",
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
