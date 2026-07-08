"""Read-only Opportunity Flow Ledger.

This module reconstructs an opportunity lifecycle from already persisted
opportunity, decision, proposal, risk, order, position, shadow, and forward
outcome records. It does not generate thesis text, call LLMs, call rankers,
call exchanges, or mutate trading state.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.analytics.debate_impact import build_debate_impact_audit
from aletheia.analytics.sizing_trace import build_sizing_trace_audit, compact_sizing_trace_summary
from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit, compact_thesis_lineage_summary
from aletheia.analytics.trade_funnel import build_trade_funnel_audit

_FORWARD_HORIZONS = ("15m", "1h", "4h", "24h")
_TRADE_ACTIONS = {"trade", "scale_in", "flip"}


def build_opportunity_flow_ledger(
    *,
    hours: int = 24,
    limit: int = 200,
    stage: str | None = None,
    verdict: str | None = None,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable read-only opportunity lifecycle ledger."""
    hours_i = max(1, int(hours or 24))
    limit_i = max(1, int(limit or 200))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_i)).isoformat()
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    filters = _filters(stage=stage, verdict=verdict, symbol=symbol)
    db, schema_errors = _open_db()
    if db is None:
        return _empty_report(generated_at, hours_i, cutoff, schema_errors, filters)

    integrated = _integrated_audits(hours_i)
    context = _context_from_audits(integrated)
    search_limit = _search_limit(limit_i, filters)
    opportunities = _recent_opportunities(db, cutoff, search_limit, symbol_filter=filters.get("symbol"))
    unfiltered_records = [_build_record(db, opp, context) for opp in opportunities]
    records = _filter_records(unfiltered_records, filters, limit_i)

    final_states = Counter(row["final_state"] for row in records)
    final_stages = Counter(row["final_stage"] for row in records)
    drop_stages = Counter(row["final_stage"] for row in records if row["final_state"] in {"dropped", "rejected"})
    debate_summary = _debate_summary(db, cutoff, integrated)

    return {
        "generated_at": generated_at,
        "window": {"hours": hours_i, "cutoff": cutoff},
        "filters": filters,
        "read_only": True,
        "schema_errors": schema_errors,
        "notes": [
            "No new LLM calls: thesis/reasoning is consumed from upstream persisted artifacts only.",
            "No ranker, guard, debate, risk, proposal, execution, or position behavior was invoked or changed.",
            "Missing legacy evidence is reported as insufficient_data instead of reconstructed after the fact.",
        ],
        "summary": {
            "total_opportunities": len(records),
            "source_opportunities": len(unfiltered_records),
            "final_state_counts": dict(final_states),
            "final_stage_counts": dict(final_stages),
            "drop_stage_counts": dict(drop_stages),
            "main_drop_stages": _top_counts(drop_stages),
            "trade_zero_explanation": _trade_zero_explanation(integrated, drop_stages),
            "debate": debate_summary,
        },
        "integrated_audits": integrated,
        "opportunities": records,
    }


def trace_opportunity_flow(opportunity_id: str) -> dict[str, Any]:
    """Trace one opportunity by id without changing any state."""
    db, schema_errors = _open_db()
    if db is None:
        return _missing_trace(opportunity_id, "; ".join(schema_errors) or "Database is not initialized.")
    opp = _opportunity_by_id(db, opportunity_id)
    if not opp:
        return _missing_trace(opportunity_id, "Opportunity row was not found.")
    record = _build_record(db, opp, {})
    record["found"] = True
    return record


def _empty_report(
    generated_at: str,
    hours: int,
    cutoff: str,
    schema_errors: list[str],
    filters: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "window": {"hours": hours, "cutoff": cutoff},
        "filters": filters or {},
        "read_only": True,
        "schema_errors": schema_errors,
        "notes": [
            "No new LLM calls: thesis/reasoning is consumed from upstream persisted artifacts only.",
            "No ranker, guard, debate, risk, proposal, execution, or position behavior was invoked or changed.",
            "Missing legacy evidence is reported as insufficient_data instead of reconstructed after the fact.",
        ],
        "summary": {
            "total_opportunities": 0,
            "source_opportunities": 0,
            "final_state_counts": {},
            "final_stage_counts": {},
            "drop_stage_counts": {},
            "main_drop_stages": [],
            "trade_zero_explanation": {
                "stage": "insufficient_data",
                "summary": "No local trading database is available.",
            },
            "debate": _empty_debate_summary(),
        },
        "integrated_audits": {},
        "opportunities": [],
    }


def _missing_trace(opportunity_id: str, reason: str) -> dict[str, Any]:
    return {
        "opportunity_id": opportunity_id,
        "found": False,
        "final_state": "missing",
        "final_stage": "insufficient_data",
        "final_reason": reason,
        "timeline": [],
        "counterfactual": {"verdict": "insufficient_data", "shadow": None, "forward": {}},
    }


