"""Read-only Thesis Lineage analytics.

Thesis generation happens upstream in producers such as market_observer,
System Operator, and Serenity. This module only reconstructs and evaluates
those existing thesis/reasoning/evidence artifacts. Missing fields stay
missing; the read model must not add LLM calls, create replacement thesis text,
create records, call agents, or change trading behavior.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from aletheia.analytics.outcome_integrity import assess_reportable_outcome
from aletheia.analytics.outcome_semantics import (
    MISSED_ALPHA,
    POSITIVE_GROSS_ONLY,
    PROTECTIVE,
    classify_outcome_row,
    outcome_values,
)


_MISS_THRESHOLD_PCT = 0.5
_POOR_EXPRESSION_DELTA_PCT = 0.5
_ROOT_TABLES = ("opportunities", "serenity_opportunities")
_OPTIONAL_TABLES = (
    "pi_decisions",
    "trade_proposals",
    "risk_decisions",
    "orders",
    "fills",
    "positions",
    "shadow_trades",
    "universe_members",
    "universe_forward_outcomes",
)


def build_thesis_lineage_records(days: int = 7, include_open: bool = True) -> list[dict]:
    """Reconstruct thesis lineage records from existing causal tables."""
    db, _errors = _open_db()
    if db is None:
        return []
    cutoff = _window(days)["cutoff"]
    market_roots = _market_roots(db, cutoff) if _table_exists(db, "opportunities") else []
    serenity_roots = _serenity_roots(db, cutoff) if _table_exists(db, "serenity_opportunities") else []
    ctx = _build_context(db, market_roots, serenity_roots, cutoff, include_open)
    records = [
        _record_from_root(root, root_type="market_observer", ctx=ctx)
        for root in market_roots
    ]
    records.extend(
        _record_from_root(root, root_type="serenity", ctx=ctx)
        for root in serenity_roots
    )
    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records


def build_thesis_lineage_audit(days: int = 7) -> dict:
    """Build a JSON-serializable thesis lineage report."""
    generated_at = _utcnow().isoformat(timespec="seconds")
    window = _window(days)
    report = _base_report(generated_at, window)
    db, errors = _open_db()
    if db is None:
        report["schema_errors"].extend(errors)
        return report
    missing_roots = [t for t in _ROOT_TABLES if not _table_exists(db, t)]
    missing_optional = [t for t in _OPTIONAL_TABLES if not _table_exists(db, t)]
    if missing_roots:
        report["schema_errors"].append(f"Missing root source table(s): {', '.join(missing_roots)}")
    if missing_optional:
        report["schema_errors"].append(f"Missing optional lineage table(s): {', '.join(missing_optional)}")
    try:
        records = build_thesis_lineage_records(days=days)
    except sqlite3.OperationalError as exc:
        report["schema_errors"].append(str(exc))
        records = []
    report["records"] = records
    report["summary"] = _summary(records)
    report["data_completeness"] = _data_completeness(records)
    report["raw_table_completeness"] = _raw_table_completeness(db, window["cutoff"])
    report["by_scanner_family"] = _group_summary(records, "scanner_family")
    report["by_source"] = _group_summary(records, "root_type")
    report["top_missed_alpha"] = sorted(
        [row for row in records if row.get("verdict") == "missed_alpha"],
        key=lambda row: _first_number(row.get("forward_net_return_pct")) or float("-inf"),
        reverse=True,
    )[:10]
    report["top_failed_traded_thesis"] = _top(records, "failed", "actual_pnl_pct", reverse=False)
    report["top_poor_expression"] = _top(records, "poor_expression", "actual_pnl_pct", reverse=False)
    report["main_finding"] = _main_finding(report)
    report["next_action"] = _next_action(report)
    return report


def render_thesis_lineage_audit(report: dict) -> str:
    """Render the report for CLI output."""
    summary = report.get("summary") or {}
    lines = ["Thesis Lineage Audit", "", f"Window: {_window_label(report.get('window') or {})}", ""]
    if report.get("schema_errors"):
        lines.append("Evidence:")
        for err in report["schema_errors"]:
            lines.append(f"  {err}")
        lines.append("")
    lines.extend([
        "Summary:",
        f"  total_thesis_count: {summary.get('total_thesis_count', 0)}",
        f"  traded_count: {summary.get('traded_count', 0)}",
        f"  skipped_watch_count: {summary.get('skipped_watch_count', 0)}",
        f"  pending_count: {summary.get('pending_count', 0)}",
        f"  missed_alpha_count: {summary.get('missed_alpha_count', 0)}",
        f"  failed_traded_thesis_count: {summary.get('failed_traded_thesis_count', 0)}",
        f"  poor_expression_count: {summary.get('poor_expression_count', 0)}",
        "",
        "Data completeness:",
    ])
    completeness = report.get("data_completeness") or {}
    missing = completeness.get("missing_counts") or {}
    rates = completeness.get("missing_rates") or {}
    for key in (
        "missing_decision_action",
        "missing_decision_reasoning",
        "mature_non_traded_missing_shadow_outcome",
        "mature_non_traded_missing_forward_return",
        "missing_decision_meta_on_proposed_records",
        "non_traded_missing_shadow_outcome",
        "non_traded_missing_forward_return",
        "traded_missing_position_id",
        "traded_missing_actual_pnl_pct",
    ):
        lines.append(f"  {key}: {missing.get(key, 0)} ({_fmt_rate(rates.get(key))})")
    raw = report.get("raw_table_completeness") or {}
    if raw:
        lines.append("  raw tables:")
        for table, row in raw.items():
            if not isinstance(row, dict):
                continue
            important = [
                f"total={row.get('total', 0)}",
                f"missing_opportunity_id={row.get('missing_opportunity_id', 0)}",
            ]
            if row.get("missing_signal_source") is not None:
                important.append(f"missing_signal_source={row.get('missing_signal_source', 0)}")
            if row.get("link_required_missing_opportunity_id") is not None:
                important.append(
                    f"link_required_missing_opp={row.get('link_required_missing_opportunity_id', 0)}"
                )
            if row.get("proposal_backfillable_missing_opportunity_id") is not None:
                important.append(
                    f"proposal_backfillable_missing_opp={row.get('proposal_backfillable_missing_opportunity_id', 0)}"
                )
            if "missing_decision_meta_in_agent_rationale" in row:
                important.append(f"missing_decision_meta={row.get('missing_decision_meta_in_agent_rationale', 0)}")
            if "missing_pnl_pct" in row:
                important.append(f"missing_pnl_pct={row.get('missing_pnl_pct', 0)}")
            if "stale_missing_pnl_pct" in row:
                important.append(f"stale_missing_pnl_pct={row.get('stale_missing_pnl_pct', 0)}")
            if "missing_forward_return_pct" in row:
                important.append(f"missing_forward_return_pct={row.get('missing_forward_return_pct', 0)}")
            if "overdue_pending" in row:
                important.append(f"overdue_pending={row.get('overdue_pending', 0)}")
            lines.append(f"    {table}: {', '.join(important)}")
    lines.extend([
        "",
        "By source:",
    ])
    for row in report.get("by_source") or []:
        lines.append(
            f"  {row['name']}: n={row['n']}, traded={row['traded_count']}, "
            f"missed={row['missed_alpha_count']}, poor={row['poor_expression_count']}"
        )
    if not report.get("by_source"):
        lines.append("  n/a")
    lines.extend(["", "By scanner_family:"])
    for row in (report.get("by_scanner_family") or [])[:8]:
        lines.append(
            f"  {row['name']}: n={row['n']}, traded={row['traded_count']}, "
            f"missed={row['missed_alpha_count']}, avg_shadow={_fmt_pct(row.get('avg_shadow_pnl_pct'))}"
        )
    if not report.get("by_scanner_family"):
        lines.append("  n/a")
    _append_top(lines, "Top missed alpha records", report.get("top_missed_alpha") or [], "forward_net_return_pct")
    _append_top(lines, "Top failed traded thesis records", report.get("top_failed_traded_thesis") or [], "actual_pnl_pct")
    lines.extend([
        "",
        "Main finding:",
        f"  {report.get('main_finding') or 'Not enough thesis lineage evidence yet.'}",
        "",
        "Next action:",
        f"  {report.get('next_action') or 'Collect more lineage evidence before changing behavior.'}",
    ])
    return "\n".join(lines)


def filter_thesis_lineage_records(
    *,
    days: int = 30,
    position_id: str | None = None,
    order_id: str | None = None,
    proposal_id: str | None = None,
    opportunity_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    records = build_thesis_lineage_records(days=days, include_open=True)
    if position_id:
        records = [r for r in records if r.get("position_id") == position_id]
    if order_id:
        records = [r for r in records if r.get("order_id") == order_id]
    if proposal_id:
        records = [r for r in records if r.get("proposal_id") == proposal_id]
    if opportunity_id:
        records = [r for r in records if r.get("root_id") == opportunity_id]
    return records[: max(1, int(limit or 50))]


def compact_thesis_lineage_summary(report: dict) -> dict:
    """Small doctor-safe summary without full lineage records."""
    summary = report.get("summary") or {}
    completeness = report.get("data_completeness") or {}
    missing = completeness.get("missing_counts") or {}
    rates = completeness.get("missing_rates") or {}
    raw = report.get("raw_table_completeness") or {}
    return {
        "available": not bool(report.get("schema_errors")),
        "window_days": (report.get("window") or {}).get("days"),
        "total_thesis_count": summary.get("total_thesis_count", 0),
        "traded_count": summary.get("traded_count", 0),
        "missed_alpha_count": summary.get("missed_alpha_count", 0),
        "failed_traded_thesis_count": summary.get("failed_traded_thesis_count", 0),
        "poor_expression_count": summary.get("poor_expression_count", 0),
        "chain_counts": completeness.get("chain_counts") or {},
        "missing_counts": {
            "missing_decision_action": missing.get("missing_decision_action", 0),
            "missing_decision_reasoning": missing.get("missing_decision_reasoning", 0),
            "mature_non_traded_missing_shadow_outcome": missing.get("mature_non_traded_missing_shadow_outcome", 0),
            "mature_non_traded_missing_forward_return": missing.get("mature_non_traded_missing_forward_return", 0),
            "missing_decision_meta_on_proposed_records": missing.get("missing_decision_meta_on_proposed_records", 0),
            "non_traded_missing_shadow_outcome": missing.get("non_traded_missing_shadow_outcome", 0),
            "non_traded_missing_forward_return": missing.get("non_traded_missing_forward_return", 0),
            "traded_missing_position_id": missing.get("traded_missing_position_id", 0),
            "traded_missing_actual_pnl_pct": missing.get("traded_missing_actual_pnl_pct", 0),
        },
        "missing_rates": {
            "missing_decision_action": rates.get("missing_decision_action"),
            "mature_non_traded_missing_shadow_outcome": rates.get("mature_non_traded_missing_shadow_outcome"),
            "mature_non_traded_missing_forward_return": rates.get("mature_non_traded_missing_forward_return"),
            "non_traded_missing_shadow_outcome": rates.get("non_traded_missing_shadow_outcome"),
            "non_traded_missing_forward_return": rates.get("non_traded_missing_forward_return"),
        },
        "raw_missing": {
            "pi_decisions_missing_opportunity_id": (raw.get("pi_decisions") or {}).get("missing_opportunity_id"),
            "pi_decisions_link_required_missing_opportunity_id": (raw.get("pi_decisions") or {}).get("link_required_missing_opportunity_id"),
            "pi_decisions_proposal_backfillable_missing_opportunity_id": (raw.get("pi_decisions") or {}).get("proposal_backfillable_missing_opportunity_id"),
            "pi_decisions_missing_signal_source": (raw.get("pi_decisions") or {}).get("missing_signal_source"),
            "trade_proposals_missing_opportunity_id": (raw.get("trade_proposals") or {}).get("missing_opportunity_id"),
            "trade_proposals_missing_decision_meta": (raw.get("trade_proposals") or {}).get("missing_decision_meta_in_agent_rationale"),
            "shadow_trades_missing_pnl_pct": (raw.get("shadow_trades") or {}).get("missing_pnl_pct"),
            "shadow_trades_stale_missing_pnl_pct": (raw.get("shadow_trades") or {}).get("stale_missing_pnl_pct"),
            "forward_outcomes_missing_forward_return_pct": (raw.get("universe_forward_outcomes") or {}).get("missing_forward_return_pct"),
            "forward_outcomes_overdue_pending": (raw.get("universe_forward_outcomes") or {}).get("overdue_pending"),
        },
        "main_finding": report.get("main_finding"),
        "next_action": report.get("next_action"),
        "schema_errors": report.get("schema_errors") or [],
    }


def _market_roots(db, cutoff: str) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM opportunities WHERE datetime(created_at) >= datetime(?) "
        "ORDER BY datetime(created_at) DESC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _serenity_roots(db, cutoff: str) -> list[dict]:
    rows = db.execute(
        "SELECT * FROM serenity_opportunities WHERE datetime(created_at) >= datetime(?) "
        "ORDER BY datetime(created_at) DESC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _build_context(db, market_roots: list[dict], serenity_roots: list[dict], cutoff: str, include_open: bool) -> dict:
    root_ids = [r["id"] for r in market_roots + serenity_roots if r.get("id")]
    ctx: dict[str, Any] = {
        "decisions": {},
        "proposals_by_root": {},
        "proposals_by_id": {},
        "risk_by_proposal": {},
        "orders_by_proposal": {},
        "fills_by_order": {},
        "positions_by_proposal": {},
        "positions_by_order": {},
        "positions_by_root": {},
        "shadows_by_root": {},
        "shadow_batch_avg": {},
        "universe_members": {},
        "forward_by_member": {},
    }
    if not root_ids:
        return ctx
    if _table_exists(db, "pi_decisions"):
        for rows in _select_in(db, "SELECT * FROM pi_decisions WHERE opportunity_id IN ({})", root_ids):
            for row in rows:
                d = dict(row)
                source = d.get("signal_source") or "market_observer"
                key = (d.get("opportunity_id"), source)
                if _is_newer(d, ctx["decisions"].get(key), "created_at"):
                    ctx["decisions"][key] = d
    proposal_ids: set[str] = set()
    if _table_exists(db, "trade_proposals"):
        for rows in _select_in(db, "SELECT * FROM trade_proposals WHERE opportunity_id IN ({})", root_ids):
            for row in rows:
                p = dict(row)
                proposal_ids.add(p["id"])
                ctx["proposals_by_id"][p["id"]] = p
                if _is_newer(p, ctx["proposals_by_root"].get(p.get("opportunity_id")), "created_at"):
                    ctx["proposals_by_root"][p.get("opportunity_id")] = p
        decision_pids = [d.get("proposal_id") for d in ctx["decisions"].values() if d.get("proposal_id")]
        missing = [pid for pid in decision_pids if pid not in ctx["proposals_by_id"]]
        for rows in _select_in(db, "SELECT * FROM trade_proposals WHERE id IN ({})", missing):
            for row in rows:
                p = dict(row)
                proposal_ids.add(p["id"])
                ctx["proposals_by_id"][p["id"]] = p
        if proposal_ids and _table_exists(db, "pi_decisions"):
            for rows in _select_in(db, "SELECT * FROM pi_decisions WHERE proposal_id IN ({})", list(proposal_ids)):
                for row in rows:
                    d = dict(row)
                    proposal = ctx["proposals_by_id"].get(d.get("proposal_id"))
                    inferred_root = d.get("opportunity_id") or (proposal or {}).get("opportunity_id")
                    if not inferred_root:
                        continue
                    source = d.get("signal_source") or _source_from_proposal(proposal)
                    if not d.get("signal_source"):
                        d["signal_source"] = source
                        d["_signal_source_inferred_from"] = "proposal_trade_school"
                    d["_lineage_root_id"] = inferred_root
                    d["_opportunity_id_inferred_from"] = None if d.get("opportunity_id") else "proposal_id"
                    key = (inferred_root, source)
                    if _is_newer(d, ctx["decisions"].get(key), "created_at"):
                        ctx["decisions"][key] = d
    if proposal_ids and _table_exists(db, "risk_decisions"):
        for rows in _select_in(db, "SELECT * FROM risk_decisions WHERE proposal_id IN ({})", list(proposal_ids)):
            for row in rows:
                r = dict(row)
                if _is_newer(r, ctx["risk_by_proposal"].get(r.get("proposal_id")), "created_at"):
                    ctx["risk_by_proposal"][r.get("proposal_id")] = r
    order_ids: set[str] = set()
    if proposal_ids and _table_exists(db, "orders"):
        for rows in _select_in(db, "SELECT * FROM orders WHERE proposal_id IN ({})", list(proposal_ids)):
            for row in rows:
                o = dict(row)
                order_ids.add(o["id"])
                if _is_newer(o, ctx["orders_by_proposal"].get(o.get("proposal_id")), "created_at"):
                    ctx["orders_by_proposal"][o.get("proposal_id")] = o
    if order_ids and _table_exists(db, "fills"):
        for rows in _select_in(db, "SELECT * FROM fills WHERE order_id IN ({})", list(order_ids)):
            for row in rows:
                f = dict(row)
                if _is_newer(f, ctx["fills_by_order"].get(f.get("order_id")), "filled_at"):
                    ctx["fills_by_order"][f.get("order_id")] = f
    status_clause = "" if include_open else " AND status = 'closed'"
    if _table_exists(db, "positions"):
        if proposal_ids:
            for rows in _select_in(db, f"SELECT * FROM positions WHERE proposal_id IN ({{}}){status_clause}", list(proposal_ids)):
                for row in rows:
                    p = dict(row)
                    if _is_newer(p, ctx["positions_by_proposal"].get(p.get("proposal_id")), "opened_at"):
                        ctx["positions_by_proposal"][p.get("proposal_id")] = p
                    if p.get("order_id"):
                        ctx["positions_by_order"][p["order_id"]] = p
        for rows in _select_in(db, f"SELECT * FROM positions WHERE opportunity_id IN ({{}}){status_clause}", root_ids):
            for row in rows:
                p = dict(row)
                if _is_newer(p, ctx["positions_by_root"].get(p.get("opportunity_id")), "opened_at"):
                    ctx["positions_by_root"][p.get("opportunity_id")] = p
    if _table_exists(db, "shadow_trades"):
        batch_values: dict[str, list[float]] = defaultdict(list)
        for rows in _select_in(db, "SELECT * FROM shadow_trades WHERE opportunity_id IN ({})", root_ids):
            for row in rows:
                s = dict(row)
                if s.get("pnl_pct") is not None and s.get("batch_id"):
                    batch_values[str(s["batch_id"])].append(float(s["pnl_pct"]))
                if _shadow_better(s, ctx["shadows_by_root"].get(s.get("opportunity_id"))):
                    ctx["shadows_by_root"][s.get("opportunity_id")] = s
        ctx["shadow_batch_avg"] = {
            batch: _round(mean(values))
            for batch, values in batch_values.items()
            if values
        }
    member_ids = [r.get("universe_member_id") for r in market_roots if r.get("universe_member_id")]
    if member_ids and _table_exists(db, "universe_members"):
        for rows in _select_in(db, "SELECT * FROM universe_members WHERE id IN ({})", member_ids):
            for row in rows:
                m = dict(row)
                ctx["universe_members"][m["id"]] = m
    if member_ids and _table_exists(db, "universe_forward_outcomes"):
        for rows in _select_in(
            db,
            "SELECT * FROM universe_forward_outcomes WHERE member_id IN ({}) "
            "AND status = 'settled' AND forward_return_pct IS NOT NULL",
            member_ids,
        ):
            for row in rows:
                f = dict(row)
                if _forward_better(f, ctx["forward_by_member"].get(f.get("member_id"))):
                    ctx["forward_by_member"][f.get("member_id")] = f
    return ctx


def _record_from_root(root: dict, *, root_type: str, ctx: dict) -> dict:
    root_id = root.get("id")
    decision = _decision_for_root(root_id, root_type, ctx)
    proposal = _proposal_for_root(root_id, decision, ctx)
    risk = ctx["risk_by_proposal"].get(proposal.get("id")) if proposal else None
    order = ctx["orders_by_proposal"].get(proposal.get("id")) if proposal else None
    fill = ctx["fills_by_order"].get(order.get("id")) if order else None
    position = _position_for(root_id, proposal, order, ctx)
    shadow = ctx["shadows_by_root"].get(root_id)
    member = ctx["universe_members"].get(root.get("universe_member_id"))
    forward = ctx["forward_by_member"].get(root.get("universe_member_id"))
    parsed = _parse_agent_rationale(proposal.get("agent_rationale") if proposal else None)

    direction = root.get("direction_bias") or root.get("direction")
    outcome_assessment = None
    if position and str(position.get("status") or "").lower() == "closed":
        outcome_assessment = assess_reportable_outcome(position)
    outcome_unavailable = bool(
        outcome_assessment is not None and not outcome_assessment.eligible
    )
    actual_pnl = None if outcome_unavailable else _actual_pnl(position)
    notional = _notional(position, proposal, order, fill)
    actual_pnl_pct = _pnl_pct(actual_pnl, notional)
    shadow_pnl = _round(_first_number(shadow.get("pnl_pct") if shadow else None))
    batch_avg = ctx["shadow_batch_avg"].get(str(shadow.get("batch_id"))) if shadow and shadow.get("batch_id") else None
    risk_reward = _first_number(proposal.get("risk_reward") if proposal else None)
    was_traded = bool(position or (order and _is_filled_order(order)) or (proposal and (proposal.get("status") or "").lower() in {"executed", "filled"}))
    forward_verdict = classify_outcome_row(forward, direction) if forward else "insufficient_data"
    verdict = (
        "outcome_unavailable"
        if outcome_unavailable
        else _verdict(
            was_traded,
            actual_pnl,
            actual_pnl_pct,
            risk_reward,
            shadow_pnl,
            batch_avg,
            forward_verdict,
        )
    )
    forward_values = outcome_values(forward or {}, direction)
    trade_motivation, trade_motivation_source = _structured_trade_motivation(
        decision, parsed
    )

    return {
        "lineage_id": f"{root_type}:{root_id}",
        "root_type": root_type,
        "root_id": root_id,
        "symbol": root.get("symbol"),
        "market_type": root.get("market_type") or root.get("market"),
        "direction": direction,
        "created_at": root.get("created_at"),
        "universe_member_id": root.get("universe_member_id"),
        "universe_rank": member.get("rank") if member else None,
        "scanner_family": root.get("scanner_family") or (member.get("scanner_family") if member else None),
        "tradability_score": _first_number(member.get("tradability_score") if member else None),
        "late_entry_penalty": _first_number(member.get("late_entry_penalty") if member else None),
        "regime_tag": root.get("regime_tag") or (member.get("regime_tag") if member else None),
        "observation_thesis": root.get("thesis") or parsed.get("human_thesis"),
        "observation_confidence": root.get("confidence_score") or root.get("confidence") or root.get("assessed_tier"),
        "key_observations": _key_observations(root),
        "decision_action": decision.get("action") if decision else None,
        "decision_conviction": decision.get("conviction") if decision else None,
        "decision_reasoning": decision.get("reasoning") if decision else None,
        "decision_signal_source": decision.get("signal_source") if decision else None,
        "decision_signal_source_inferred_from": decision.get("_signal_source_inferred_from") if decision else None,
        "decision_opportunity_id_inferred_from": decision.get("_opportunity_id_inferred_from") if decision else None,
        "supporting_signals": parsed.get("supporting_signals"),
        "opposing_signals": parsed.get("opposing_signals"),
        "bear_case": (decision.get("bear_case") if decision else None) or parsed.get("bear_case"),
        "key_assumptions": _json_list(decision.get("key_assumptions") if decision else None) or parsed.get("key_assumptions"),
        "proposal_id": proposal.get("id") if proposal else None,
        "proposal_status": proposal.get("status") if proposal else None,
        "risk_approved": _bool_or_none(risk.get("approved") if risk else None),
        "risk_violations": _json_list(risk.get("violations") if risk else None),
        "order_id": order.get("id") if order else None,
        "position_id": position.get("id") if position else None,
        "was_traded": was_traded,
        "notional_usdt": notional,
        "actual_pnl": actual_pnl,
        "actual_pnl_pct": actual_pnl_pct,
        "outcome_eligibility_reason": (
            outcome_assessment.reason if outcome_unavailable else None
        ),
        "close_reason": position.get("close_reason") if position else None,
        "shadow_batch_id": shadow.get("batch_id") if shadow else None,
        "shadow_rank": shadow.get("rank") if shadow else None,
        "shadow_pnl_pct": shadow_pnl,
        "counterfactual_avg_pnl_pct": batch_avg,
        "verdict": verdict,
        "failure_bucket": _failure_bucket(verdict, risk, position),
        "parse_warning": parsed.get("parse_warning"),
        "human_thesis": parsed.get("human_thesis"),
        "decision_meta": parsed.get("decision_meta"),
        "trade_motivation": trade_motivation,
        "trade_motivation_source": trade_motivation_source,
        "forward_return_pct": _round(forward_values["directional_return_pct"]),
        "forward_net_return_pct": _round(forward_values["net_return_pct"]),
        "forward_min_tradable_edge_pct": _round(forward_values["min_tradable_edge_pct"]),
        "serenity": _serenity_fields(root) if root_type == "serenity" else None,
    }


def _decision_for_root(root_id: str | None, root_type: str, ctx: dict) -> dict | None:
    source = "serenity" if root_type == "serenity" else "market_observer"
    return ctx["decisions"].get((root_id, source))


def _source_from_proposal(proposal: dict | None) -> str:
    if proposal and proposal.get("trade_school") == "serenity":
        return "serenity"
    return "market_observer"


def _proposal_for_root(root_id: str | None, decision: dict | None, ctx: dict) -> dict | None:
    if decision and decision.get("proposal_id"):
        proposal = ctx["proposals_by_id"].get(decision["proposal_id"])
        if proposal:
            return proposal
    return ctx["proposals_by_root"].get(root_id)


def _position_for(root_id: str | None, proposal: dict | None, order: dict | None, ctx: dict) -> dict | None:
    if proposal and ctx["positions_by_proposal"].get(proposal.get("id")):
        return ctx["positions_by_proposal"][proposal["id"]]
    if order and ctx["positions_by_order"].get(order.get("id")):
        return ctx["positions_by_order"][order["id"]]
    return ctx["positions_by_root"].get(root_id)


def _parse_agent_rationale(raw: str | None) -> dict:
    if not raw:
        return {"human_thesis": None, "decision_meta": {}, "supporting_signals": [], "opposing_signals": [], "key_assumptions": []}
    if "\n---\n" not in raw:
        return {"human_thesis": raw, "decision_meta": {}, "supporting_signals": [], "opposing_signals": [], "key_assumptions": []}
    human, meta_raw = raw.split("\n---\n", 1)
    result = {"human_thesis": human.strip() or None, "decision_meta": {}, "supporting_signals": [], "opposing_signals": [], "key_assumptions": []}
    try:
        meta = json.loads(meta_raw)
        if not isinstance(meta, dict):
            result["parse_warning"] = "decision_meta_not_object"
            return result
    except (TypeError, ValueError):
        result["parse_warning"] = "decision_meta_json_invalid"
        return result
    result["decision_meta"] = meta
    result["supporting_signals"] = _coerce_list(meta.get("supporting_signals") or meta.get("supporting"))
    result["opposing_signals"] = _coerce_list(meta.get("opposing_signals") or meta.get("opposing"))
    result["bear_case"] = meta.get("bear_case")
    result["key_assumptions"] = _coerce_list(meta.get("key_assumptions"))
    return result


def _structured_trade_motivation(
    decision: dict | None, parsed_rationale: dict
) -> tuple[dict | None, str | None]:
    from aletheia.trading.trade_motivation import parse_trade_motivation

    if decision:
        try:
            manifest = json.loads(decision.get("evidence_manifest_json") or "{}")
        except (TypeError, ValueError):
            manifest = {}
        if isinstance(manifest, dict):
            motivation = parse_trade_motivation(manifest.get("trade_motivation"))
            if motivation is not None:
                return motivation, "pi_decision"
    motivation = parse_trade_motivation(
        (parsed_rationale.get("decision_meta") or {}).get("trade_motivation")
    )
    return (motivation, "trade_proposal") if motivation is not None else (None, None)


def _verdict(
    was_traded: bool,
    actual_pnl: float | None,
    actual_pnl_pct: float | None,
    risk_reward: float | None,
    shadow_pnl: float | None,
    batch_avg: float | None,
    forward_verdict: str,
) -> str:
    if was_traded:
        if actual_pnl_pct is not None and batch_avg is not None and actual_pnl_pct < batch_avg - _POOR_EXPRESSION_DELTA_PCT:
            return "poor_expression"
        if actual_pnl_pct is not None:
            if actual_pnl_pct > 0:
                return "validated"
            if actual_pnl_pct < 0:
                return "failed"
            if actual_pnl is not None and actual_pnl >= 0 and risk_reward is not None and risk_reward > 0:
                return "validated"
        return "pending"
    if forward_verdict == MISSED_ALPHA:
        return "missed_alpha"
    if forward_verdict == POSITIVE_GROSS_ONLY or (
        shadow_pnl is not None and shadow_pnl > _MISS_THRESHOLD_PCT
    ):
        return POSITIVE_GROSS_ONLY
    if forward_verdict == PROTECTIVE:
        return PROTECTIVE
    return "pending"


def _summary(records: list[dict]) -> dict:
    return {
        "total_thesis_count": len(records),
        "traded_count": sum(1 for r in records if r.get("was_traded")),
        "skipped_watch_count": sum(1 for r in records if (r.get("decision_action") or "").lower() in {"skip", "watch"}),
        "pending_count": sum(1 for r in records if r.get("verdict") == "pending"),
        "missed_alpha_count": sum(1 for r in records if r.get("verdict") == "missed_alpha"),
        "positive_gross_move_only_count": sum(
            1 for r in records if r.get("verdict") == POSITIVE_GROSS_ONLY
        ),
        "failed_traded_thesis_count": sum(1 for r in records if r.get("verdict") == "failed"),
        "poor_expression_count": sum(1 for r in records if r.get("verdict") == "poor_expression"),
    }


def _data_completeness(records: list[dict]) -> dict:
    total = len(records)

    def missing(field: str) -> int:
        return sum(1 for r in records if r.get(field) in (None, "", [], {}))

    def count_where(predicate) -> int:
        return sum(1 for r in records if predicate(r))

    records_with_decision = count_where(lambda r: bool(r.get("decision_action")))
    proposed_records = count_where(lambda r: bool(r.get("proposal_id")))
    non_traded_records = count_where(lambda r: not r.get("was_traded"))
    mature_non_traded_records = count_where(
        lambda r: not r.get("was_traded") and _older_than_hours(r.get("created_at"), 25)
    )
    traded_records = count_where(lambda r: bool(r.get("was_traded")))
    counts = {
        "missing_root_id": missing("root_id"),
        "missing_root_source": missing("root_type"),
        "missing_universe_member_id": missing("universe_member_id"),
        "missing_observation_thesis": missing("observation_thesis"),
        "unjudged_observation_count": missing("decision_action"),
        "missing_decision_action": missing("decision_action"),
        "missing_decision_reasoning": missing("decision_reasoning"),
        "linked_decision_missing_signal_source": count_where(
            lambda r: bool(r.get("decision_action")) and not r.get("decision_signal_source")
        ),
        "missing_decision_meta_on_proposed_records": count_where(
            lambda r: bool(r.get("proposal_id")) and not r.get("decision_meta")
        ),
        "parse_warning_count": count_where(lambda r: bool(r.get("parse_warning"))),
        "non_traded_missing_shadow_outcome": count_where(
            lambda r: not r.get("was_traded") and r.get("shadow_pnl_pct") is None
        ),
        "mature_non_traded_missing_shadow_outcome": count_where(
            lambda r: (
                not r.get("was_traded")
                and _older_than_hours(r.get("created_at"), 25)
                and r.get("shadow_pnl_pct") is None
            )
        ),
        "non_traded_missing_forward_return": count_where(
            lambda r: not r.get("was_traded") and r.get("forward_return_pct") is None
        ),
        "mature_non_traded_missing_forward_return": count_where(
            lambda r: (
                not r.get("was_traded")
                and _older_than_hours(r.get("created_at"), 25)
                and r.get("forward_return_pct") is None
            )
        ),
        "traded_missing_position_id": count_where(
            lambda r: bool(r.get("was_traded")) and not r.get("position_id")
        ),
        "traded_missing_actual_pnl_pct": count_where(
            lambda r: bool(r.get("was_traded")) and r.get("actual_pnl_pct") is None
        ),
    }
    denominators = {
        "missing_decision_meta_on_proposed_records": proposed_records,
        "linked_decision_missing_signal_source": records_with_decision,
        "non_traded_missing_shadow_outcome": non_traded_records,
        "mature_non_traded_missing_shadow_outcome": mature_non_traded_records,
        "non_traded_missing_forward_return": non_traded_records,
        "mature_non_traded_missing_forward_return": mature_non_traded_records,
        "traded_missing_position_id": traded_records,
        "traded_missing_actual_pnl_pct": traded_records,
    }
    return {
        "total_records": total,
        "chain_counts": {
            "with_universe_member": total - counts["missing_universe_member_id"],
            "with_decision": records_with_decision,
            "with_proposal": proposed_records,
            "with_shadow_outcome": count_where(lambda r: r.get("shadow_pnl_pct") is not None),
            "with_forward_return": count_where(lambda r: r.get("forward_return_pct") is not None),
            "with_position": count_where(lambda r: bool(r.get("position_id"))),
        },
        "missing_counts": counts,
        "missing_rates": {
            key: _rate(value, denominators.get(key, total))
            for key, value in counts.items()
        },
    }


def _raw_table_completeness(db, cutoff: str) -> dict:
    rows: dict[str, dict] = {}
    if _table_exists(db, "pi_decisions"):
        link_required = (
            "action IN ('trade','scale_in','flip','close','reduce') "
            "OR proposal_id IS NOT NULL OR symbol IS NOT NULL OR direction IS NOT NULL"
        )
        global_no_target = (
            "action IN ('skip','watch') AND symbol IS NULL AND direction IS NULL "
            "AND proposal_id IS NULL AND (opportunity_id IS NULL OR opportunity_id = '')"
        )
        has_signal_source = "signal_source" in _table_columns(db, "pi_decisions")
        rows["pi_decisions"] = {
            "total": _count_rows(db, "pi_decisions", cutoff),
            "missing_opportunity_id": _count_rows(
                db, "pi_decisions", cutoff, "(opportunity_id IS NULL OR opportunity_id = '')"
            ),
            "global_no_target_count": _count_rows(db, "pi_decisions", cutoff, global_no_target),
            "link_required_count": _count_rows(db, "pi_decisions", cutoff, link_required),
            "link_required_missing_opportunity_id": _count_rows(
                db,
                "pi_decisions",
                cutoff,
                f"({link_required}) AND (opportunity_id IS NULL OR opportunity_id = '')",
            ),
            "proposal_backfillable_missing_opportunity_id": _count_pi_proposal_join_rows(
                db,
                cutoff,
                "(pd.opportunity_id IS NULL OR pd.opportunity_id = '') "
                "AND tp.opportunity_id IS NOT NULL AND tp.opportunity_id != ''",
            ) if _table_exists(db, "trade_proposals") else 0,
            "missing_signal_source": _count_rows(
                db, "pi_decisions", cutoff, "(signal_source IS NULL OR signal_source = '')"
            ) if has_signal_source else None,
            "link_required_missing_signal_source": _count_rows(
                db,
                "pi_decisions",
                cutoff,
                f"({link_required}) AND (signal_source IS NULL OR signal_source = '')",
            ) if has_signal_source else None,
            "missing_reasoning": _count_rows(
                db, "pi_decisions", cutoff, "(reasoning IS NULL OR reasoning = '')"
            ),
        }
    if _table_exists(db, "trade_proposals"):
        rationale_rows = _select_rows(
            db,
            "trade_proposals",
            cutoff,
            "agent_rationale IS NOT NULL AND agent_rationale != ''",
            columns="agent_rationale",
        )
        missing_meta = 0
        invalid_meta = 0
        for row in rationale_rows:
            parsed = _parse_agent_rationale(row["agent_rationale"])
            if not parsed.get("decision_meta"):
                missing_meta += 1
            if parsed.get("parse_warning"):
                invalid_meta += 1
        rows["trade_proposals"] = {
            "total": _count_rows(db, "trade_proposals", cutoff),
            "missing_opportunity_id": _count_rows(
                db, "trade_proposals", cutoff, "(opportunity_id IS NULL OR opportunity_id = '')"
            ),
            "missing_agent_rationale": _count_rows(
                db, "trade_proposals", cutoff, "(agent_rationale IS NULL OR agent_rationale = '')"
            ),
            "missing_decision_meta_in_agent_rationale": missing_meta,
            "invalid_decision_meta_in_agent_rationale": invalid_meta,
        }
    if _table_exists(db, "shadow_trades"):
        rows["shadow_trades"] = {
            "total": _count_rows(db, "shadow_trades", cutoff),
            "missing_opportunity_id": _count_rows(
                db, "shadow_trades", cutoff, "(opportunity_id IS NULL OR opportunity_id = '')"
            ),
            "missing_pnl_pct": _count_rows(db, "shadow_trades", cutoff, "pnl_pct IS NULL"),
            "stale_missing_pnl_pct": _count_rows(
                db,
                "shadow_trades",
                cutoff,
                "pnl_pct IS NULL AND datetime(created_at) < datetime('now','-25 hours')",
            ),
        }
    if _table_exists(db, "universe_forward_outcomes"):
        rows["universe_forward_outcomes"] = {
            "total": _count_rows_by_time(db, "universe_forward_outcomes", "entry_ts", cutoff),
            "missing_member_id": _count_rows_by_time(
                db, "universe_forward_outcomes", "entry_ts", cutoff, "(member_id IS NULL OR member_id = '')"
            ),
            "missing_forward_return_pct": _count_rows_by_time(
                db,
                "universe_forward_outcomes",
                "entry_ts",
                cutoff,
                "status = 'settled' AND forward_return_pct IS NULL",
            ),
            "settled": _count_rows_by_time(db, "universe_forward_outcomes", "entry_ts", cutoff, "status = 'settled'"),
            "pending": _count_rows_by_time(db, "universe_forward_outcomes", "entry_ts", cutoff, "status = 'pending'"),
            "cancelled": _count_rows_by_time(db, "universe_forward_outcomes", "entry_ts", cutoff, "status = 'cancelled'"),
            "failed": _count_rows_by_time(db, "universe_forward_outcomes", "entry_ts", cutoff, "status = 'failed'"),
            "overdue_pending": _count_rows_by_time(
                db,
                "universe_forward_outcomes",
                "entry_ts",
                cutoff,
                "status = 'pending' AND datetime(due_at) <= datetime('now')",
            ),
        }
    return rows


def _group_summary(records: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[str(record.get(key) or "unknown")].append(record)
    rows = []
    for name, items in groups.items():
        shadow = [r.get("shadow_pnl_pct") for r in items if r.get("shadow_pnl_pct") is not None]
        actual = [r.get("actual_pnl_pct") for r in items if r.get("actual_pnl_pct") is not None]
        rows.append({
            "name": name,
            "n": len(items),
            "traded_count": sum(1 for r in items if r.get("was_traded")),
            "missed_alpha_count": sum(1 for r in items if r.get("verdict") == "missed_alpha"),
            "failed_traded_thesis_count": sum(1 for r in items if r.get("verdict") == "failed"),
            "poor_expression_count": sum(1 for r in items if r.get("verdict") == "poor_expression"),
            "avg_shadow_pnl_pct": _round(mean(shadow)) if shadow else None,
            "avg_actual_pnl_pct": _round(mean(actual)) if actual else None,
        })
    rows.sort(key=lambda r: (-int(r["n"]), r["name"]))
    return rows


def _top(records: list[dict], verdict: str, score_key: str, *, reverse: bool) -> list[dict]:
    rows = [r for r in records if r.get("verdict") == verdict and r.get(score_key) is not None]
    rows.sort(key=lambda r: float(r.get(score_key) or 0), reverse=reverse)
    return rows[:5]


def _main_finding(report: dict) -> str:
    summary = report.get("summary") or {}
    if summary.get("missed_alpha_count"):
        return f"{summary['missed_alpha_count']} skipped/watch thesis record(s) had positive resolved shadow outcome."
    if summary.get("poor_expression_count"):
        return f"{summary['poor_expression_count']} traded thesis record(s) underperformed their shadow batch."
    if summary.get("failed_traded_thesis_count"):
        return f"{summary['failed_traded_thesis_count']} traded thesis record(s) closed with negative outcome."
    if not summary.get("total_thesis_count"):
        return "No thesis lineage records were found in this window."
    return "No obvious thesis lineage failure pattern in this window."


def _next_action(report: dict) -> str:
    summary = report.get("summary") or {}
    if summary.get("missed_alpha_count"):
        return "Inspect top missed-alpha records before changing ranker, prompt, or sizing."
    if summary.get("poor_expression_count") or summary.get("failed_traded_thesis_count"):
        return "Compare observation thesis, decision rationale, and counterfactual batch before changing entries or exits."
    return "Keep collecting outcomes; do not change trading behavior from this audit alone."


def _append_top(lines: list[str], title: str, records: list[dict], metric: str) -> None:
    lines.extend(["", f"{title}:"])
    if not records:
        lines.append("  n/a")
        return
    for r in records[:5]:
        lines.append(
            f"  {r.get('lineage_id')} {r.get('symbol')} {r.get('direction')} "
            f"{metric}={_fmt_pct(r.get(metric))} verdict={r.get('verdict')}"
        )


def _failure_bucket(verdict: str, risk: dict | None, position: dict | None) -> str | None:
    if verdict == "missed_alpha":
        return "skipped_positive_counterfactual"
    if verdict == "poor_expression":
        return "execution_or_expression_underperformed_batch"
    if verdict == "failed":
        return position.get("close_reason") if position else "negative_actual_pnl"
    if risk and _bool_or_none(risk.get("approved")) is False:
        return "risk_rejected"
    return None


def _actual_pnl(position: dict | None) -> float | None:
    if not position:
        return None
    realized = _first_number(position.get("realized_pnl"))
    if (position.get("status") or "").lower() == "open":
        return _round(
            (realized or 0.0)
            + (_first_number(position.get("unrealized_pnl")) or 0.0)
        )
    return _round(realized)


def _notional(position: dict | None, proposal: dict | None, order: dict | None, fill: dict | None) -> float | None:
    if position:
        entry = _first_number(position.get("entry_price"))
        qty = _first_number(position.get("quantity"))
        if entry is not None and qty is not None:
            return _round(abs(entry * qty))
    for row in (proposal, order):
        if row:
            price = _first_number(row.get("price"))
            qty = _first_number(row.get("quantity"))
            if price is not None and qty is not None:
                return _round(abs(price * qty))
    if fill:
        price = _first_number(fill.get("fill_price"))
        qty = _first_number(fill.get("fill_quantity"))
        if price is not None and qty is not None:
            return _round(abs(price * qty))
    return None


def _pnl_pct(pnl: float | None, notional: float | None) -> float | None:
    if pnl is None or not notional:
        return None
    return _round((pnl / abs(notional)) * 100.0)


def _key_observations(root: dict) -> list:
    if root.get("key_observations"):
        parsed = _json_list(root.get("key_observations"))
        return parsed if parsed else [str(root["key_observations"])]
    return []


def _serenity_fields(root: dict) -> dict:
    return {
        "action_intent": root.get("action_intent"),
        "stage": root.get("stage"),
        "status": root.get("status"),
        "verdict_json": _json_obj(root.get("verdict_json")),
        "assessed_tier": root.get("assessed_tier"),
        "researchability_grade": root.get("researchability_grade"),
    }


def _risk_flags(member: dict | None) -> list:
    return _json_list(member.get("risk_flags") if member else None)


def _older_than_hours(raw: str | None, hours: int) -> bool:
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt <= _utcnow() - timedelta(hours=hours)


def _is_filled_order(order: dict) -> bool:
    return (order.get("status") or "").lower() in {"filled", "executed"} or bool(order.get("filled_at"))


def _open_db():
    import aletheia.storage.sqlite as sqlite_store

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return None, ["Database is not initialized."]
    try:
        return sqlite_store.get_db(), []
    except sqlite3.OperationalError as exc:
        return None, [f"Trading database could not be opened: {exc}"]


def _window(days: int) -> dict:
    days = max(1, int(days or 7))
    return {"days": days, "cutoff": (_utcnow() - timedelta(days=days)).isoformat()}


def _base_report(generated_at: str, window: dict) -> dict:
    return {
        "generated_at": generated_at,
        "window": window,
        "summary": {
            "total_thesis_count": 0,
            "traded_count": 0,
            "skipped_watch_count": 0,
            "pending_count": 0,
            "missed_alpha_count": 0,
            "failed_traded_thesis_count": 0,
            "poor_expression_count": 0,
        },
        "records": [],
        "data_completeness": {
            "total_records": 0,
            "chain_counts": {},
            "missing_counts": {},
            "missing_rates": {},
        },
        "raw_table_completeness": {},
        "by_scanner_family": [],
        "by_source": [],
        "top_missed_alpha": [],
        "top_failed_traded_thesis": [],
        "top_poor_expression": [],
        "main_finding": "Not enough thesis lineage evidence yet.",
        "next_action": "Collect more lineage evidence before changing behavior.",
        "schema_errors": [],
    }


def _table_exists(db, table: str) -> bool:
    row = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _table_columns(db, table: str) -> set[str]:
    if not _table_exists(db, table):
        return set()
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _time_column(db, table: str) -> str | None:
    cols = _table_columns(db, table)
    for col in ("created_at", "opened_at", "settled_at", "resolved_at", "entry_ts"):
        if col in cols:
            return col
    return None


def _where_for_window(db, table: str, cutoff: str, extra: str | None = None) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    time_col = _time_column(db, table)
    if time_col:
        clauses.append(f"datetime({time_col}) >= datetime(?)")
        params.append(cutoff)
    if extra:
        clauses.append(f"({extra})")
    return (" WHERE " + " AND ".join(clauses), params) if clauses else ("", params)


def _count_rows(db, table: str, cutoff: str, extra: str | None = None) -> int:
    where, params = _where_for_window(db, table, cutoff, extra)
    row = db.execute(f"SELECT COUNT(*) FROM {table}{where}", params).fetchone()
    return int(row[0] if row else 0)


def _count_rows_by_time(db, table: str, time_col: str, cutoff: str, extra: str | None = None) -> int:
    clauses = [f"datetime({time_col}) >= datetime(?)"]
    params: list[Any] = [cutoff]
    if extra:
        clauses.append(f"({extra})")
    row = db.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()
    return int(row[0] if row else 0)


def _count_pi_proposal_join_rows(db, cutoff: str, extra: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM pi_decisions pd "
        "JOIN trade_proposals tp ON tp.id = pd.proposal_id "
        "WHERE datetime(pd.created_at) >= datetime(?) AND (" + extra + ")",
        (cutoff,),
    ).fetchone()
    return int(row[0] if row else 0)


def _select_rows(db, table: str, cutoff: str, extra: str | None = None, *, columns: str = "*") -> list:
    where, params = _where_for_window(db, table, cutoff, extra)
    return db.execute(f"SELECT {columns} FROM {table}{where}", params).fetchall()


def _select_in(db, sql_template: str, values: list[Any], *, chunk_size: int = 800):
    unique = list(dict.fromkeys(str(v) for v in values if v is not None))
    for idx in range(0, len(unique), chunk_size):
        chunk = unique[idx:idx + chunk_size]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        yield db.execute(sql_template.format(placeholders), tuple(chunk)).fetchall()


def _is_newer(row: dict, current: dict | None, key: str) -> bool:
    return current is None or str(row.get(key) or "") > str(current.get(key) or "")


def _shadow_better(row: dict, current: dict | None) -> bool:
    if current is None:
        return True
    row_key = (1 if _bool_or_none(row.get("was_selected")) else 0, -int(_first_number(row.get("rank")) or 999999), str(row.get("created_at") or ""))
    cur_key = (1 if _bool_or_none(current.get("was_selected")) else 0, -int(_first_number(current.get("rank")) or 999999), str(current.get("created_at") or ""))
    return row_key > cur_key


def _forward_better(row: dict, current: dict | None) -> bool:
    if current is None:
        return True
    priority = {"24h": 4, "4h": 3, "1h": 2, "15m": 1}
    row_key = (priority.get(str(row.get("horizon")), 0), str(row.get("settled_at") or ""))
    cur_key = (priority.get(str(current.get("horizon")), 0), str(current.get("settled_at") or ""))
    return row_key > cur_key


def _json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (TypeError, ValueError):
            return []
    return []


def _json_obj(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _coerce_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _first_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return _round(float(numerator) / float(denominator))


def _fmt_pct(value: Any) -> str:
    num = _first_number(value)
    return "n/a" if num is None else f"{num:+.2f}%"


def _fmt_rate(value: Any) -> str:
    num = _first_number(value)
    return "n/a" if num is None else f"{num:.1%}"


def _window_label(window: dict) -> str:
    return f"{window.get('days', 7)}d"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
