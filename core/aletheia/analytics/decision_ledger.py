"""Decision-quality ledger: each AI entry decision, its stated reason, its net outcome.

Read-only. One row per Planner decision (a ``candidate_planning_generations`` row
with a validated hypothesis). The reason is the validated ``EntryHypothesis``;
downstream state comes from ``get_entry_plan_execution_lineage``; the cost-adjusted
outcome comes only from ``assess_reportable_outcome`` on the Position row. Unknown
economics stay ``None`` — never zero.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from statistics import fmean

from aletheia.analytics.outcome_integrity import (
    assess_reportable_outcome,
    validated_funding_economics,
)
from aletheia.storage.repositories import (
    get_db,
    get_entry_plan_execution_lineage,
    transaction,
)

_HYPOTHESIS_FIELDS = (
    "decision", "reason_code", "mechanism", "direction",
    "capture_reason", "risk_tier", "capture_priority",
)


def build_decision_ledger(*, days: int = 7, now: datetime | None = None) -> dict:
    """Return ``{"rows": [...], "summary": {...}}`` for decisions created in the window."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    since = (current - timedelta(days=days)).isoformat()
    with transaction():
        db = get_db()
        generations = [dict(row) for row in db.execute(
            "SELECT g.id, g.created_at, g.symbol, g.status, g.outcome_class, g.outcome_reason, "
            "g.entry_plan_id, g.validated_hypothesis_json, "
            "ep.status AS plan_status, ep.terminal_reason AS plan_terminal_reason "
            "FROM candidate_planning_generations g "
            "LEFT JOIN entry_plans ep ON ep.id = g.entry_plan_id "
            "WHERE g.validated_hypothesis_json IS NOT NULL AND g.created_at >= ? "
            "ORDER BY g.created_at, g.id",
            (since,),
        )]
        lineage = get_entry_plan_execution_lineage(
            [g["entry_plan_id"] for g in generations if g["entry_plan_id"]]
        )
        position_ids = [
            item["position"]["id"] for item in lineage.values() if item.get("position")
        ]
        positions = {row["id"]: dict(row) for row in db.execute(
            "SELECT * FROM positions WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(position_ids),),
        )}
        rows = [_row(g, lineage, positions) for g in generations]
    return {"since": since, "days": days, "rows": rows, "summary": _summary(rows)}


def _row(generation: dict, lineage: dict, positions: dict) -> dict:
    hypothesis = json.loads(generation["validated_hypothesis_json"])
    row = {
        "generation_id": generation["id"],
        "created_at": generation["created_at"],
        "symbol": generation["symbol"],
        **{key: hypothesis.get(key) for key in _HYPOTHESIS_FIELDS},
        "entry_plan_id": generation["entry_plan_id"],
        "position_id": None,
        "outcome_eligible": False,
        "outcome_reason": None,
        "net_return_pct": None,
        "realized_pnl": None,
        "entry_fee": None,
        "exit_fee": None,
        "funding_cost": None,
    }
    plan_id = generation["entry_plan_id"]
    if not plan_id:
        row["state"] = generation["outcome_class"] or generation["status"]
        row["state_reason"] = generation["outcome_reason"]
        return row
    execution = lineage[plan_id]
    state = execution["execution_state"].lower()
    if state == "not_started":
        row["state"] = f"plan_{generation['plan_status']}"
        row["state_reason"] = generation["plan_terminal_reason"]
    else:
        row["state"] = state
        orders = execution["orders"]
        row["state_reason"] = (
            execution["availability"]["reason"] or execution["economics"]["reason"]
            or (f"order_status={orders[-1]['status']}" if orders else None)
        )
    position = positions.get((execution.get("position") or {}).get("id"))
    if position is None:
        return row
    assessment = assess_reportable_outcome(position)
    funding_cost, _source = validated_funding_economics(
        market_type=position.get("market_type"),
        execution_mode=position.get("execution_mode"),
        funding_cost=position.get("funding_cost"),
        funding_source=position.get("funding_source"),
    )
    row.update(
        position_id=position["id"],
        outcome_eligible=assessment.eligible,
        outcome_reason=assessment.reason,
        net_return_pct=assessment.net_return_pct if assessment.eligible else None,
        realized_pnl=position.get("realized_pnl") if assessment.eligible else None,
        entry_fee=position.get("entry_fee"),
        exit_fee=position.get("exit_fee"),
        funding_cost=funding_cost,
    )
    return row


def _outcome_stats(rows: list[dict]) -> dict:
    returns = [row["net_return_pct"] for row in rows if row["outcome_eligible"]]
    return {
        "eligible": len(returns),
        "win_rate": sum(r > 0 for r in returns) / len(returns) if returns else None,
        "mean_net_return_pct": fmean(returns) if returns else None,
        "sum_net_return_pct": sum(returns) if returns else None,
    }


def _summary(rows: list[dict]) -> dict:
    states: dict[str, int] = {}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        states[row["state"]] = states.get(row["state"], 0) + 1
        groups.setdefault(f"{row['mechanism']}/{row['direction']}", []).append(row)
    return {
        "decisions": len(rows),
        "states": dict(sorted(states.items(), key=lambda kv: -kv[1])),
        "outcomes": _outcome_stats(rows),
        "by_mechanism_direction": {
            key: {"decisions": len(group), **_outcome_stats(group)}
            for key, group in sorted(groups.items())
        },
    }
