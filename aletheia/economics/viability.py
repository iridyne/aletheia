"""Deterministic System and Operator economic viability audits.

LLMs never decide accounting numbers. Trading outcomes come from the existing
Outcome Integrity contract; runtime costs come from append-only cost events.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from aletheia.economics.schema import ECONOMICS_SCHEMA_VERSION, ensure_economics_schema
from aletheia.economics.tracing import list_cost_summary

METRIC_VERSION = "aletheia-viability-v1"
_NEUTRAL_EPSILON_USD = 1e-9
_OPERATOR_AGENT_NAMES = ("pi_trading_decision", "system_operator")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def audit_enabled() -> bool:
    return os.getenv("ALETHEIA_VIABILITY_AUDIT", "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _resolve_window_end(value: datetime | str | None) -> datetime:
    if value is None:
        return _utcnow()
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _verdict(value: float) -> str:
    if value > _NEUTRAL_EPSILON_USD:
        return "positive"
    if value < -_NEUTRAL_EPSILON_USD:
        return "negative"
    return "neutral"


def _fixed_costs_complete(*values: float | None) -> bool:
    return all(
        value is not None
        and math.isfinite(float(value))
        and float(value) >= 0
        for value in values
    )


def _persist(report: dict[str, Any]) -> dict[str, Any]:
    db = ensure_economics_schema()
    payload = {
        "scope": report["scope"],
        "window_start": report["window_start"],
        "window_end": report["window_end"],
        "execution_mode": report["execution_mode"],
        "metric_version": report["metric_version"],
        "outcome_contract_version": report.get("outcome_contract_version"),
        "net_trading_result_usd": report.get("net_trading_result_usd"),
        "baseline_result_usd": report.get("baseline_result_usd"),
        "incremental_value_usd": report.get("incremental_value_usd"),
        "intelligence_cost_usd": report.get("intelligence_cost_usd"),
        "tool_cost_usd": report.get("tool_cost_usd"),
        "data_cost_usd": report.get("data_cost_usd"),
        "infrastructure_cost_usd": report.get("infrastructure_cost_usd"),
        "other_cost_usd": report.get("other_cost_usd"),
        "deployment_cost_usd": report.get("deployment_cost_usd"),
        "viability_amount_usd": report.get("viability_amount_usd"),
        "verdict": report["verdict"],
        "cost_completeness": report["cost_completeness"],
        "sample_count": report["sample_count"],
        "exclusion_counts_json": _json(report.get("exclusion_counts", {})),
        "assumptions_json": _json(report.get("assumptions", [])),
        "reason": report["reason"],
        "generated_at": report["generated_at"],
    }
    dedup_key = hashlib.sha256(_json(payload).encode()).hexdigest()
    report_id = "viability_" + dedup_key[:16]
    db.execute(
        """INSERT OR IGNORE INTO viability_reports (
            id,dedup_key,scope,window_start,window_end,execution_mode,metric_version,
            outcome_contract_version,net_trading_result_usd,baseline_result_usd,
            incremental_value_usd,intelligence_cost_usd,tool_cost_usd,data_cost_usd,
            infrastructure_cost_usd,other_cost_usd,deployment_cost_usd,
            viability_amount_usd,verdict,cost_completeness,sample_count,
            exclusion_counts_json,assumptions_json,reason,generated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (report_id, dedup_key, *payload.values()),
    )
    return {**report, "report_id": report_id, "dedup_key": dedup_key}


def _known_cost(summary: dict[str, Any], event_type: str) -> float:
    return float(
        summary.get("by_type", {}).get(event_type, {}).get("known_cost_usd") or 0.0
    )