def render_flow_audit(report: dict[str, Any]) -> str:
    """Render the ledger audit for humans."""
    summary = report.get("summary") or {}
    window = report.get("window") or {}
    lines = [
        "Opportunity Flow Ledger",
        "",
        f"Window: {window.get('hours', '?')}h",
        f"Opportunities: {summary.get('total_opportunities', 0)}",
        "",
    ]
    filters = report.get("filters") or {}
    if any(filters.get(key) for key in ("stage", "verdict", "symbol")):
        lines.extend([
            "Filters:",
            f"  stage: {filters.get('stage') or 'any'}",
            f"  verdict: {filters.get('verdict') or 'any'}",
            f"  symbol: {filters.get('symbol') or 'any'}",
            f"  source_opportunities_before_filter: {summary.get('source_opportunities', 0)}",
            "",
        ])
    if report.get("schema_errors"):
        lines.append("Evidence:")
        for err in report["schema_errors"]:
            lines.append(f"  {err}")
        lines.append("")
    lines.append("Final states:")
    lines.extend(_render_counts(summary.get("final_state_counts")))
    lines.extend(["", "Main drop stages:"])
    rows = summary.get("main_drop_stages") or []
    if not rows:
        lines.append("  n/a")
    for row in rows:
        lines.append(f"  {row.get('stage')}: {row.get('count')}")
    explanation = summary.get("trade_zero_explanation") or {}
    lines.extend([
        "",
        "Trade-zero explanation:",
        f"  {explanation.get('summary', 'insufficient_data')}",
        "",
        "Integrated audit signals:",
    ])
    audits = report.get("integrated_audits") or {}
    funnel = (audits.get("trade_funnel") or {}).get("main_choke_point") or {}
    thesis = audits.get("thesis_lineage") or {}
    debate = audits.get("debate_impact") or {}
    sizing = audits.get("sizing_trace") or {}
    supply = audits.get("candidate_supply") or {}
    supply_latest = supply.get("latest_snapshot") or {}
    supply_drought = supply.get("drought") or {}
    lines.append(f"  trade_funnel: {funnel.get('stage', 'insufficient_data')}")
    lines.append(f"  debate_impact: {debate.get('verdict', 'insufficient_data')}")
    lines.append(f"  thesis_lineage: missed={thesis.get('missed_alpha_count', 0)} poor={thesis.get('poor_expression_count', 0)}")
    lines.append(f"  sizing_trace: {(sizing.get('main_sizing_bottleneck') or {}).get('reason', 'insufficient_data')}")
    lines.append(
        "  candidate_supply: "
        f"executable={supply_latest.get('executable_count', 'n/a')} "
        f"main_reason={supply_latest.get('main_reason', 'n/a')} "
        f"drought={'yes' if supply_drought else 'no'}"
    )
    debate_summary = summary.get("debate") or {}
    trace_flow = debate_summary.get("trace_flow") or {}
    decision_flow = debate_summary.get("decision_flow") or {}
    lines.extend([
        "",
        "Debate signal:",
        f"  debated_decisions: {decision_flow.get('debated_decisions', 0)}",
        f"  debated_skips: {decision_flow.get('debated_skips', 0)}",
        f"  trade_like_decisions: {decision_flow.get('trade_like_decisions', 0)}",
        f"  trace_complete: {trace_flow.get('trace_complete_decisions', 0)}",
        f"  trace_missing: {trace_flow.get('trace_missing_decisions', 0)}",
        f"  missed_alpha_suspect: {(debate_summary.get('verdict_counts') or {}).get('missed_alpha_suspect', 0)}",
        f"  next_needed: {debate_summary.get('next_needed', 'collect_more_evidence')}",
    ])
    lines.extend(["", "Recent opportunities:"])
    records = report.get("opportunities") or []
    if not records:
        lines.append("  n/a")
    for row in records[:30]:
        lines.append(
            "  "
            f"{row.get('opportunity_id')} {row.get('symbol') or 'unknown'} "
            f"{row.get('direction') or 'unknown'} "
            f"state={row.get('final_state')} stage={row.get('final_stage')} "
            f"reason={row.get('final_reason')}"
        )
    if len(records) > 30:
        lines.append(f"  ... {len(records) - 30} more")
    return "\n".join(lines)


