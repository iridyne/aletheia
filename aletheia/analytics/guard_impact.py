"""Read-only Guard Impact Audit.

This module answers one narrow question: are active blocking paper-entry guards
currently suppressing candidate flow, and do resolved counterfactual outcomes
make that suppression look protective or suspicious?

It does not call the ranker, LLMs, HTTP clients, exchanges, or any mutation API.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from aletheia.config.loader import get_data_dir
from aletheia.knowledge.frontmatter import parse_frontmatter
from aletheia.knowledge.guards import TradingGuard, evaluate_guards, load_trading_guards
from aletheia.storage.sqlite import get_db

_HORIZONS = ("1h", "4h", "24h")
_VERDICT_PROTECTIVE = "protective"
_VERDICT_SUSPECT = "overbroad_suspect"
_VERDICT_INSUFFICIENT = "insufficient_data"


def build_guard_impact_audit(hours: int = 24) -> dict[str, Any]:
    """Build a deterministic, read-only guard impact report."""
    hours_i = max(1, int(hours or 24))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_i)).isoformat()
    db = get_db()

    active_guards = [
        g for g in load_trading_guards(scope="paper_entry", status="active")
        if bool(g.block_candidate)
    ]
    guard_meta = _guard_frontmatter_by_id()
    opportunities = _recent_open_opportunities(db, cutoff)
    universe_members = _universe_members_by_id(db, [o.get("universe_member_id") for o in opportunities])

    blocked: list[dict[str, Any]] = []
    for opp in opportunities:
        direction = _direction_from_bias(opp.get("direction_bias"))
        if not direction:
            continue
        member = universe_members.get(opp.get("universe_member_id"))
        context = _guard_context(opp, member, direction)
        evaluation = evaluate_guards(context, scope="paper_entry", guards=active_guards)
        hard_hits = [h for h in evaluation.hits if h.block_candidate]
        if not hard_hits:
            continue
        outcomes = _outcomes_for_candidate(db, opp, direction)
        verdict = _candidate_verdict(outcomes)
        blocked.append({
            "opportunity_id": opp.get("id"),
            "symbol": opp.get("symbol"),
            "direction": direction,
            "scanner_family": opp.get("scanner_family"),
            "created_at": opp.get("created_at"),
            "confidence": opp.get("confidence"),
            "confidence_score": opp.get("confidence_score"),
            "universe_member_id": opp.get("universe_member_id"),
            "matched_guards": [
                _guard_hit_row(hit.guard_id, active_guards, guard_meta, hit.reason)
                for hit in hard_hits
            ],
            "outcomes": outcomes,
            "verdict": verdict,
        })

    return {
        "window": {"hours": hours_i, "cutoff": cutoff},
        "active_blocking_guards": {
            "total": len(active_guards),
            "by_scanner_family": _aggregate_guards(active_guards, "scanner_family"),
            "by_direction": _aggregate_guards(active_guards, "direction"),
            "by_symbol": _aggregate_guards(active_guards, "symbol"),
        },
        "candidate_flow": {
            "recent_open_opportunities": len(opportunities),
            "guard_blocked_candidates": len(blocked),
            "by_scanner_family": _aggregate_candidates(blocked, "scanner_family"),
            "by_direction": _aggregate_candidates(blocked, "direction"),
            "by_symbol": _aggregate_candidates(blocked, "symbol"),
        },
        "blocked_candidates": blocked,
        "verdict_counts": dict(Counter(row["verdict"] for row in blocked)),
        "verdict": _overall_verdict(blocked, active_guards),
        "notes": [
            "Read-only snapshot: no guards were modified and no candidates were submitted.",
            "Verdict uses existing shadow/forward outcomes only; missing outcomes stay insufficient_data.",
        ],
    }


def render_guard_impact_audit(report: dict[str, Any]) -> str:
    """Render a compact human report for `aletheia inspect guard-impact`."""
    window = report.get("window") or {}
    guards = report.get("active_blocking_guards") or {}
    flow = report.get("candidate_flow") or {}
    lines = [
        "Guard Impact Audit",
        "",
        f"Window: {window.get('hours', '?')}h",
        f"Overall verdict: {report.get('verdict', _VERDICT_INSUFFICIENT)}",
        "",
        "Active blocking guards:",
        f"  total: {guards.get('total', 0)}",
        f"  by_scanner_family: {_fmt_counts(guards.get('by_scanner_family'))}",
        f"  by_direction: {_fmt_counts(guards.get('by_direction'))}",
        f"  by_symbol: {_fmt_counts(guards.get('by_symbol'))}",
        "",
        "Candidate flow:",
        f"  recent_open_opportunities: {flow.get('recent_open_opportunities', 0)}",
        f"  guard_blocked_candidates: {flow.get('guard_blocked_candidates', 0)}",
        f"  by_scanner_family: {_fmt_counts(flow.get('by_scanner_family'))}",
        f"  by_direction: {_fmt_counts(flow.get('by_direction'))}",
        f"  by_symbol: {_fmt_counts(flow.get('by_symbol'))}",
        "",
        "Blocked candidates:",
    ]
    blocked = report.get("blocked_candidates") or []
    if not blocked:
        lines.append("  n/a")
    for row in blocked[:30]:
        guards_s = ",".join(g.get("guard_id", "?") for g in row.get("matched_guards") or [])
        outcome_s = _fmt_outcomes(row.get("outcomes") or {})
        lines.append(
            "  "
            f"{row.get('symbol')} {row.get('direction')} "
            f"family={row.get('scanner_family') or 'unknown'} "
            f"guards={guards_s or 'n/a'} "
            f"outcomes={outcome_s} "
            f"verdict={row.get('verdict')}"
        )
    if len(blocked) > 30:
        lines.append(f"  ... {len(blocked) - 30} more")
    return "\n".join(lines)


def _recent_open_opportunities(db, cutoff: str) -> list[dict[str, Any]]:
    if not _table_exists(db, "opportunities"):
        return []
    rows = db.execute(
        "SELECT * FROM opportunities "
        "WHERE status = 'open' AND datetime(created_at) >= datetime(?) "
        "ORDER BY datetime(created_at) DESC",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def _universe_members_by_id(db, member_ids: list[Any]) -> dict[str, dict[str, Any]]:
    ids = [str(x) for x in member_ids if x]
    if not ids or not _table_exists(db, "universe_members"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(ids, 100):
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(f"SELECT * FROM universe_members WHERE id IN ({placeholders})", tuple(chunk)).fetchall()
        for row in rows:
            d = dict(row)
            out[str(d["id"])] = d
    return out


def _guard_context(opp: dict[str, Any], member: dict[str, Any] | None, direction: str) -> dict[str, Any]:
    confidence_score = _to_float(opp.get("confidence_score"))
    late_entry_penalty = _to_float((member or {}).get("late_entry_penalty"))
    return {
        "symbol": opp.get("symbol"),
        "asset_class": opp.get("asset_class") or "crypto",
        "direction": direction,
        "scanner_family": opp.get("scanner_family"),
        "confidence": opp.get("confidence"),
        "confidence_score": confidence_score,
        "confidence_bucket": _confidence_bucket(confidence_score),
        "late_entry_penalty": late_entry_penalty,
        "late_entry_bucket": _late_entry_bucket(late_entry_penalty),
        "tradability_score": _to_float((member or {}).get("tradability_score")),
        "universe_total_score": _to_float((member or {}).get("total_score")),
        "crowding_risk": _to_float((member or {}).get("crowding_risk")),
        "risk_flags": _json_list((member or {}).get("risk_flags")),
        "factor_status": None,
        "factor_expectancy": None,
        "factor_trade_count": None,
        "strategy_has_ruleset": False,
        "ruleset_entry_result": None,
        "strategy_drift_status": None,
        "paper_expectancy": None,
        "backtest_expectancy": None,
    }


def _outcomes_for_candidate(db, opp: dict[str, Any], direction: str) -> dict[str, Any]:
    outcomes: dict[str, Any] = {"shadow": None, "forward": {}}
    if _table_exists(db, "shadow_trades"):
        row = db.execute(
            "SELECT pnl_pct, resolved_at, created_at, batch_id, rank FROM shadow_trades "
            "WHERE opportunity_id = ? AND pnl_pct IS NOT NULL "
            "ORDER BY datetime(resolved_at) DESC, datetime(created_at) DESC LIMIT 1",
            (opp.get("id"),),
        ).fetchone()
        if row:
            d = dict(row)
            outcomes["shadow"] = {
                "pnl_pct": _round(_to_float(d.get("pnl_pct"))),
                "resolved_at": d.get("resolved_at"),
                "batch_id": d.get("batch_id"),
                "rank": d.get("rank"),
            }
    member_id = opp.get("universe_member_id")
    if member_id and _table_exists(db, "universe_forward_outcomes"):
        for horizon in _HORIZONS:
            row = db.execute(
                "SELECT forward_return_pct, settled_at, status FROM universe_forward_outcomes "
                "WHERE member_id = ? AND horizon = ? AND status = 'settled' "
                "AND forward_return_pct IS NOT NULL "
                "ORDER BY datetime(settled_at) DESC LIMIT 1",
                (member_id, horizon),
            ).fetchone()
            if not row:
                continue
            d = dict(row)
            raw = _to_float(d.get("forward_return_pct"))
            outcomes["forward"][horizon] = {
                "forward_return_pct": _round(raw),
                "direction_adjusted_return_pct": _round(_direction_adjusted(raw, direction)),
                "settled_at": d.get("settled_at"),
                "status": d.get("status"),
            }
    return outcomes


def _candidate_verdict(outcomes: dict[str, Any]) -> str:
    values: list[float] = []
    shadow = outcomes.get("shadow")
    if shadow and shadow.get("pnl_pct") is not None:
        values.append(float(shadow["pnl_pct"]))
    for item in (outcomes.get("forward") or {}).values():
        val = item.get("direction_adjusted_return_pct")
        if val is not None:
            values.append(float(val))
    if not values:
        return _VERDICT_INSUFFICIENT
    if any(v > 0 for v in values):
        return _VERDICT_SUSPECT
    if all(v <= 0 for v in values):
        return _VERDICT_PROTECTIVE
    return _VERDICT_INSUFFICIENT


def _overall_verdict(blocked: list[dict[str, Any]], guards: list[TradingGuard]) -> str:
    if not guards:
        return _VERDICT_INSUFFICIENT
    verdicts = [r.get("verdict") for r in blocked]
    if _VERDICT_SUSPECT in verdicts:
        return _VERDICT_SUSPECT
    if _VERDICT_PROTECTIVE in verdicts:
        return _VERDICT_PROTECTIVE
    return _VERDICT_INSUFFICIENT


def _guard_hit_row(
    guard_id: str,
    guards: list[TradingGuard],
    meta_by_id: dict[str, dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    guard = next((g for g in guards if g.guard_id == guard_id), None)
    meta = meta_by_id.get(guard_id) or {}
    return {
        "guard_id": guard_id,
        "title": guard.title if guard else meta.get("title"),
        "classification": _classify_guard(guard, meta),
        "evidence_symbol": meta.get("symbol"),
        "evidence_horizon": meta.get("horizon"),
        "evidence_forward_return_pct": _round(_to_float(meta.get("forward_return_pct"))),
        "reason": reason,
    }


def _classify_guard(guard: TradingGuard | None, meta: dict[str, Any]) -> list[str]:
    classes: list[str] = []
    if guard:
        at = guard.applies_to
        pred_fields = {p.field for p in guard.predicates}
        if at.symbol or "symbol" in pred_fields:
            classes.append("symbol-specific")
        if at.scanner_family or "scanner_family" in pred_fields:
            classes.append("family-wide")
        if at.direction or "direction" in pred_fields:
            classes.append("direction-wide")
        if any("regime" in f for f in pred_fields):
            classes.append("regime-specific")
    tags = [str(t).lower() for t in (meta.get("tags") or [])]
    text = " ".join([str(meta.get("title") or ""), str(meta.get("description") or ""), *tags]).lower()
    if "regime" in text and "regime-specific" not in classes:
        classes.append("regime-specific")
    return classes or ["general"]


def _aggregate_guards(guards: list[TradingGuard], field: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for guard in guards:
        values = _guard_values(guard, field)
        if not values:
            counter["unscoped"] += 1
            continue
        for value in values:
            counter[str(value)] += 1
    return dict(counter)


def _guard_values(guard: TradingGuard, field: str) -> list[Any]:
    at = guard.applies_to
    if field == "scanner_family":
        return list(at.scanner_family or [])
    if field == "direction":
        return list(at.direction or [])
    if field == "symbol":
        return list(at.symbol or [])
    return []


def _aggregate_candidates(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        counter[str(row.get(field) or "unknown")] += 1
    return dict(counter)


def _guard_frontmatter_by_id() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    guards_dir = get_data_dir() / "knowledge" / "guards"
    for path in _markdown_files(guards_dir):
        try:
            meta, _ = parse_frontmatter(path)
        except Exception:
            continue
        if not meta or meta.get("status") != "active":
            continue
        gid = str(meta.get("guard_id") or path.stem)
        out[gid] = meta
    return out


def _markdown_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(path.glob("*.md"))


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


def _confidence_bucket(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _late_entry_bucket(value: float | None) -> str | None:
    if value is None:
        return None
    if value >= 30:
        return "high"
    if value >= 10:
        return "medium"
    return "low"


def _json_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _fmt_counts(counts: dict[str, int] | None) -> str:
    if not counts:
        return "n/a"
    return ", ".join(f"{k}={v}" for k, v in counts.items())


def _fmt_outcomes(outcomes: dict[str, Any]) -> str:
    parts: list[str] = []
    shadow = outcomes.get("shadow")
    if shadow and shadow.get("pnl_pct") is not None:
        parts.append(f"shadow={shadow['pnl_pct']:+.2f}%")
    for horizon in _HORIZONS:
        item = (outcomes.get("forward") or {}).get(horizon)
        if item and item.get("direction_adjusted_return_pct") is not None:
            parts.append(f"{horizon}={item['direction_adjusted_return_pct']:+.2f}%")
    return ", ".join(parts) if parts else "n/a"


def _table_exists(db, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _chunks(values: list[str], size: int):
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]