def build_system_viability(
    *,
    days: int = 7,
    execution_mode: str = "paper_internal",
    data_cost_usd: float | None = None,
    infrastructure_cost_usd: float | None = None,
    other_cost_usd: float | None = None,
    window_end: datetime | str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if not audit_enabled():
        raise RuntimeError("viability audit is disabled by ALETHEIA_VIABILITY_AUDIT")
    if days <= 0:
        raise ValueError("days must be positive")

    from aletheia.analytics.outcome_integrity import (
        OUTCOME_SEMANTICS_VERSION,
        eligible_strategy_outcomes,
    )

    end = _resolve_window_end(window_end)
    start = end - timedelta(days=days)
    start_iso, end_iso = _iso(start), _iso(end)
    outcomes = eligible_strategy_outcomes(
        execution_mode=execution_mode,
        closed_after=start_iso,
        closed_before=end_iso,
    )
    trading_result = round(sum(float(row["realized_pnl"]) for row in outcomes), 12)
    costs = list_cost_summary(window_start=start_iso, window_end=end_iso)
    intelligence_cost = _known_cost(costs, "llm")
    tool_cost = _known_cost(costs, "tool")
    metered_data_cost = _known_cost(costs, "data")
    metered_infrastructure_cost = _known_cost(costs, "infrastructure")
    metered_other_cost = _known_cost(costs, "other")
    unknown_events = int(costs.get("unknown_event_count") or 0)
    exclusions: Counter = Counter()
    assumptions = [
        "Eligible realized_pnl already includes entry fee, exit fee, and validated funding cost; trade costs are not deducted twice.",
        "Unrealized PnL and non-eligible administrative or maintenance closes are excluded.",
        "Explicit fixed allocations are additional to any metered data, infrastructure, or other cost events already present in the ledger.",
    ]

    charged_data_cost = (
        None if data_cost_usd is None else round(metered_data_cost + float(data_cost_usd), 12)
    )
    charged_infrastructure_cost = (
        None
        if infrastructure_cost_usd is None
        else round(metered_infrastructure_cost + float(infrastructure_cost_usd), 12)
    )
    charged_other_cost = (
        None if other_cost_usd is None else round(metered_other_cost + float(other_cost_usd), 12)
    )

    if not outcomes:
        verdict = "insufficient_data"
        reason = "No eligible genuine closed outcomes exist in the audit window."
        completeness = "outcome_sample_missing"
        viability_amount = None
        deployment_cost = None
    elif costs["event_count"] == 0:
        verdict = "invalid"
        reason = "Trading outcomes exist, but no runtime cost events were captured in the window."
        completeness = "runtime_cost_evidence_missing"
        viability_amount = None
        deployment_cost = None
    elif unknown_events:
        verdict = "invalid"
        reason = f"{unknown_events} runtime cost event(s) have unknown usage or pricing."
        completeness = "runtime_cost_incomplete"
        viability_amount = None
        deployment_cost = None
        exclusions["unknown_runtime_cost"] = unknown_events
    elif not _fixed_costs_complete(
        data_cost_usd,
        infrastructure_cost_usd,
        other_cost_usd,
    ):
        verdict = "invalid"
        reason = "Data, infrastructure, and other fixed costs were not all supplied explicitly."
        completeness = "fixed_cost_incomplete"
        viability_amount = None
        deployment_cost = None
    else:
        deployment_cost = round(
            intelligence_cost
            + tool_cost
            + float(charged_data_cost)
            + float(charged_infrastructure_cost)
            + float(charged_other_cost),
            12,
        )
        viability_amount = round(trading_result - deployment_cost, 12)
        verdict = _verdict(viability_amount)
        reason = "Eligible net trading result minus complete deployment cost for the same window."
        completeness = "complete"

    report = {
        "scope": "system",
        "window_start": start_iso,
        "window_end": end_iso,
        "window_days": days,
        "execution_mode": execution_mode,
        "metric_version": METRIC_VERSION,
        "schema_version": ECONOMICS_SCHEMA_VERSION,
        "outcome_contract_version": OUTCOME_SEMANTICS_VERSION,
        "net_trading_result_usd": trading_result,
        "baseline_result_usd": 0.0,
        "incremental_value_usd": trading_result,
        "intelligence_cost_usd": intelligence_cost,
        "tool_cost_usd": tool_cost,
        "data_cost_usd": charged_data_cost,
        "infrastructure_cost_usd": charged_infrastructure_cost,
        "other_cost_usd": charged_other_cost,
        "deployment_cost_usd": deployment_cost,
        "viability_amount_usd": viability_amount,
        "verdict": verdict,
        "cost_completeness": completeness,
        "sample_count": len(outcomes),
        "runtime_cost_event_count": costs["event_count"],
        "exclusion_counts": dict(exclusions),
        "assumptions": assumptions,
        "reason": reason,
        "generated_at": _iso(end),
    }
    return _persist(report) if persist else report


def _parse_dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _same_close_event(left: Any, right: Any, tolerance_sec: float = 1.0) -> bool:
    left_dt, right_dt = _parse_dt(left), _parse_dt(right)
    return bool(
        left_dt
        and right_dt
        and abs((left_dt - right_dt).total_seconds()) <= tolerance_sec
    )


def _operator_incremental_cohort(
    db,
    start_iso: str,
    end_iso: str,
    execution_mode: str,
) -> tuple[list[dict[str, Any]], Counter]:
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

    samples: list[dict[str, Any]] = []
    exclusions: Counter = Counter()
    positions = eligible_strategy_outcomes(
        execution_mode=execution_mode,
        closed_after=start_iso,
        closed_before=end_iso,
    )
    for position in positions:
        proposal_id = position.get("proposal_id")
        if not proposal_id:
            exclusions["proposal_missing"] += 1
            continue
        proposal = db.execute(
            """SELECT opportunity_id,created_at,market_type,direction
               FROM trade_proposals WHERE id=? AND status='executed'""",
            (proposal_id,),
        ).fetchone()
        opportunity_id = proposal["opportunity_id"] if proposal else None
        if not opportunity_id:
            exclusions["opportunity_link_missing"] += 1
            continue
        selected = db.execute(
            """SELECT * FROM shadow_trades
               WHERE opportunity_id=? AND was_selected=1 AND batch_id IS NOT NULL
                 AND resolved_at IS NOT NULL AND net_pnl_pct IS NOT NULL
                 AND created_at <= ?
               ORDER BY created_at DESC,rowid DESC LIMIT 1""",
            (opportunity_id, proposal["created_at"]),
        ).fetchone()
        if selected is None:
            exclusions["selected_shadow_missing"] += 1
            continue
        selected = dict(selected)
        position_market = str(
            position.get("market_type") or proposal["market_type"] or ""
        ).lower()
        position_direction = str(
            position.get("direction") or proposal["direction"] or ""
        ).lower()
        if (
            str(selected.get("market_type") or "").lower() != position_market
            or str(selected.get("direction") or "").lower() != position_direction
        ):
            exclusions["selected_shadow_identity_mismatch"] += 1
            continue
        if not _same_close_event(selected.get("resolved_at"), position.get("closed_at")):
            exclusions["shadow_horizon_mismatch"] += 1
            continue
        selected_cost_model = str(selected.get("cost_model_version") or "").strip()
        if not selected_cost_model:
            exclusions["shadow_cost_model_missing"] += 1
            continue
        controls = db.execute(
            """SELECT * FROM shadow_trades
               WHERE batch_id=? AND opportunity_id<>? AND was_selected=0
                 AND resolved_at IS NOT NULL AND net_pnl_pct IS NOT NULL
                 AND market_type=? AND direction=?
               ORDER BY rank,rowid""",
            (
                selected["batch_id"],
                opportunity_id,
                selected.get("market_type") or "spot",
                selected.get("direction") or position.get("direction"),
            ),
        ).fetchall()
        controls = [
            dict(row)
            for row in controls
            if _same_close_event(row["resolved_at"], position.get("closed_at"))
        ]
        if not controls:
            exclusions["same_horizon_control_missing"] += 1
            continue
        controls = [
            row
            for row in controls
            if str(row.get("cost_model_version") or "").strip()
            == selected_cost_model
        ]
        if not controls:
            exclusions["cost_model_mismatch"] += 1
            continue
        notional = float(position.get("entry_price") or 0) * float(
            position.get("quantity") or 0
        )
        if notional <= 0:
            exclusions["invalid_notional"] += 1
            continue
        actual_pct = float(position["realized_pnl"]) / notional * 100.0
        baseline_pct = mean(float(row["net_pnl_pct"]) for row in controls)
        baseline_usd = baseline_pct / 100.0 * notional
        incremental_usd = float(position["realized_pnl"]) - baseline_usd
        samples.append(
            {
                "position_id": position["id"],
                "proposal_id": proposal_id,
                "opportunity_id": opportunity_id,
                "batch_id": selected["batch_id"],
                "notional_usd": notional,
                "actual_pnl_usd": float(position["realized_pnl"]),
                "actual_return_pct": actual_pct,
                "baseline_return_pct": baseline_pct,
                "baseline_pnl_usd": baseline_usd,
                "incremental_value_usd": incremental_usd,
                "control_n": len(controls),
            }
        )
    return samples, exclusions


def _operator_cost_rows(
    db,
    *,
    start_iso: str,
    end_iso: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    placeholders = ",".join("?" for _ in _OPERATOR_AGENT_NAMES)
    run_rows = db.execute(
        f"""SELECT id FROM agent_runs
            WHERE agent_name IN ({placeholders})
              AND started_at >= ? AND started_at <= ?""",
        (*_OPERATOR_AGENT_NAMES, start_iso, end_iso),
    ).fetchall()
    run_ids = [str(row["id"]) for row in run_rows]

    logical_clause = (
        "(json_valid(metadata_json) "
        f"AND json_extract(metadata_json,'$.logical_agent') IN ({placeholders}))"
    )
    conditions = [
        "started_at >= ?",
        "started_at <= ?",
    ]
    params: list[Any] = [start_iso, end_iso]
    identity_clauses = [logical_clause]
    params.extend(_OPERATOR_AGENT_NAMES)
    if run_ids:
        run_placeholders = ",".join("?" for _ in run_ids)
        identity_clauses.append(f"run_id IN ({run_placeholders})")
        params.extend(run_ids)
    conditions.append("(" + " OR ".join(identity_clauses) + ")")
    rows = db.execute(
        "SELECT * FROM runtime_cost_events WHERE "
        + " AND ".join(conditions)
        + " ORDER BY started_at,id",
        params,
    ).fetchall()
    return [dict(row) for row in rows], run_ids


def build_operator_viability(
    *,
    days: int = 7,
    execution_mode: str = "paper_internal",
    data_cost_usd: float | None = None,
    window_end: datetime | str | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    if not audit_enabled():
        raise RuntimeError("viability audit is disabled by ALETHEIA_VIABILITY_AUDIT")
    if days <= 0:
        raise ValueError("days must be positive")

    from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION

    end = _resolve_window_end(window_end)
    start = end - timedelta(days=days)
    start_iso, end_iso = _iso(start), _iso(end)
    db = ensure_economics_schema()
    samples, exclusions = _operator_incremental_cohort(
        db,
        start_iso,
        end_iso,
        execution_mode,
    )
    cost_rows, run_ids = _operator_cost_rows(
        db,
        start_iso=start_iso,
        end_iso=end_iso,
    )
    intelligence_cost = round(
        sum(
            float(row["estimated_cost_usd"] or 0)
            for row in cost_rows
            if row["event_type"] == "llm"
        ),
        12,
    )
    tool_cost = round(
        sum(
            float(row["estimated_cost_usd"] or 0)
            for row in cost_rows
            if row["event_type"] == "tool"
        ),
        12,
    )
    metered_data_cost = round(
        sum(
            float(row["estimated_cost_usd"] or 0)
            for row in cost_rows
            if row["event_type"] == "data"
        ),
        12,
    )
    unknown_costs = sum(
        1
        for row in cost_rows
        if row["cost_status"] == "unknown" or row["estimated_cost_usd"] is None
    )
    logical_event_count = sum(
        1
        for row in cost_rows
        if row.get("run_id") is None
        and json.loads(row.get("metadata_json") or "{}").get("logical_agent")
        in _OPERATOR_AGENT_NAMES
    )
    actual_total = round(sum(sample["actual_pnl_usd"] for sample in samples), 12)
    baseline_total = round(sum(sample["baseline_pnl_usd"] for sample in samples), 12)
    incremental_total = round(
        sum(sample["incremental_value_usd"] for sample in samples),
        12,
    )
    charged_data_cost = (
        None
        if data_cost_usd is None
        else round(metered_data_cost + float(data_cost_usd), 12)
    )
    assumptions = [
        "Only verified eligible closed positions with an exact executed Proposal and selected Shadow batch are included.",
        "Control Shadows share batch, direction, market type, close event, and cost-model version.",
        "All System Operator LLM/tool costs in the window are charged, including skipped decisions.",
        "The event-driven daemon has no generic AgentRun; its model calls are included through the exact trading-decision module identity stored on each cost event.",
        "Risk, execution, and exit contributions remain embedded in actual realized PnL and are not separately credited to the Operator.",
    ]

    if not samples:
        verdict = "insufficient_data"
        reason = "No closed Operator trades have an exact same-close Shadow control cohort."
        completeness = "operator_cohort_missing"
        viability_amount = None
        deployment_cost = None
    elif not cost_rows:
        verdict = "invalid"
        reason = "Operator outcome cohorts exist, but no Operator runtime cost events are linked in the window."
        completeness = "operator_runtime_cost_missing"
        viability_amount = None
        deployment_cost = None
    elif unknown_costs:
        verdict = "invalid"
        reason = f"{unknown_costs} Operator runtime cost event(s) have unknown usage or pricing."
        completeness = "operator_runtime_cost_incomplete"
        viability_amount = None
        deployment_cost = None
        exclusions["unknown_runtime_cost"] += unknown_costs
    elif not _fixed_costs_complete(data_cost_usd):
        verdict = "invalid"
        reason = "Operator-attributable dynamic data cost was not supplied explicitly."
        completeness = "operator_data_cost_incomplete"
        viability_amount = None
        deployment_cost = None
    else:
        deployment_cost = round(
            intelligence_cost + tool_cost + float(charged_data_cost),
            12,
        )
        viability_amount = round(incremental_total - deployment_cost, 12)
        verdict = _verdict(viability_amount)
        reason = "Operator incremental value over exact same-close Shadows minus complete Operator dynamic costs."
        completeness = "complete"

    report = {
        "scope": "operator",
        "window_start": start_iso,
        "window_end": end_iso,
        "window_days": days,
        "execution_mode": execution_mode,
        "metric_version": METRIC_VERSION,
        "schema_version": ECONOMICS_SCHEMA_VERSION,
        "outcome_contract_version": OUTCOME_SEMANTICS_VERSION,
        "net_trading_result_usd": actual_total,
        "baseline_result_usd": baseline_total,
        "incremental_value_usd": incremental_total,
        "intelligence_cost_usd": intelligence_cost,
        "tool_cost_usd": tool_cost,
        "data_cost_usd": charged_data_cost,
        "infrastructure_cost_usd": None,
        "other_cost_usd": None,
        "deployment_cost_usd": deployment_cost,
        "viability_amount_usd": viability_amount,
        "verdict": verdict,
        "cost_completeness": completeness,
        "sample_count": len(samples),
        "operator_run_count": len(run_ids),
        "logical_operator_cost_event_count": logical_event_count,
        "runtime_cost_event_count": len(cost_rows),
        "exclusion_counts": dict(exclusions),
        "assumptions": assumptions,
        "reason": reason,
        "generated_at": _iso(end),
        "samples": samples,
    }
    return _persist(report) if persist else report


def list_viability_reports(
    *,
    scope: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List persisted viability reports for CLI/UI inspection."""
    ensure_economics_schema()
    from aletheia.storage.sqlite import get_db

    db = get_db()
    where: list[str] = []
    params: list[Any] = []
    if scope:
        if scope not in ("system", "operator"):
            raise ValueError("scope must be 'system' or 'operator'")
        where.append("scope=?")
        params.append(scope)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM viability_reports {where_sql} "
        f"ORDER BY window_end DESC, generated_at DESC "
        f"LIMIT ? OFFSET ?",
        (*params, max(1, min(int(limit), 500)), max(0, int(offset))),
    ).fetchall()
    return [dict(row) for row in rows]


def get_viability_report(report_id: str) -> dict[str, Any] | None:
    """Fetch one persisted viability report by id."""
    ensure_economics_schema()
    from aletheia.storage.sqlite import get_db

    row = get_db().execute(
        "SELECT * FROM viability_reports WHERE id=?", (report_id,)
    ).fetchone()
    return dict(row) if row else None


def render_viability(report: dict[str, Any]) -> str:
    def money(value: Any) -> str:
        return "unknown" if value is None else f"${float(value):.6f}"

    lines = [
        f"Economic Viability Audit — {report['scope']}",
        "",
        f"Window: {report['window_start']} → {report['window_end']}",
        f"Execution mode: {report['execution_mode']}",
        f"Verdict: {report['verdict']}",
        f"Cost completeness: {report['cost_completeness']}",
        f"Sample count: {report['sample_count']}",
        "",
        f"Trading result: {money(report.get('net_trading_result_usd'))}",
        f"Baseline result: {money(report.get('baseline_result_usd'))}",
        f"Incremental value: {money(report.get('incremental_value_usd'))}",
        f"LLM cost: {money(report.get('intelligence_cost_usd'))}",
        f"Tool cost: {money(report.get('tool_cost_usd'))}",
        f"Data cost: {money(report.get('data_cost_usd'))}",
        f"Infrastructure cost: {money(report.get('infrastructure_cost_usd'))}",
        f"Other cost: {money(report.get('other_cost_usd'))}",
        f"Total charged cost: {money(report.get('deployment_cost_usd'))}",
        f"Viability amount: {money(report.get('viability_amount_usd'))}",
        "",
        f"Reason: {report['reason']}",
    ]
    exclusions = report.get("exclusion_counts") or {}
    if exclusions:
        lines.append(
            "Exclusions: "
            + ", ".join(f"{key}={value}" for key, value in sorted(exclusions.items()))
        )
    return "\n".join(lines)