def render_flow_trace(record: dict[str, Any]) -> str:
    """Render one opportunity lifecycle."""
    lines = [
        "Opportunity Flow Trace",
        "",
        f"Opportunity: {record.get('opportunity_id')}",
        f"Found: {record.get('found', True)}",
        f"Symbol: {record.get('symbol') or 'unknown'}",
        f"Direction: {record.get('direction') or 'unknown'}",
        f"Final: {record.get('final_state')} / {record.get('final_stage')}",
        f"Reason: {record.get('final_reason')}",
        "",
        "Timeline:",
    ]
    timeline = record.get("timeline") or []
    if not timeline:
        lines.append("  n/a")
    for event in timeline:
        at = event.get("at") or "unknown_time"
        details = event.get("details") or {}
        detail_s = _compact_details(details)
        suffix = f" ({detail_s})" if detail_s else ""
        lines.append(
            f"  {at}  {event.get('stage')}  status={event.get('status')} "
            f"reason={event.get('reason') or 'n/a'}{suffix}"
        )
    cf = record.get("counterfactual") or {}
    lines.extend([
        "",
        "Counterfactual:",
        f"  verdict: {cf.get('verdict', 'insufficient_data')}",
        f"  shadow: {_fmt_shadow(cf.get('shadow'))}",
        f"  forward: {_fmt_forward(cf.get('forward') or {})}",
    ])
    return "\n".join(lines)


