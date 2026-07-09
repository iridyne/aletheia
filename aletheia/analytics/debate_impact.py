"""Read-only Debate Impact Audit.

This module answers one narrow question: when System Operator ran debate and
then skipped, did existing forward outcomes later support or contradict that
skip?

It reads only persisted decisions, opportunities, and forward outcomes. It does
not call rankers, LLMs, HTTP clients, exchanges, or any mutation API.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.storage.sqlite import get_db

_HORIZONS = ("15m", "1h", "4h")
_VERDICT_MISSED = "missed_alpha_suspect"
_VERDICT_PROTECTIVE = "protective"
_VERDICT_INSUFFICIENT = "insufficient_data"


def build_debate_impact_audit(hours: int = 24) -> dict[str, Any]:
    """Build a deterministic, read-only debate impact report."""
    hours_i = max(1, int(hours or 24))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_i)).isoformat()
    db = get_db()

    flow = _decision_flow(db, cutoff)
    opportunities = _debated_opportunities(db, cutoff)
    for row in opportunities:
        row["outcomes"] = _outcomes_for_opportunity(db, row)
        row["verdict"] = _opportunity_verdict(row["outcomes"])

    return {
        "window": {"hours": hours_i, "cutoff": cutoff},
        "decision_flow": flow,
        "outcome_summary": _outcome_summary(opportunities),
        "debated_opportunities": opportunities,
        "verdict_counts": dict(Counter(row["verdict"] for row in opportunities)),
        "verdict": _overall_verdict(opportunities),
        "notes": [
            "Read-only snapshot: no decisions, proposals, guards, or thresholds were modified.",
            "Unsupported candidate signal is counted from persisted decision reasoning only; this audit does not rerun the ranker.",
            "Debate trace fields are available only for rows written after the trace migration; legacy rows stay missing.",
            "Verdict uses existing forward outcomes only; missing outcomes stay insufficient_data.",
        ],
    }


def render_debate_impact_audit(report: dict[str, Any]) -> str:
    """Render a compact human report for `aletheia inspect debate-impact`."""
    window = report.get("window") or {}
    flow = report.get("decision_flow") or {}
    lines = [
        "Debate Impact Audit",
        "",
        f"Window: {window.get('hours', '?')}h",
        f"Overall verdict: {report.get('verdict', _VERDICT_INSUFFICIENT)}",
        "",
        "Decision flow:",
        f"  total_decisions: {flow.get('total_decisions', 0)}",
        f"  debated_decisions: {flow.get('debated_decisions', 0)}",
        f"  debated_skips: {flow.get('debated_skips', 0)}",
        f"  trade_like_decisions: {flow.get('trade_like_decisions', 0)}",
        f"  unsupported_mentions: {flow.get('unsupported_mentions', 0)}",
        "",
        "Forward outcomes for debated skips:",
    ]
    summary = report.get("outcome_summary") or {}
    for horizon in _HORIZONS:
        item = summary.get(horizon) or {}
        lines.append(
            "  "
            f"{horizon}: with_outcome={item.get('with_outcome', 0)} "
            f"favorable={item.get('favorable', 0)} "
            f"unfavorable={item.get('unfavorable', 0)} "
            f"avg_dir_adj={_fmt_pct(item.get('avg_direction_adjusted_return_pct'))}"
        )

    lines += ["", "Debated opportunities:"]
    rows = report.get("debated_opportunities") or []
    if not rows:
        lines.append("  n/a")
    for row in rows[:30]:
        lines.append(
            "  "
            f"{row.get('symbol') or 'unknown'} {row.get('direction') or 'unknown'} "
            f"family={row.get('scanner_family') or 'unknown'} "
            f"decisions={row.get('decision_count', 0)} "
            f"conviction={_fmt_trace(row)} "
            f"outcomes={_fmt_outcomes(row.get('outcomes') or {})} "
            f"verdict={row.get('verdict')}"
        )
    if len(rows) > 30:
        lines.append(f"  ... {len(rows) - 30} more")
    return "\n".join(lines)


def _decision_flow(db, cutoff: str) -> dict[str, int]:
    if not _table_exists(db, "pi_decisions"):
        return {
            "total_decisions": 0,
            "debated_decisions": 0,
            "debated_skips": 0,
            "trade_like_decisions": 0,
            "unsupported_mentions": 0,
        }
    row = db.execute(
        "SELECT "
        "COUNT(*) AS total_decisions, "
        "SUM(CASE WHEN debate_ran = 1 THEN 1 ELSE 0 END) AS debated_decisions, "
        "SUM(CASE WHEN debate_ran = 1 AND action = 'skip' THEN 1 ELSE 0 END) AS debated_skips, "
        "SUM(CASE WHEN action IN ('trade','scale_in','flip') THEN 1 ELSE 0 END) AS trade_like_decisions, "
        "SUM(CASE WHEN reasoning LIKE '%okx_demo_symbol_not_supported%' THEN 1 ELSE 0 END) AS unsupported_mentions "
        "FROM pi_decisions WHERE datetime(created_at) >= datetime(?)",
        (cutoff,),
    ).fetchone()
    d = dict(row) if row else {}
    return {k: int(d.get(k) or 0) for k in (
        "total_decisions",
        "debated_decisions",
        "debated_skips",
        "trade_like_decisions",
        "unsupported_mentions",
    )}


def _debated_opportunities(db, cutoff: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "pi_decisions"):
        return []
    if not _table_exists(db, "opportunities"):
        return []
    trace_select = (
        "AVG(pd.debate_initial_conviction) AS avg_initial_conviction, "
        "AVG(pd.debate_bull_confidence) AS avg_debate_bull, "
        "AVG(pd.debate_bear_confidence) AS avg_debate_bear, "
        "AVG(pd.debate_adjustment) AS avg_debate_adjustment, "
    ) if _has_columns(db, "pi_decisions", [
        "debate_initial_conviction",
        "debate_bull_confidence",
        "debate_bear_confidence",
        "debate_adjustment",
    ]) else (
        "NULL AS avg_initial_conviction, "
        "NULL AS avg_debate_bull, "
        "NULL AS avg_debate_bear, "
        "NULL AS avg_debate_adjustment, "
    )
    rows = db.execute(
        "SELECT "
        "pd.opportunity_id, MAX(pd.created_at) AS latest_decision_at, "
        "COUNT(*) AS decision_count, AVG(pd.conviction) AS avg_final_conviction, "
        f"{trace_select}"
        "o.symbol, o.direction_bias, o.scanner_family, o.universe_member_id "
        "FROM pi_decisions pd "
        "LEFT JOIN opportunities o ON o.id = pd.opportunity_id "
        "WHERE datetime(pd.created_at) >= datetime(?) "
        "AND pd.debate_ran = 1 AND pd.action = 'skip' "
        "GROUP BY pd.opportunity_id "
        "ORDER BY datetime(latest_decision_at) DESC",
        (cutoff,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        d = dict(row)
        out.append({
            "opportunity_id": d.get("opportunity_id"),
            "latest_decision_at": d.get("latest_decision_at"),
            "decision_count": int(d.get("decision_count") or 0),
            "avg_final_conviction": _round(_to_float(d.get("avg_final_conviction")), 1),
            "avg_initial_conviction": _round(_to_float(d.get("avg_initial_conviction")), 1),
            "avg_debate_bull": _round(_to_float(d.get("avg_debate_bull")), 1),
            "avg_debate_bear": _round(_to_float(d.get("avg_debate_bear")), 1),
            "avg_debate_adjustment": _round(_to_float(d.get("avg_debate_adjustment")), 1),
            "symbol": d.get("symbol"),
            "direction": _direction_from_bias(d.get("direction_bias")),
            "scanner_family": d.get("scanner_family"),
            "universe_member_id": d.get("universe_member_id"),
        })
    return out


def _outcomes_for_opportunity(db, row: dict[str, Any]) -> dict[str, Any]:
    outcomes: dict[str, Any] = {}
    member_id = row.get("universe_member_id")
    direction = row.get("direction")
    if not member_id or not direction or not _table_exists(db, "universe_forward_outcomes"):
        return outcomes
    for horizon in _HORIZONS:
        found = db.execute(
            "SELECT forward_return_pct, settled_at, status FROM universe_forward_outcomes "
            "WHERE member_id = ? AND horizon = ? AND status = 'settled' "
            "AND forward_return_pct IS NOT NULL "
            "ORDER BY datetime(settled_at) DESC LIMIT 1",
            (member_id, horizon),
        ).fetchone()
        if not found:
            continue
        d = dict(found)
        raw = _to_float(d.get("forward_return_pct"))
        outcomes[horizon] = {
            "forward_return_pct": _round(raw),
            "direction_adjusted_return_pct": _round(_direction_adjusted(raw, direction)),
            "settled_at": d.get("settled_at"),
            "status": d.get("status"),
        }
    return outcomes


def _outcome_summary(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for horizon in _HORIZONS:
        values = [
            item.get("direction_adjusted_return_pct")
            for row in rows
            for item in [(row.get("outcomes") or {}).get(horizon) or {}]
            if item.get("direction_adjusted_return_pct") is not None
        ]
        summary[horizon] = {
            "with_outcome": len(values),
            "favorable": sum(1 for value in values if value > 0),
            "unfavorable": sum(1 for value in values if value <= 0),
            "avg_direction_adjusted_return_pct": _round(
                sum(values) / len(values) if values else None
            ),
            "min_direction_adjusted_return_pct": _round(min(values) if values else None),
            "max_direction_adjusted_return_pct": _round(max(values) if values else None),
        }
    return summary


def _opportunity_verdict(outcomes: dict[str, Any]) -> str:
    values = [
        item.get("direction_adjusted_return_pct")
        for item in outcomes.values()
        if item.get("direction_adjusted_return_pct") is not None
    ]
    if not values:
        return _VERDICT_INSUFFICIENT
    if any(value > 0 for value in values):
        return _VERDICT_MISSED
    if all(value <= 0 for value in values):
        return _VERDICT_PROTECTIVE
    return _VERDICT_INSUFFICIENT


def _overall_verdict(rows: list[dict[str, Any]]) -> str:
    verdicts = [row.get("verdict") for row in rows]
    if _VERDICT_MISSED in verdicts:
        return _VERDICT_MISSED
    if _VERDICT_PROTECTIVE in verdicts:
        return _VERDICT_PROTECTIVE
    return _VERDICT_INSUFFICIENT


def _direction_from_bias(raw: Any) -> str | None:
    bias = str(raw or "").lower()
    if bias in {"long", "bullish"}:
        return "long"
    if bias in {"short", "bearish"}:
        return "short"
    return None


def _direction_adjusted(value: float | None, direction: str) -> float | None:
    if value is None:
        return None
    return -value if direction == "short" else value


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None, ndigits: int = 4) -> float | None:
    return round(value, ndigits) if value is not None else None


def _fmt_num(value: Any) -> str:
    return "n/a" if value is None else str(value)


def _fmt_pct(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):+.4f}%"


def _fmt_outcomes(outcomes: dict[str, Any]) -> str:
    parts: list[str] = []
    for horizon in _HORIZONS:
        item = outcomes.get(horizon)
        if item and item.get("direction_adjusted_return_pct") is not None:
            parts.append(f"{horizon}={item['direction_adjusted_return_pct']:+.4f}%")
    return ", ".join(parts) if parts else "n/a"


def _fmt_trace(row: dict[str, Any]) -> str:
    final = _fmt_num(row.get("avg_final_conviction"))
    initial = row.get("avg_initial_conviction")
    if initial is None:
        return f"final={final} trace=missing"
    return (
        f"initial={_fmt_num(initial)} final={final} "
        f"adj={_fmt_num(row.get('avg_debate_adjustment'))} "
        f"bull={_fmt_num(row.get('avg_debate_bull'))} "
        f"bear={_fmt_num(row.get('avg_debate_bear'))}"
    )


def _table_exists(db, table: str) -> bool:
    found = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return found is not None


def _has_columns(db, table: str, columns: list[str]) -> bool:
    if not _table_exists(db, table):
        return False
    found = {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    return all(column in found for column in columns)
