"""Read-only Trade Funnel Audit.

Answers where opportunity flow is lost before it becomes opened positions.
This module only reads local SQLite state. It does not submit orders, create
proposals, update opportunities, call agents, call LLMs, or call exchanges.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any


_COUNT_KEYS = (
    "opportunities_created",
    "open_opportunities",
    "ranker_candidates",
    "ranker_eligible",
    "signal_pending_written",
    "system_operator_evaluated",
    "decisions_trade",
    "decisions_watch",
    "decisions_skip",
    "decisions_scale_in",
    "decisions_flip",
    "proposals_created",
    "risk_approved",
    "risk_rejected",
    "orders_executed",
    "positions_opened",
)

_REQUIRED_TABLES = (
    "opportunities",
    "pi_decisions",
    "trade_proposals",
    "risk_decisions",
    "orders",
    "positions",
)

_TINY_POSITION_NOTIONAL_USDT = 5.0


def build_trade_funnel_audit(
    *,
    hours: int | None = 24,
    days: int | None = None,
    min_sample_size: int = 3,
    include_ranker_snapshot: bool = True,
) -> dict:
    """Build a JSON-serializable opportunity-to-position funnel report."""
    import aletheia.storage.sqlite as sqlite_store

    window = _window(hours=hours, days=days)
    generated_at = _utcnow().isoformat(timespec="seconds")
    report = _base_report(generated_at, window)

    if getattr(sqlite_store, "_db_conn", None) is None and not sqlite_store.db_path().exists():
        report["schema_errors"].append("Database is not initialized.")
        report["main_choke_point"] = _choke(
            "insufficient_data",
            "insufficient_data",
            "No local trading database is available.",
            "Start the system and collect opportunities/signals before auditing the funnel.",
        )
        return report

    try:
        db = sqlite_store.get_db()
    except sqlite3.OperationalError as exc:
        report["schema_errors"].append(str(exc))
        report["main_choke_point"] = _choke(
            "insufficient_data",
            "insufficient_data",
            f"Trading database could not be opened: {exc}",
            "Fix database availability before auditing trading flow.",
        )
        return report

    missing = [table for table in _REQUIRED_TABLES if not _table_exists(db, table)]
    if missing:
        report["schema_errors"].append(f"Missing required table(s): {', '.join(missing)}")

    cutoff = window["cutoff"]
    try:
        counts = report["counts"]
        if _table_exists(db, "opportunities"):
            counts["opportunities_created"] = _count(db, "opportunities", "created_at", cutoff)
            counts["open_opportunities"] = _count(
                db,
                "opportunities",
                "created_at",
                cutoff,
                "status = 'open'",
            )
        if _table_exists(db, "pi_decisions"):
            counts["signal_pending_written"] = _count(
                db,
                "pi_decisions",
                "created_at",
                cutoff,
                "action = 'signal_pending'",
            )
            for action, key in (
                ("trade", "decisions_trade"),
                ("watch", "decisions_watch"),
                ("skip", "decisions_skip"),
                ("scale_in", "decisions_scale_in"),
                ("flip", "decisions_flip"),
            ):
                counts[key] = _count(db, "pi_decisions", "created_at", cutoff, "action = ?", (action,))
        counts["system_operator_evaluated"] = sum(
            int(counts[key] or 0)
            for key in (
                "decisions_trade",
                "decisions_watch",
                "decisions_skip",
                "decisions_scale_in",
                "decisions_flip",
            )
        )
        if _table_exists(db, "trade_proposals"):
            counts["proposals_created"] = _count(db, "trade_proposals", "created_at", cutoff)
        if _table_exists(db, "risk_decisions"):
            counts["risk_approved"] = _count(db, "risk_decisions", "created_at", cutoff, "approved = 1")
            counts["risk_rejected"] = _count(db, "risk_decisions", "created_at", cutoff, "approved = 0")
        if _table_exists(db, "orders"):
            counts["orders_executed"] = _orders_executed(db, cutoff)
        if _table_exists(db, "positions"):
            counts["positions_opened"] = _count(db, "positions", "opened_at", cutoff)
        persisted_ranker = _persisted_ranker_counts(db, cutoff)
        if persisted_ranker["ranker_candidates"] is not None:
            counts.update(persisted_ranker)
        elif include_ranker_snapshot and _table_exists(db, "opportunities"):
            snapshot = _ranker_snapshot()
            counts["ranker_candidates"] = snapshot.get("ranker_candidates")
            counts["ranker_eligible"] = snapshot.get("ranker_eligible")
            if snapshot.get("error"):
                report["ranker_snapshot_error"] = snapshot["error"]

        # The raw counts above are event throughput in one clock window. They are
        # useful operationally but are not a conversion cohort: downstream rows
        # can belong to older opportunities and one opportunity can be evaluated
        # repeatedly. Build a distinct opportunity-ID cohort for all rates/dropoffs.
        cohort_counts = _causal_cohort_counts(db, cutoff)
        report["causal_cohort_counts"] = cohort_counts
        report["conversion_rates"] = _conversion_rates(cohort_counts)
        report["top_dropoffs"] = _top_dropoffs(cohort_counts)
        if _table_exists(db, "risk_decisions"):
            report["risk_rejection_reasons"] = _risk_rejection_reasons(db, cutoff)
        if _table_exists(db, "orders"):
            report["execution_error_reasons"] = _execution_error_reasons(db, cutoff)
        report["proposal_submission_reasons"] = _proposal_submission_reasons(
            db, cutoff, causal_cohort=False,
        )
        report["causal_proposal_submission_reasons"] = _proposal_submission_reasons(
            db, cutoff, causal_cohort=True,
        )
        if _table_exists(db, "positions"):
            report["position_stats"] = _position_stats(db, cutoff)
        report["main_choke_point"] = _main_choke_point(
            cohort_counts,
            report["position_stats"],
            min_sample_size,
            report["causal_proposal_submission_reasons"],
        )
    except sqlite3.OperationalError as exc:
        report["schema_errors"].append(str(exc))
        report["main_choke_point"] = _choke(
            "insufficient_data",
            "insufficient_data",
            f"Required funnel evidence is unavailable: {exc}",
            "Run migrations or collect more trading evidence before interpreting the funnel.",
        )
    return report


def render_trade_funnel_audit(report: dict) -> str:
    """Render the report for the standalone CLI."""
    window = report.get("window") or {}
    lines = ["Trade Funnel Audit", "", f"Window: {_window_label(window)}", ""]
    if report.get("schema_errors"):
        lines.append("Evidence:")
        for err in report["schema_errors"]:
            lines.append(f"  {err}")
        lines.append("")
    lines.append("Event-volume counts (same clock window; not conversion rates):")
    counts = report.get("counts") or {}
    for key in (
        "opportunities_created",
        "ranker_candidates",
        "ranker_eligible",
        "signal_pending_written",
        "system_operator_evaluated",
        "decisions_trade",
        "decisions_watch",
        "decisions_skip",
        "proposals_created",
        "risk_approved",
        "risk_rejected",
        "orders_executed",
        "positions_opened",
    ):
        value = counts.get(key)
        lines.append(f"  {key}: {'n/a' if value is None else value}")
    lines.extend(["", "Causal opportunity cohort (used for conversion rates):"])
    cohort = report.get("causal_cohort_counts") or {}
    for key in (
        "opportunities_created",
        "ranker_candidates",
        "ranker_eligible",
        "signal_pending_written",
        "system_operator_evaluated",
        "operator_deferred",
        "decisions_trade",
        "decisions_watch",
        "decisions_skip",
        "proposals_created",
        "risk_approved",
        "risk_rejected",
        "orders_executed",
        "positions_opened",
    ):
        value = cohort.get(key)
        lines.append(f"  {key}: {'n/a' if value is None else value}")
    submission_reasons = report.get("causal_proposal_submission_reasons") or []
    if submission_reasons:
        lines.extend(["", "Pre-proposal rejection reasons:"])
        for item in submission_reasons:
            lines.append(f"  {item['reason']}: {item['count']}")
    stats = report.get("position_stats") or {}
    if stats.get("sample_size"):
        lines.extend([
            "",
            "Position stats:",
            f"  median_notional_usdt: {_fmt_num(stats.get('median_notional_usdt'))}",
            f"  avg_notional_usdt: {_fmt_num(stats.get('avg_notional_usdt'))}",
            f"  median_leverage: {_fmt_num(stats.get('median_leverage'))}",
        ])
    choke = report.get("main_choke_point") or {}
    lines.extend([
        "",
        "Main choke point:",
        f"  {choke.get('summary', 'insufficient data')}",
        "",
        "Next action:",
        f"  {choke.get('next_action', 'Collect more evidence before changing behavior.')}",
    ])
    return "\n".join(lines)


def compact_trade_funnel_summary(report: dict) -> dict:
    """Small object suitable for doctor JSON output."""
    counts = report.get("counts") or {}
    has_evidence = any(int(counts.get(key) or 0) > 0 for key in (
        "opportunities_created",
        "signal_pending_written",
        "system_operator_evaluated",
        "proposals_created",
        "risk_approved",
        "risk_rejected",
        "orders_executed",
        "positions_opened",
    ))
    return {
        "available": has_evidence or not bool(report.get("schema_errors")),
        "reason": "; ".join(report.get("schema_errors") or []),
        "main_choke_point": report.get("main_choke_point"),
        "counts": report.get("counts"),
        "causal_cohort_counts": report.get("causal_cohort_counts"),
        "proposal_submission_reasons": report.get("proposal_submission_reasons"),
        "causal_proposal_submission_reasons": report.get(
            "causal_proposal_submission_reasons"
        ),
        "position_stats": report.get("position_stats"),
    }


def _base_report(generated_at: str, window: dict) -> dict:
    counts = _empty_counts()
    return {
        "generated_at": generated_at,
        "window": window,
        "counts": counts,
        "count_semantics": {
            "counts": "independent event rows in the clock window; operational throughput only",
            "causal_cohort_counts": (
                "distinct opportunities created in the window, joined downstream by opportunity_id"
            ),
            "conversion_rates": "computed only from causal_cohort_counts",
        },
        "causal_cohort_counts": _empty_counts(),
        "conversion_rates": {
            "opportunity_to_signal_pending": None,
            "signal_pending_to_evaluated": None,
            "evaluated_to_trade_decision": None,
            "trade_decision_to_proposal": None,
            "proposal_to_risk_approved": None,
            "risk_approved_to_executed": None,
            "proposal_to_position": None,
        },
        "top_dropoffs": [],
        "risk_rejection_reasons": [],
        "proposal_submission_reasons": [],
        "causal_proposal_submission_reasons": [],
        "execution_error_reasons": [],
        "position_stats": {
            "sample_size": 0,
            "avg_notional_usdt": None,
            "median_notional_usdt": None,
            "max_notional_usdt": None,
            "avg_leverage": None,
            "median_leverage": None,
        },
        "main_choke_point": _choke(
            "insufficient_data",
            "insufficient_data",
            "Not enough trading evidence to identify a choke point.",
            "Collect opportunities, decisions, proposals, and executions before tuning behavior.",
        ),
        "schema_errors": [],
    }


def _empty_counts() -> dict[str, int | None]:
    counts: dict[str, int | None] = {key: 0 for key in _COUNT_KEYS}
    counts["ranker_candidates"] = None
    counts["ranker_eligible"] = None
    return counts


def _window(*, hours: int | None, days: int | None) -> dict:
    if days is not None:
        lookback_hours = max(1, int(days) * 24)
        display_hours = None
    else:
        lookback_hours = max(1, int(hours or 24))
        display_hours = lookback_hours
    cutoff = (_utcnow() - timedelta(hours=lookback_hours)).isoformat()
    return {
        "hours": display_hours,
        "days": int(days) if days is not None else None,
        "cutoff": cutoff,
    }


def _table_exists(db, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _count(
    db,
    table: str,
    time_col: str,
    cutoff: str,
    extra_where: str | None = None,
    extra_params: tuple = (),
) -> int:
    where = [f"datetime({time_col}) >= datetime(?)"]
    params: list[Any] = [cutoff]
    if extra_where:
        where.append(extra_where)
        params.extend(extra_params)
    row = db.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {' AND '.join(where)}",
        tuple(params),
    ).fetchone()
    return int(row["n"] if row else 0)


def _causal_cohort_counts(db, cutoff: str) -> dict[str, int | None]:
    """Count distinct opportunities created in-window through linked stages.

    Downstream timestamps are intentionally unrestricted: this is a cohort view,
    so a decision made after the opportunity's creation window still matures that
    same opportunity. Rows without an opportunity_id remain visible in raw event
    counts but cannot honestly contribute to conversion.
    """
    counts = _empty_counts()
    counts["operator_deferred"] = 0
    if not _table_exists(db, "opportunities"):
        return counts

    cohort_rows = db.execute(
        "SELECT id, status FROM opportunities WHERE datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    cohort_ids = {str(row["id"]) for row in cohort_rows}
    counts["opportunities_created"] = len(cohort_ids)
    counts["open_opportunities"] = _count(
        db, "opportunities", "created_at", cutoff, "status = 'open'",
    )
    if _table_exists(db, "filter_decisions"):
        rows = db.execute(
            "SELECT stage, COUNT(DISTINCT fd.opportunity_id) AS n "
            "FROM filter_decisions fd "
            "JOIN opportunities o ON o.id = fd.opportunity_id "
            "WHERE datetime(o.created_at) >= datetime(?) "
            "AND fd.stage IN ('paper_ranker', 'paper_trader_selection') "
            "GROUP BY stage",
            (cutoff,),
        ).fetchall()
        ranker_counts = {str(row["stage"]): int(row["n"]) for row in rows}
        if ranker_counts:
            counts["ranker_candidates"] = ranker_counts.get("paper_ranker", 0)
            counts["ranker_eligible"] = ranker_counts.get("paper_trader_selection", 0)

    signal_ids: set[str] = set()
    trade_ids: set[str] = set()
    if _table_exists(db, "pi_decisions"):
        rows = db.execute(
            "SELECT DISTINCT pd.opportunity_id "
            "FROM pi_decisions pd JOIN opportunities o ON o.id = pd.opportunity_id "
            "WHERE datetime(o.created_at) >= datetime(?) AND pd.action = 'signal_pending'",
            (cutoff,),
        ).fetchall()
        signal_ids = {str(row["opportunity_id"]) for row in rows}
        counts["signal_pending_written"] = len(signal_ids)

        rows = db.execute(
            "SELECT pd.opportunity_id, pd.action "
            "FROM pi_decisions pd JOIN opportunities o ON o.id = pd.opportunity_id "
            "WHERE datetime(o.created_at) >= datetime(?) "
            "AND pd.action IN ('trade','watch','skip','scale_in','flip') "
            "ORDER BY julianday(pd.created_at) ASC, pd.rowid ASC",
            (cutoff,),
        ).fetchall()
        latest_action: dict[str, str] = {}
        for decision in rows:
            opportunity_id = str(decision["opportunity_id"])
            if opportunity_id in signal_ids:
                latest_action[opportunity_id] = str(decision["action"])
        observed_ids: set[str] = set()
        if _table_exists(db, "operator_evaluation_events"):
            rows = db.execute(
                "WITH ranked AS ("
                "  SELECT json_extract(oe.queue_snapshot_json, '$.opportunity_id') "
                "    AS opportunity_id, oe.event_kind, "
                "    json_extract(oe.queue_snapshot_json, '$.defer_until') AS defer_until, "
                "    ROW_NUMBER() OVER ("
                "      PARTITION BY json_extract(oe.queue_snapshot_json, '$.opportunity_id') "
                "      ORDER BY julianday(oe.created_at) DESC, oe.rowid DESC"
                "    ) AS rn "
                "  FROM operator_evaluation_events oe "
                "  JOIN opportunities o "
                "    ON o.id=json_extract(oe.queue_snapshot_json, '$.opportunity_id') "
                "  WHERE json_valid(oe.queue_snapshot_json) "
                "    AND datetime(o.created_at) >= datetime(?)"
                ") SELECT opportunity_id FROM ranked "
                "WHERE rn=1 AND event_kind LIKE 'operator_decision_deferred_%' "
                "AND julianday(defer_until) > julianday('now')",
                (cutoff,),
            ).fetchall()
            observed_ids = {
                str(row["opportunity_id"])
                for row in rows
                if row["opportunity_id"] is not None
            } & signal_ids
        action_counts = Counter(latest_action.values())
        trade_ids = {
            opportunity_id
            for opportunity_id, action in latest_action.items()
            if action in {"trade", "scale_in", "flip"}
        }
        counts["system_operator_evaluated"] = len(set(latest_action) | observed_ids)
        counts["operator_deferred"] = len(observed_ids - set(latest_action))
        for action, key in (
            ("trade", "decisions_trade"),
            ("watch", "decisions_watch"),
            ("skip", "decisions_skip"),
            ("scale_in", "decisions_scale_in"),
            ("flip", "decisions_flip"),
        ):
            counts[key] = int(action_counts.get(action, 0))

    proposal_ids: set[str] = set()
    if _table_exists(db, "trade_proposals"):
        proposal_ids = _linked_opportunity_ids(
            db,
            "trade_proposals tp JOIN opportunities o ON o.id = tp.opportunity_id",
            cutoff,
        )
        proposal_ids &= trade_ids
        counts["proposals_created"] = len(proposal_ids)

    approved_ids: set[str] = set()
    if _table_exists(db, "risk_decisions") and _table_exists(db, "trade_proposals"):
        for approved, key in ((1, "risk_approved"), (0, "risk_rejected")):
            linked = _linked_opportunity_ids(
                db,
                "risk_decisions rd "
                "JOIN trade_proposals tp ON tp.id = rd.proposal_id "
                "JOIN opportunities o ON o.id = tp.opportunity_id",
                cutoff,
                "rd.approved = ?",
                (approved,),
            )
            linked &= proposal_ids
            counts[key] = len(linked)
            if approved:
                approved_ids = linked

    if _table_exists(db, "orders") and _table_exists(db, "trade_proposals"):
        executed_ids = _linked_opportunity_ids(
            db,
            "orders ord "
            "JOIN trade_proposals tp ON tp.id = ord.proposal_id "
            "JOIN opportunities o ON o.id = tp.opportunity_id",
            cutoff,
            "(lower(COALESCE(ord.status, '')) IN ('filled', 'executed') "
            "OR ord.filled_at IS NOT NULL)",
        )
        counts["orders_executed"] = len(executed_ids & approved_ids)

    if _table_exists(db, "positions") and _table_exists(db, "trade_proposals"):
        rows = db.execute(
            "SELECT DISTINCT o.id "
            "FROM positions pos "
            "LEFT JOIN trade_proposals tp ON tp.id = pos.proposal_id "
            "JOIN opportunities o ON o.id = COALESCE(pos.opportunity_id, tp.opportunity_id) "
            "WHERE datetime(o.created_at) >= datetime(?)",
            (cutoff,),
        ).fetchall()
        position_ids = {str(row["id"]) for row in rows}
        counts["positions_opened"] = len(position_ids & approved_ids)

    return counts


def _linked_opportunity_ids(
    db,
    from_sql: str,
    cutoff: str,
    extra_where: str | None = None,
    extra_params: tuple = (),
) -> set[str]:
    where = ["datetime(o.created_at) >= datetime(?)"]
    params: list[Any] = [cutoff]
    if extra_where:
        where.append(extra_where)
        params.extend(extra_params)
    rows = db.execute(
        f"SELECT DISTINCT o.id FROM {from_sql} WHERE {' AND '.join(where)}",
        tuple(params),
    ).fetchall()
    return {str(row["id"]) for row in rows}


def _orders_executed(db, cutoff: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) AS n FROM orders "
        "WHERE datetime(created_at) >= datetime(?) "
        "AND (lower(COALESCE(status, '')) IN ('filled', 'executed') OR filled_at IS NOT NULL)",
        (cutoff,),
    ).fetchone()
    return int(row["n"] if row else 0)


def _ranker_snapshot() -> dict:
    try:
        from aletheia.agents.paper_trader_ranker import build_paper_candidates

        candidates = build_paper_candidates(limit=50)
        return {
            "ranker_candidates": len(candidates),
            "ranker_eligible": sum(1 for c in candidates if not c.get("skip_reason")),
        }
    except Exception as exc:
        return {"ranker_candidates": None, "ranker_eligible": None, "error": str(exc)}


def _persisted_ranker_counts(db, cutoff: str) -> dict[str, int | None]:
    """Return historical candidate evaluations without re-running the ranker."""
    if not _table_exists(db, "filter_decisions"):
        return {"ranker_candidates": None, "ranker_eligible": None}
    rows = db.execute(
        "SELECT stage, COUNT(DISTINCT COALESCE(run_id, '') || ':' || candidate_key) AS n "
        "FROM filter_decisions "
        "WHERE datetime(evaluated_at) >= datetime(?) "
        "AND stage IN ('paper_ranker', 'paper_trader_selection') "
        "GROUP BY stage",
        (cutoff,),
    ).fetchall()
    counts = {str(row["stage"]): int(row["n"]) for row in rows}
    if not counts:
        return {"ranker_candidates": None, "ranker_eligible": None}
    return {
        "ranker_candidates": counts.get("paper_ranker", 0),
        "ranker_eligible": counts.get("paper_trader_selection", 0),
    }


def _conversion_rates(counts: dict) -> dict:
    trade_decisions = _trade_decision_count(counts)
    return {
        "opportunity_to_signal_pending": _rate(counts["signal_pending_written"], counts["opportunities_created"]),
        "signal_pending_to_evaluated": _rate(counts["system_operator_evaluated"], counts["signal_pending_written"]),
        "evaluated_to_trade_decision": _rate(trade_decisions, counts["system_operator_evaluated"]),
        "trade_decision_to_proposal": _rate(counts["proposals_created"], trade_decisions),
        "proposal_to_risk_approved": _rate(counts["risk_approved"], counts["proposals_created"]),
        "risk_approved_to_executed": _rate(counts["orders_executed"], counts["risk_approved"]),
        "proposal_to_position": _rate(counts["positions_opened"], counts["proposals_created"]),
    }


def _top_dropoffs(counts: dict) -> list[dict]:
    trade_decisions = _trade_decision_count(counts)
    stages = [
        ("opportunity_to_signal_pending", counts["opportunities_created"], counts["signal_pending_written"], "Ranker/paper_trader gate is filtering or deduping candidates."),
        ("signal_pending_to_evaluated", counts["signal_pending_written"], counts["system_operator_evaluated"], "System Operator daemon/scheduler is not consuming pending signals."),
        ("evaluated_to_trade_decision", counts["system_operator_evaluated"], trade_decisions, "System Operator is mostly deferring or choosing skip/watch."),
        ("trade_decision_to_proposal", trade_decisions, counts["proposals_created"], "Decision-to-proposal submission path is not producing proposals."),
        ("proposal_to_risk_approved", counts["proposals_created"], counts["risk_approved"], "Risk Engine or configured constraints are blocking proposals."),
        ("risk_approved_to_executed", counts["risk_approved"], counts["orders_executed"], "Execution Gateway, broker, or symbol availability is failing after approval."),
        ("proposal_to_position", counts["proposals_created"], counts["positions_opened"], "Approved/executed flow is not materializing into positions."),
    ]
    rows = []
    for stage, from_count, to_count, interpretation in stages:
        from_i = int(from_count or 0)
        to_i = int(to_count or 0)
        if from_i <= 0:
            continue
        drop = max(0, from_i - to_i)
        rows.append({
            "stage": stage,
            "from": from_i,
            "to": to_i,
            "drop_count": drop,
            "drop_pct": _round(drop / from_i) if from_i else None,
            "interpretation": interpretation,
        })
    rows.sort(key=lambda r: (r["drop_pct"] or 0, r["drop_count"]), reverse=True)
    return rows[:5]


def _risk_rejection_reasons(db, cutoff: str) -> list[dict]:
    rows = db.execute(
        "SELECT violations, message FROM risk_decisions "
        "WHERE approved = 0 AND datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    counter: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for row in rows:
        reasons = _json_list(row["violations"])
        if not reasons and row["message"]:
            reasons = [str(row["message"])]
        for reason in reasons or ["unknown"]:
            raw = str(reason)
            bucket = _risk_reason_bucket(raw)
            counter[bucket] += 1
            examples.setdefault(bucket, raw)
    result = []
    for reason, count in counter.most_common(10):
        item = {"reason": reason, "count": count}
        if examples.get(reason) != reason:
            item["example"] = examples[reason]
        result.append(item)
    return result


def _proposal_submission_reasons(
    db,
    cutoff: str,
    *,
    causal_cohort: bool,
) -> list[dict]:
    if not _table_exists(db, "filter_decisions"):
        return []
    if causal_cohort and _table_exists(db, "opportunities"):
        rows = db.execute(
            "SELECT fd.reason_code, COUNT(DISTINCT fd.candidate_key) AS n "
            "FROM filter_decisions fd "
            "JOIN opportunities o ON o.id = fd.opportunity_id "
            "WHERE datetime(o.created_at) >= datetime(?) "
            "AND fd.stage = 'proposal_submission' AND fd.decision = 'rejected' "
            "GROUP BY fd.reason_code ORDER BY n DESC, fd.reason_code",
            (cutoff,),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT reason_code, COUNT(DISTINCT candidate_key) AS n "
            "FROM filter_decisions "
            "WHERE datetime(evaluated_at) >= datetime(?) "
            "AND stage = 'proposal_submission' AND decision = 'rejected' "
            "GROUP BY reason_code ORDER BY n DESC, reason_code",
            (cutoff,),
        ).fetchall()
    return [
        {"reason": str(row["reason_code"]), "count": int(row["n"])}
        for row in rows
    ]


def _risk_reason_bucket(reason: str) -> str:
    """Collapse human-readable numeric details without changing reason tokens."""
    text = reason.strip() or "unknown"
    if re.fullmatch(r"[a-z0-9_.:-]+", text):
        return text
    text = re.sub(r"\$[-+]?\d[\d,]*(?:\.\d+)?", "$N", text)
    text = re.sub(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%", "N%", text)
    text = re.sub(r"(?<=[>:<])[-+]?\d+(?:\.\d+)?", "N", text)
    return text


def _execution_error_reasons(db, cutoff: str) -> list[dict]:
    rows = db.execute(
        "SELECT status, error_reason FROM orders "
        "WHERE datetime(created_at) >= datetime(?) "
        "AND (error_reason IS NOT NULL OR lower(COALESCE(status, '')) IN ('error', 'failed', 'rejected'))",
        (cutoff,),
    ).fetchall()
    counter: Counter[str] = Counter()
    for row in rows:
        reason = row["error_reason"] or row["status"] or "unknown"
        counter[str(reason)] += 1
    return [{"reason": reason, "count": count} for reason, count in counter.most_common(10)]


def _position_stats(db, cutoff: str) -> dict:
    rows = db.execute(
        "SELECT quantity, entry_price, leverage FROM positions "
        "WHERE datetime(opened_at) >= datetime(?)",
        (cutoff,),
    ).fetchall()
    notionals = [
        float(row["quantity"] or 0) * float(row["entry_price"] or 0)
        for row in rows
        if float(row["quantity"] or 0) > 0 and float(row["entry_price"] or 0) > 0
    ]
    leverages = [float(row["leverage"] or 1) for row in rows if row["leverage"] is not None]
    return {
        "sample_size": len(notionals),
        "avg_notional_usdt": _round(mean(notionals)) if notionals else None,
        "median_notional_usdt": _round(median(notionals)) if notionals else None,
        "max_notional_usdt": _round(max(notionals)) if notionals else None,
        "avg_leverage": _round(mean(leverages)) if leverages else None,
        "median_leverage": _round(median(leverages)) if leverages else None,
    }


def _main_choke_point(
    counts: dict,
    stats: dict,
    min_sample_size: int,
    proposal_submission_reasons: list[dict] | None = None,
) -> dict:
    trade_decisions = _trade_decision_count(counts)
    terminal_decisions = sum(
        int(counts[key] or 0)
        for key in ("decisions_trade", "decisions_watch", "decisions_skip", "decisions_scale_in", "decisions_flip")
    )
    positions = int(counts["positions_opened"] or 0)
    median_notional = stats.get("median_notional_usdt")

    if positions >= min_sample_size and median_notional is not None and median_notional <= _TINY_POSITION_NOTIONAL_USDT:
        return _choke(
            "sizing_tiny_positions",
            "warn",
            f"{positions} positions opened, but median notional is {median_notional}U.",
            "Run Sizing Trace Audit to inspect base notional, Kelly, ATR, conviction, and risk caps.",
        )
    if int(counts["opportunities_created"] or 0) < min_sample_size:
        return _choke(
            "no_opportunities",
            "insufficient_data",
            f"Only {counts['opportunities_created']} opportunities were created in this window.",
            "Collect more market/news/observer output before changing ranker, System Operator, or sizing behavior.",
        )
    if int(counts["proposals_created"] or 0) >= min_sample_size and int(counts["risk_rejected"] or 0) > int(counts["risk_approved"] or 0):
        return _choke(
            "risk_engine",
            "warn",
            f"{counts['risk_rejected']} of {counts['proposals_created']} proposals were rejected by risk.",
            "Inspect top risk rejection reasons before changing sizing or execution behavior.",
        )
    if int(counts["risk_approved"] or 0) >= min_sample_size and int(counts["orders_executed"] or 0) == 0:
        return _choke(
            "execution_gateway",
            "warn",
            f"{counts['risk_approved']} risk-approved proposals produced 0 executed orders.",
            "Inspect Execution Gateway and broker/demo exchange errors.",
        )
    if int(counts["proposals_created"] or 0) >= min_sample_size and positions == 0:
        return _choke(
            "position_opening",
            "warn",
            f"{counts['proposals_created']} proposals produced 0 opened positions.",
            "Inspect order fill handling and position creation after execution.",
        )
    if int(counts["proposals_created"] or 0) > 0 and positions == 0:
        proposals = int(counts["proposals_created"] or 0)
        rejected = int(counts["risk_rejected"] or 0)
        if rejected:
            return _choke(
                "risk_engine",
                "insufficient_data",
                f"Only {proposals} linked proposals are in this cohort; {rejected} were rejected by risk.",
                f"Collect at least {min_sample_size} linked proposals before treating this as a stable choke point.",
            )
        return _choke(
            "position_opening",
            "insufficient_data",
            f"Only {proposals} linked proposals and 0 positions are in this cohort.",
            f"Collect at least {min_sample_size} linked proposals before tuning execution behavior.",
        )
    if trade_decisions > 0 and int(counts["proposals_created"] or 0) == 0:
        reasons = proposal_submission_reasons or []
        reason_text = ", ".join(
            f"{item['reason']} x{item['count']}" for item in reasons[:3]
        )
        suffix = f" Known pre-proposal rejections: {reason_text}." if reason_text else ""
        return _choke(
            "proposal_submission",
            "warn" if trade_decisions >= min_sample_size else "insufficient_data",
            f"{trade_decisions} trade-like decisions produced 0 proposals.{suffix}",
            "Inspect the structured proposal-submission reasons before changing System Operator behavior.",
        )
    if int(counts["system_operator_evaluated"] or 0) >= min_sample_size and trade_decisions == 0:
        if int(counts["operator_deferred"] or 0) > 0 and terminal_decisions == 0:
            return _choke(
                "system_operator_deferred",
                "warn",
                f"{counts['operator_deferred']} observed signals remain deferred with 0 terminal decisions.",
                "Await the durable defer deadlines or inspect defer reasons before changing System Operator behavior.",
            )
        return _choke(
            "system_operator_decision",
            "warn",
            f"{counts['system_operator_evaluated']} evaluated signals produced 0 trade decisions.",
            "Inspect System Operator defer/skip/watch reasons and prompt/context constraints.",
        )
    if int(counts["signal_pending_written"] or 0) > 0 and int(counts["system_operator_evaluated"] or 0) == 0:
        return _choke(
            "system_operator_consumption",
            "warn",
            f"{counts['signal_pending_written']} signal_pending rows were written, but none were evaluated.",
            "Inspect System Operator daemon/scheduler consumption of pending signals.",
        )
    if int(counts["signal_pending_written"] or 0) == 0:
        return _choke(
            "ranker_or_paper_trader",
            "warn",
            f"{counts['opportunities_created']} opportunities produced 0 signal_pending rows.",
            "Inspect ranker eligibility, paper_trader dedupe, symbol filters, and opportunity status handling.",
        )
    return _choke(
        "ok",
        "ok",
        f"{counts['positions_opened']} positions opened from {counts['opportunities_created']} opportunities in this window.",
        "No single funnel choke point is obvious; inspect standalone reports before changing behavior.",
    )


def _trade_decision_count(counts: dict) -> int:
    return int(counts["decisions_trade"] or 0) + int(counts["decisions_scale_in"] or 0) + int(counts["decisions_flip"] or 0)


def _rate(numerator: int | None, denominator: int | None) -> float | None:
    denom = int(denominator or 0)
    if denom <= 0:
        return None
    return _round(float(numerator or 0) / denom)


def _json_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(str(raw))
    except (TypeError, ValueError):
        return [str(raw)] if str(raw).strip() else []
    if isinstance(value, list):
        return value
    return [value]


def _choke(stage: str, severity: str, summary: str, next_action: str) -> dict:
    return {
        "stage": stage,
        "severity": severity,
        "summary": summary,
        "next_action": next_action,
    }


def _round(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fmt_num(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _window_label(window: dict) -> str:
    if window.get("days") is not None:
        return f"{window['days']}d"
    return f"{window.get('hours', 24)}h"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