def _integrated_audits(hours: int) -> dict[str, Any]:
    audits: dict[str, Any] = {}
    try:
        audits["trade_funnel"] = build_trade_funnel_audit(hours=hours, include_ranker_snapshot=False)
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        audits["trade_funnel"] = {"error": str(exc)}
    try:
        audits["debate_impact"] = _compact_debate_impact(build_debate_impact_audit(hours=hours))
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        audits["debate_impact"] = {"error": str(exc), "debated_opportunities": []}
    try:
        audits["thesis_lineage"] = compact_thesis_lineage_summary(build_thesis_lineage_audit(days=max(1, (hours + 23) // 24)))
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        audits["thesis_lineage"] = {"error": str(exc)}
    try:
        audits["sizing_trace"] = compact_sizing_trace_summary(build_sizing_trace_audit(hours=hours))
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        audits["sizing_trace"] = {"error": str(exc)}
    try:
        from aletheia.storage.sqlite import get_db
        from aletheia.analytics.candidate_supply import (
            evaluate_candidate_supply_drought,
            latest_candidate_supply_snapshot,
        )

        db = get_db()
        rows = db.execute(
            "SELECT * FROM agent_shell_events "
            "ORDER BY created_at DESC, rowid DESC LIMIT 20",
        ).fetchall()
        events = [
            {**dict(r), "payload": json.loads(r["payload"]) if r["payload"] else {}}
            for r in rows
        ]
        audits["candidate_supply"] = {
            "latest_snapshot": latest_candidate_supply_snapshot(events),
            "drought": evaluate_candidate_supply_drought(events),
        }
    except Exception as exc:  # pragma: no cover - defensive read-only boundary
        audits["candidate_supply"] = {"error": str(exc), "latest_snapshot": None, "drought": None}
    return audits


def _open_db() -> tuple[Any | None, list[str]]:
    import aletheia.storage.sqlite as sqlite_store

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        return None, ["Database is not initialized."]
    try:
        return sqlite_store.get_db(), []
    except sqlite3.OperationalError as exc:
        return None, [f"Trading database could not be opened: {exc}"]


def _compact_debate_impact(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "verdict": report.get("verdict"),
        "decision_flow": report.get("decision_flow"),
        "outcome_summary": report.get("outcome_summary"),
        "verdict_counts": report.get("verdict_counts"),
        "debated_opportunities": report.get("debated_opportunities") or [],
    }


def _debate_summary(db, cutoff: str, integrated: dict[str, Any]) -> dict[str, Any]:
    debate = integrated.get("debate_impact") or {}
    decision_flow = debate.get("decision_flow") or {}
    trace_flow = _debate_trace_flow(db, cutoff)
    verdict_counts = debate.get("verdict_counts") or {}
    return {
        "verdict": debate.get("verdict", "insufficient_data"),
        "decision_flow": decision_flow,
        "trace_flow": trace_flow,
        "verdict_counts": verdict_counts,
        "outcome_summary": debate.get("outcome_summary") or {},
        "next_needed": _debate_next_needed(decision_flow, trace_flow, verdict_counts),
    }


def _empty_debate_summary() -> dict[str, Any]:
    return {
        "verdict": "insufficient_data",
        "decision_flow": {},
        "trace_flow": {
            "debated_decisions": 0,
            "trace_complete_decisions": 0,
            "trace_missing_decisions": 0,
            "avg_initial_conviction": None,
            "avg_final_conviction": None,
            "avg_adjustment": None,
        },
        "verdict_counts": {},
        "outcome_summary": {},
        "next_needed": "collect_more_evidence",
    }


def _debate_trace_flow(db, cutoff: str) -> dict[str, Any]:
    base = _empty_debate_summary()["trace_flow"]
    if not _table_exists(db, "pi_decisions"):
        return base
    if not all(
        _has_column(db, "pi_decisions", col)
        for col in (
            "debate_initial_conviction",
            "debate_bull_confidence",
            "debate_bear_confidence",
            "debate_adjustment",
        )
    ):
        return base
    row = db.execute(
        "SELECT "
        "COUNT(*) AS debated_decisions, "
        "SUM(CASE WHEN debate_initial_conviction IS NOT NULL "
        "AND debate_bull_confidence IS NOT NULL "
        "AND debate_bear_confidence IS NOT NULL "
        "AND debate_adjustment IS NOT NULL THEN 1 ELSE 0 END) AS trace_complete_decisions, "
        "SUM(CASE WHEN debate_initial_conviction IS NULL "
        "OR debate_bull_confidence IS NULL "
        "OR debate_bear_confidence IS NULL "
        "OR debate_adjustment IS NULL THEN 1 ELSE 0 END) AS trace_missing_decisions, "
        "AVG(debate_initial_conviction) AS avg_initial_conviction, "
        "AVG(conviction) AS avg_final_conviction, "
        "AVG(debate_adjustment) AS avg_adjustment "
        "FROM pi_decisions "
        "WHERE debate_ran = 1 AND datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchone()
    if not row:
        return base
    d = dict(row)
    return {
        "debated_decisions": int(d.get("debated_decisions") or 0),
        "trace_complete_decisions": int(d.get("trace_complete_decisions") or 0),
        "trace_missing_decisions": int(d.get("trace_missing_decisions") or 0),
        "avg_initial_conviction": _round(_to_float(d.get("avg_initial_conviction"))),
        "avg_final_conviction": _round(_to_float(d.get("avg_final_conviction"))),
        "avg_adjustment": _round(_to_float(d.get("avg_adjustment"))),
    }


def _debate_next_needed(
    decision_flow: dict[str, Any],
    trace_flow: dict[str, Any],
    verdict_counts: dict[str, Any],
) -> str:
    debated = int(decision_flow.get("debated_decisions") or trace_flow.get("debated_decisions") or 0)
    complete = int(trace_flow.get("trace_complete_decisions") or 0)
    missed = int(verdict_counts.get("missed_alpha_suspect") or 0)
    if debated <= 0:
        return "collect_more_debate_samples"
    if complete <= 0:
        return "wait_for_post_migration_debate_samples"
    if missed > 0:
        return "inspect_debate_adjustments_before_tuning"
    return "monitor_debate_calibration"


def _context_from_audits(audits: dict[str, Any]) -> dict[str, Any]:
    debate_rows = (audits.get("debate_impact") or {}).get("debated_opportunities") or []
    return {
        "debate_by_opp": {row.get("opportunity_id"): row for row in debate_rows if row.get("opportunity_id")},
    }


def _filters(*, stage: str | None, verdict: str | None, symbol: str | None) -> dict[str, str | None]:
    return {
        "stage": _clean_filter(stage),
        "verdict": _clean_filter(verdict),
        "symbol": _clean_symbol(symbol),
    }


def _clean_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_symbol(value: str | None) -> str | None:
    text = str(value or "").strip().upper()
    return text or None


def _search_limit(limit: int, filters: dict[str, str | None]) -> int:
    if any(filters.get(key) for key in ("stage", "verdict", "symbol")):
        return min(max(limit * 20, 1000), 5000)
    return limit


def _filter_records(
    records: list[dict[str, Any]],
    filters: dict[str, str | None],
    limit: int,
) -> list[dict[str, Any]]:
    stage = filters.get("stage")
    verdict = filters.get("verdict")
    symbol = filters.get("symbol")
    out: list[dict[str, Any]] = []
    for row in records:
        if symbol and str(row.get("symbol") or "").upper() != symbol:
            continue
        if verdict and str((row.get("counterfactual") or {}).get("verdict") or "") != verdict:
            continue
        if stage and not _record_has_stage(row, stage):
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _record_has_stage(row: dict[str, Any], stage: str) -> bool:
    if row.get("final_stage") == stage:
        return True
    return any(event.get("stage") == stage for event in row.get("timeline") or [])


def _recent_opportunities(
    db,
    cutoff: str,
    limit: int,
    *,
    symbol_filter: str | None = None,
) -> list[dict[str, Any]]:
    if not _table_exists(db, "opportunities"):
        return []
    where = ["datetime(created_at) >= datetime(?)"]
    params: list[Any] = [cutoff]
    if symbol_filter:
        where.append("upper(symbol) = ?")
        params.append(symbol_filter)
    params.append(limit)
    rows = db.execute(
        f"SELECT * FROM opportunities WHERE {' AND '.join(where)} "
        "ORDER BY datetime(created_at) DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _opportunity_by_id(db, opportunity_id: str) -> dict[str, Any] | None:
    if not _table_exists(db, "opportunities"):
        return None
    row = db.execute("SELECT * FROM opportunities WHERE id = ? LIMIT 1", (opportunity_id,)).fetchone()
    return dict(row) if row else None


def _build_record(db, opp: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    opp_id = str(opp.get("id"))
    direction = _direction_from_bias(opp.get("direction_bias"))
    events: list[dict[str, Any]] = [
        _event(
            "created",
            "ok",
            opp.get("created_at"),
            "Opportunity was persisted.",
            {
                "thesis": opp.get("thesis") or "insufficient_data",
                "confidence": opp.get("confidence"),
                "confidence_score": opp.get("confidence_score"),
                "source_agent": opp.get("source_agent"),
                "scanner_family": opp.get("scanner_family"),
            },
        )
    ]

    member = _universe_member(db, opp.get("universe_member_id"))
    local_drop = _local_pre_trade_drop(opp, member, direction)
    if local_drop:
        events.append(_event(local_drop["stage"], "dropped", local_drop.get("at"), local_drop["reason"], local_drop))

    decisions = _decisions_for_opportunity(db, opp_id)
    for decision in decisions:
        events.extend(_decision_events(decision))

    proposals = _proposals_for_opportunity(db, opp_id)
    proposal_ids = [p.get("id") for p in proposals if p.get("id")]
    for proposal in proposals:
        events.append(
            _event(
                "proposal_created",
                "ok",
                proposal.get("created_at"),
                "Proposal was created from this opportunity.",
                {
                    "proposal_id": proposal.get("id"),
                    "status": proposal.get("status"),
                    "execution_mode": proposal.get("execution_mode"),
                },
            )
        )

    risks = _risk_decisions_for_proposals(db, proposal_ids)
    for risk in risks:
        approved = _truthy(risk.get("approved"))
        events.append(
            _event(
                "risk_approved" if approved else "risk_rejected",
                "ok" if approved else "rejected",
                risk.get("created_at"),
                risk.get("message") or ("Risk approved." if approved else "Risk rejected."),
                {
                    "risk_decision_id": risk.get("id"),
                    "proposal_id": risk.get("proposal_id"),
                    "risk_score": risk.get("risk_score"),
                    "violations": _json_list(risk.get("violations")),
                },
            )
        )

    orders = _orders_for_proposals(db, proposal_ids)
    for order in orders:
        executed = _order_executed(order)
        failed = _order_failed(order)
        stage = "order_executed" if executed else "order_failed" if failed else "order_created"
        events.append(
            _event(
                stage,
                "ok" if executed else "rejected" if failed else "pending",
                order.get("filled_at") or order.get("created_at"),
                order.get("status") or "Order persisted.",
                {
                    "order_id": order.get("id"),
                    "proposal_id": order.get("proposal_id"),
                    "status": order.get("status"),
                    "execution_mode": order.get("execution_mode"),
                },
            )
        )

    positions = _positions_for_opportunity(db, opp_id, proposal_ids, [o.get("id") for o in orders if o.get("id")])
    for position in positions:
        events.append(
            _event(
                "position_opened",
                "ok",
                position.get("opened_at"),
                "Position was opened.",
                {
                    "position_id": position.get("id"),
                    "status": position.get("status"),
                    "quantity": position.get("quantity"),
                    "entry_price": position.get("entry_price"),
                },
            )
        )
        if str(position.get("status") or "").lower() == "closed" or position.get("closed_at"):
            events.append(
                _event(
                    "position_closed",
                    "ok",
                    position.get("closed_at") or position.get("opened_at"),
                    position.get("close_reason") or "Position is closed.",
                    {
                        "position_id": position.get("id"),
                        "realized_pnl": position.get("realized_pnl"),
                    },
                )
            )

    counterfactual = _counterfactual(db, opp, direction)
    if counterfactual.get("shadow"):
        events.append(
            _event(
                "shadow_resolved",
                "ok",
                counterfactual["shadow"].get("resolved_at"),
                "Shadow trade outcome is available.",
                counterfactual["shadow"],
            )
        )
    for horizon, outcome in (counterfactual.get("forward") or {}).items():
        events.append(
            _event(
                "forward_outcome_resolved",
                "ok",
                outcome.get("settled_at"),
                f"{horizon} forward outcome is available.",
                {"horizon": horizon, **outcome},
            )
        )

    events = sorted(events, key=lambda e: _sort_ts(e.get("at")))
    final_state, final_stage, final_reason = _final_result(events)
    counterfactual["verdict"] = _counterfactual_verdict(counterfactual, final_state)

    return {
        "opportunity_id": opp_id,
        "symbol": opp.get("symbol"),
        "market": opp.get("market"),
        "direction": direction or "insufficient_data",
        "scanner_family": opp.get("scanner_family"),
        "created_at": opp.get("created_at"),
        "root": {
            "status": opp.get("status"),
            "source_agent": opp.get("source_agent"),
            "thesis": opp.get("thesis") or "insufficient_data",
            "confidence": opp.get("confidence"),
            "confidence_score": opp.get("confidence_score"),
            "universe_member_id": opp.get("universe_member_id"),
        },
        "final_state": final_state,
        "final_stage": final_stage,
        "final_reason": final_reason,
        "timeline": events,
        "counterfactual": counterfactual,
    }


def _universe_member(db, member_id: Any) -> dict[str, Any] | None:
    if not member_id or not _table_exists(db, "universe_members"):
        return None
    row = db.execute("SELECT * FROM universe_members WHERE id = ? LIMIT 1", (member_id,)).fetchone()
    return dict(row) if row else None


def _local_pre_trade_drop(
    opp: dict[str, Any], member: dict[str, Any] | None, direction: str | None
) -> dict[str, Any] | None:
    if not direction:
        return {"stage": "ranker_excluded", "reason": "neutral_or_missing_direction", "at": opp.get("created_at")}
    if not _truthy(opp.get("execution_eligible"), default=True):
        return {"stage": "ranker_excluded", "reason": "execution_eligible_false", "at": opp.get("created_at")}
    score = _to_float(opp.get("confidence_score"))
    if score is not None and score < 45:
        return {"stage": "ranker_excluded", "reason": "confidence_below", "confidence_score": score, "at": opp.get("created_at")}
    valid_until = opp.get("valid_until")
    if valid_until and _is_past(valid_until):
        return {"stage": "ttl_expired", "reason": "opportunity_valid_until_expired", "at": valid_until}
    if member:
        if not _truthy(member.get("execution_eligible"), default=True):
            return {"stage": "ranker_excluded", "reason": "universe_member_not_eligible", "at": opp.get("created_at")}
        if member.get("expires_at") and _is_past(member.get("expires_at")):
            return {"stage": "ttl_expired", "reason": "universe_member_expired", "at": member.get("expires_at")}
    return None


def _decisions_for_opportunity(db, opp_id: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "pi_decisions"):
        return []
    rows = db.execute(
        "SELECT * FROM pi_decisions WHERE opportunity_id = ? ORDER BY datetime(created_at) ASC",
        (opp_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _decision_events(decision: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    action = str(decision.get("action") or "").lower()
    reason = str(decision.get("reasoning") or "").strip() or "insufficient_data"
    details = {
        "decision_id": decision.get("id"),
        "action": action,
        "conviction": decision.get("conviction"),
        "reasoning": reason,
    }
    reason_l = reason.lower()
    if "okx_demo_symbol_not_supported" in reason or "unsupported" in reason_l:
        events.append(_event("venue_unsupported", "dropped", decision.get("created_at"), reason, details))
    if "guard_blocked:" in reason_l or "guard_blocked" in reason_l:
        events.append(_event("guard_blocked", "dropped", decision.get("created_at"), reason, details))
    if action == "signal_pending":
        events.append(_event("signal_pending", "pending", decision.get("created_at"), reason, details))
    elif action in _TRADE_ACTIONS:
        events.append(_event("operator_trade_intent", "ok", decision.get("created_at"), reason, details))
    elif action in {"skip", "watch"}:
        events.append(_event("operator_skip", "dropped", decision.get("created_at"), reason, details))
    if _truthy(decision.get("debate_ran")):
        trace = {
            "decision_id": decision.get("id"),
            "initial_conviction": decision.get("debate_initial_conviction"),
            "final_conviction": decision.get("conviction"),
            "bull_confidence": decision.get("debate_bull_confidence"),
            "bear_confidence": decision.get("debate_bear_confidence"),
            "adjustment": decision.get("debate_adjustment"),
            "trace_status": "ok" if decision.get("debate_initial_conviction") is not None else "insufficient_data",
        }
        events.append(
            _event(
                "debate_downgraded" if action == "skip" else "debate_recorded",
                "dropped" if action == "skip" else "ok",
                decision.get("created_at"),
                "Persisted debate trace." if trace["trace_status"] == "ok" else "Legacy debate row has no trace fields.",
                trace,
            )
        )
    return events


def _proposals_for_opportunity(db, opp_id: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "trade_proposals"):
        return []
    rows = db.execute(
        "SELECT * FROM trade_proposals WHERE opportunity_id = ? ORDER BY datetime(created_at) ASC",
        (opp_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _risk_decisions_for_proposals(db, proposal_ids: list[Any]) -> list[dict[str, Any]]:
    if not proposal_ids or not _table_exists(db, "risk_decisions"):
        return []
    return _rows_by_ids(db, "risk_decisions", "proposal_id", proposal_ids, "created_at")


def _orders_for_proposals(db, proposal_ids: list[Any]) -> list[dict[str, Any]]:
    if not proposal_ids or not _table_exists(db, "orders"):
        return []
    return _rows_by_ids(db, "orders", "proposal_id", proposal_ids, "created_at")


def _positions_for_opportunity(
    db, opp_id: str, proposal_ids: list[Any], order_ids: list[Any]
) -> list[dict[str, Any]]:
    if not _table_exists(db, "positions"):
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if _has_column(db, "positions", "opportunity_id"):
        clauses.append("opportunity_id = ?")
        params.append(opp_id)
    if proposal_ids:
        clauses.append(f"proposal_id IN ({','.join('?' for _ in proposal_ids)})")
        params.extend(proposal_ids)
    if order_ids:
        clauses.append(f"order_id IN ({','.join('?' for _ in order_ids)})")
        params.extend(order_ids)
    if not clauses:
        return []
    rows = db.execute(
        f"SELECT * FROM positions WHERE {' OR '.join(clauses)} ORDER BY datetime(opened_at) ASC",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def _rows_by_ids(db, table: str, column: str, ids: list[Any], order_col: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    clean = [x for x in ids if x]
    for idx in range(0, len(clean), 100):
        chunk = clean[idx : idx + 100]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY datetime({order_col}) ASC",
            tuple(chunk),
        ).fetchall()
        out.extend(dict(row) for row in rows)
    return out


def _counterfactual(db, opp: dict[str, Any], direction: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {"shadow": None, "forward": {}}
    if _table_exists(db, "shadow_trades"):
        row = db.execute(
            "SELECT * FROM shadow_trades WHERE opportunity_id = ? "
            "ORDER BY datetime(COALESCE(resolved_at, created_at)) DESC LIMIT 1",
            (opp.get("id"),),
        ).fetchone()
        if row:
            d = dict(row)
            out["shadow"] = {
                "pnl_pct": _round(_to_float(d.get("pnl_pct"))),
                "resolved_at": d.get("resolved_at"),
                "entry_price": d.get("entry_price"),
                "exit_price": d.get("exit_price"),
                "rank": d.get("rank"),
                "status": "resolved" if d.get("resolved_at") or d.get("pnl_pct") is not None else "pending",
            }
    member_id = opp.get("universe_member_id")
    if member_id and direction and _table_exists(db, "universe_forward_outcomes"):
        for horizon in _FORWARD_HORIZONS:
            row = db.execute(
                "SELECT * FROM universe_forward_outcomes WHERE member_id = ? AND horizon = ? "
                "AND status = 'settled' AND forward_return_pct IS NOT NULL "
                "ORDER BY datetime(settled_at) DESC LIMIT 1",
                (member_id, horizon),
            ).fetchone()
            if not row:
                continue
            d = dict(row)
            raw = _to_float(d.get("forward_return_pct"))
            out["forward"][horizon] = {
                "forward_return_pct": _round(raw),
                "direction_adjusted_return_pct": _round(_direction_adjusted(raw, direction)),
                "settled_at": d.get("settled_at"),
                "status": d.get("status"),
            }
    return out


def _final_result(events: list[dict[str, Any]]) -> tuple[str, str, str]:
    priority = [
        ("position_closed", "closed"),
        ("position_opened", "executed"),
        ("order_executed", "executed"),
        ("order_failed", "dropped"),
        ("risk_rejected", "rejected"),
        ("proposal_created", "proposed"),
        ("venue_unsupported", "dropped"),
        ("debate_downgraded", "dropped"),
        ("operator_skip", "dropped"),
        ("signal_pending", "pending"),
        ("guard_blocked", "dropped"),
        ("ttl_expired", "dropped"),
        ("ranker_excluded", "dropped"),
    ]
    for stage, state in priority:
        found = [event for event in events if event.get("stage") == stage]
        if found:
            event = found[-1]
            return state, stage, event.get("reason") or "insufficient_data"
    return "pending", "created", "No downstream lifecycle evidence is available yet."


def _counterfactual_verdict(counterfactual: dict[str, Any], final_state: str) -> str:
    if final_state in {"executed", "closed"}:
        return "executed_path"
    values: list[float] = []
    shadow = counterfactual.get("shadow")
    if shadow and shadow.get("pnl_pct") is not None:
        values.append(float(shadow["pnl_pct"]))
    for item in (counterfactual.get("forward") or {}).values():
        if item.get("direction_adjusted_return_pct") is not None:
            values.append(float(item["direction_adjusted_return_pct"]))
    if not values:
        return "insufficient_data"
    if any(value > 0 for value in values):
        return "missed_alpha_suspect"
    if all(value <= 0 for value in values):
        return "protective_or_correct"
    return "insufficient_data"


def _trade_zero_explanation(integrated: dict[str, Any], drop_stages: Counter[str]) -> dict[str, Any]:
    funnel = integrated.get("trade_funnel") or {}
    counts = funnel.get("counts") or {}
    trade_like = int(counts.get("decisions_trade") or 0) + int(counts.get("decisions_scale_in") or 0) + int(counts.get("decisions_flip") or 0)
    proposals = int(counts.get("proposals_created") or 0)
    if trade_like == 0 and int(counts.get("system_operator_evaluated") or 0) > 0:
        return {
            "stage": "system_operator_decision",
            "summary": "System Operator evaluated persisted signals but produced 0 trade-like decisions.",
            "evidence": funnel.get("main_choke_point"),
        }
    if proposals == 0 and drop_stages:
        stage, count = drop_stages.most_common(1)[0]
        return {"stage": stage, "summary": f"Most reconstructed drops ended at {stage} ({count})."}
    choke = funnel.get("main_choke_point") or {}
    return {
        "stage": choke.get("stage", "insufficient_data"),
        "summary": choke.get("summary", "insufficient_data"),
        "evidence": choke,
    }


def _event(
    stage: str,
    status: str,
    at: Any,
    reason: str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": status,
        "at": at,
        "reason": reason or "insufficient_data",
        "details": details or {},
    }


def _guard_reason(row: dict[str, Any]) -> str:
    guards = row.get("matched_guards") or []
    ids = [str(g.get("guard_id")) for g in guards if g.get("guard_id")]
    return f"Blocked by active guard(s): {', '.join(ids)}" if ids else "Blocked by active guard."


def _order_executed(order: dict[str, Any]) -> bool:
    status = str(order.get("status") or "").lower()
    return status in {"filled", "executed"} or bool(order.get("filled_at"))


def _order_failed(order: dict[str, Any]) -> bool:
    return str(order.get("status") or "").lower() in {"error", "failed", "rejected", "cancelled", "canceled"}


def _direction_from_bias(raw: Any) -> str | None:
    bias = str(raw or "").strip().lower()
    if bias in {"long", "bullish"}:
        return "long"
    if bias in {"short", "bearish"}:
        return "short"
    return None


def _direction_adjusted(value: float | None, direction: str) -> float | None:
    if value is None:
        return None
    return -value if direction == "short" else value


def _truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError):
        return [str(raw)] if str(raw).strip() else []
    return parsed if isinstance(parsed, list) else [parsed]


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _is_past(raw: Any) -> bool:
    try:
        text = str(raw).replace("Z", "+00:00")
        ts = datetime.fromisoformat(text)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts < datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False


def _sort_ts(raw: Any) -> str:
    return str(raw or "9999-12-31T23:59:59+00:00")


def _table_exists(db, table: str) -> bool:
    try:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    except sqlite3.OperationalError:
        return False
    return row is not None


def _has_column(db, table: str, column: str) -> bool:
    try:
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    return column in {row[1] for row in rows}


def _top_counts(counter: Counter[str], limit: int = 8) -> list[dict[str, Any]]:
    return [{"stage": stage, "count": count} for stage, count in counter.most_common(limit)]


def _render_counts(counts: dict[str, int] | None) -> list[str]:
    if not counts:
        return ["  n/a"]
    return [f"  {key}: {value}" for key, value in counts.items()]


def _compact_details(details: dict[str, Any]) -> str:
    keep = []
    for key in ("decision_id", "proposal_id", "risk_decision_id", "order_id", "position_id", "conviction", "trace_status"):
        if details.get(key) is not None:
            keep.append(f"{key}={details[key]}")
    return ", ".join(keep)


def _fmt_shadow(shadow: dict[str, Any] | None) -> str:
    if not shadow:
        return "n/a"
    pnl = shadow.get("pnl_pct")
    return f"pnl_pct={pnl}" if pnl is not None else f"status={shadow.get('status', 'pending')}"


def _fmt_forward(forward: dict[str, Any]) -> str:
    if not forward:
        return "n/a"
    parts = []
    for horizon in _FORWARD_HORIZONS:
        item = forward.get(horizon)
        if item and item.get("direction_adjusted_return_pct") is not None:
            parts.append(f"{horizon}={item['direction_adjusted_return_pct']:+.4f}%")
    return ", ".join(parts) if parts else "n/a"
