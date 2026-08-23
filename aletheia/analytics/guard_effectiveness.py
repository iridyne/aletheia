"""Guard Effectiveness Analytics.

Tracks how guards perform: how often they fire, what they blocked/penalized,
and whether blocked trades would have been losses (counterfactual validation).

Deterministic. No LLM. Read-only (queries attribution + ranker logs).
"""
from __future__ import annotations

import logging
import json
import sqlite3
from collections import defaultdict

from aletheia.analytics.trade_attribution import build_trade_attribution_rows
from aletheia.knowledge.guards import load_trading_guards

logger = logging.getLogger("aletheia.analytics.guard_effectiveness")


def _clean_rows(rows: list[dict]) -> list[dict]:
    """#159: drop synthetic/administrative closes AND pre-clean-window (dirty) data.

    Guard promotion/effectiveness learn loss-rates that MUTATE live guard state
    (promote to a hard block / deprecate), so they must only count genuine
    post-floor trade outcomes — matching factor_feedback._genuine_rows plus the
    clean-window floor the sibling learners apply. Reconciler orphan sweeps and
    cleanups close near ~entry (near-random) and would tip the promote/deprecate
    thresholds on fabricated evidence.
    """
    from aletheia.analytics.close_reasons import is_synthetic_close
    from aletheia.analytics.learning_window import LEARNING_DATA_FLOOR_ISO
    return [
        r for r in rows
        if not is_synthetic_close(r.get("close_reason"))
        and str(r.get("closed_at") or "") >= LEARNING_DATA_FLOOR_ISO
    ]


def compute_guard_stats() -> list[dict]:
    """Compute effectiveness stats for all active guards.

    For each guard, produces:
    - fire_count: how many closed positions would have triggered this guard at entry
    - loss_rate_when_fired: % of those that ended as losses
    - avg_loss_pct: average loss % on trades that triggered guard AND lost
    - avg_penalty_saved: avg score penalty that would have been applied

    This is counterfactual — it re-evaluates guards against historical attribution
    rows to estimate "would this guard have helped?"
    """
    guards = load_trading_guards(status="active")
    if not guards:
        return []

    rows = _clean_rows(build_trade_attribution_rows(limit=500))
    blocked_by_guard = _blocked_candidate_stats_by_guard(guards)
    if not rows and not blocked_by_guard:
        return []

    stats = []
    for guard in guards:
        fired_rows = _rows_matching_guard(guard, rows)
        blocked = blocked_by_guard.get(guard.guard_id, _empty_blocked_stats())
        if not fired_rows:
            stats.append({
                "guard_id": guard.guard_id,
                "title": guard.title,
                "fire_count": 0,
                "loss_rate_when_fired": 0.0,
                "avg_loss_pct": 0.0,
                "estimated_savings": 0.0,
                **blocked,
            })
            continue

        losses = [r for r in fired_rows if float(r.get("realized_pnl", 0)) < 0]
        loss_rate = len(losses) / len(fired_rows) if fired_rows else 0
        avg_loss = (
            sum(float(r.get("realized_pnl_pct", 0)) for r in losses) / len(losses)
            if losses else 0
        )

        stats.append({
            "guard_id": guard.guard_id,
            "title": guard.title,
            "fire_count": len(fired_rows),
            "loss_rate_when_fired": round(loss_rate, 3),
            "avg_loss_pct": round(avg_loss, 4),
            "estimated_savings": round(abs(avg_loss) * len(losses), 2),
            **blocked,
        })

    return stats


def _blocked_candidate_stats_by_guard(
    guards,
    horizon: str = "4h",
    evaluated_since: str | None = None,
) -> dict[str, dict]:
    """Aggregate successfully blocked candidate outcomes by matched guard id.

    Filter evidence is the only place where a successful hard block and its
    counterfactual coexist. Position attribution cannot observe these rows by
    definition because the candidate never became a position.

    evaluated_since: ISO timestamp; defaults to 90 days ago (guard effectiveness
    is an operational metric; historical blocks beyond that window have low
    actionability). Pass None to scan all history (OOM risk on large DBs).
    """
    from aletheia.learning.outcomes import assess_candidate_outcome
    from aletheia.storage.sqlite import get_db
    from datetime import datetime, timezone, timedelta

    if evaluated_since is None:
        evaluated_since = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

    guard_ids = {guard.guard_id for guard in guards}
    candidates: dict[str, set[str]] = defaultdict(set)
    settled: dict[str, dict[str, float]] = defaultdict(dict)
    try:
        rows = get_db().execute(
            "SELECT fd.input_snapshot, fd.outcome_candidate_key AS decision_outcome_candidate_key, fo.* "
            "FROM filter_decisions fd "
            "LEFT JOIN filter_candidate_outcomes fo "
            "  ON fo.outcome_candidate_key = fd.outcome_candidate_key AND fo.horizon = ? "
            "WHERE fd.filter_name = 'guard_candidate_block' "
            "  AND fd.decision = 'rejected' "
            "  AND fd.outcome_candidate_key IS NOT NULL "
            "  AND datetime(fd.evaluated_at) >= datetime(?) "
            "ORDER BY datetime(fd.evaluated_at)",
            (horizon, evaluated_since),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    for raw in rows:
        row = dict(raw)
        matched_ids = _guard_ids_from_snapshot(row.get("input_snapshot")) & guard_ids
        if not matched_ids:
            continue
        candidate_key = str(row.get("decision_outcome_candidate_key") or row.get("outcome_candidate_key") or "")
        if not candidate_key:
            continue
        assessment = assess_candidate_outcome(row)
        for guard_id in matched_ids:
            candidates[guard_id].add(candidate_key)
            if assessment.eligible and assessment.net_return_pct is not None:
                settled[guard_id].setdefault(candidate_key, assessment.net_return_pct)

    result: dict[str, dict] = {}
    for guard_id in candidates:
        values = list(settled.get(guard_id, {}).values())
        result[guard_id] = {
            "blocked_candidate_count": len(candidates[guard_id]),
            "blocked_settled_n": len(values),
            "blocked_loss_rate": round(sum(1 for value in values if value < 0) / len(values), 4) if values else None,
            "blocked_avg_net_pct": round(sum(values) / len(values), 4) if values else None,
            "blocked_outcome_horizon": horizon,
            "blocked_evidence_source": "filter_candidate_outcomes",
        }
    return result


def _guard_ids_from_snapshot(raw: object) -> set[str]:
    try:
        data = raw if isinstance(raw, dict) else json.loads(str(raw or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return set()
    values = data.get("guard_ids") if isinstance(data, dict) else None
    return {str(value) for value in values or [] if value}


def _empty_blocked_stats() -> dict:
    return {
        "blocked_candidate_count": 0,
        "blocked_settled_n": 0,
        "blocked_loss_rate": None,
        "blocked_avg_net_pct": None,
        "blocked_outcome_horizon": "4h",
        "blocked_evidence_source": "filter_candidate_outcomes",
    }


def _rows_matching_guard(guard, rows: list[dict]) -> list[dict]:
    """Filter attribution rows that would have triggered a guard at entry."""
    from aletheia.knowledge.guards import _applies_to_context, _predicates_match

    matched = []
    for row in rows:
        ctx = _row_to_guard_context(row)
        if not _applies_to_context(guard, ctx):
            continue
        if _predicates_match(guard.predicates, ctx):
            matched.append(row)
    return matched


def _row_to_guard_context(row: dict) -> dict:
    """Convert an attribution row to a guard evaluation context."""
    return {
        "symbol": row.get("symbol"),
        "asset_class": "crypto",
        "direction": row.get("direction"),
        "scanner_family": row.get("scanner_family"),
        "confidence": row.get("confidence"),
        "confidence_score": row.get("confidence_score"),
        "confidence_bucket": row.get("confidence"),
        "late_entry_penalty": row.get("late_entry_penalty"),
        "tradability_score": row.get("tradability_score"),
        "universe_total_score": row.get("universe_total_score"),
        "crowding_risk": row.get("crowding_risk"),
        "risk_flags": row.get("risk_flags", []),
        "factor_status": None,
        "factor_expectancy": None,
        "factor_trade_count": None,
        "strategy_has_ruleset": False,
        "ruleset_entry_result": None,
        "strategy_drift_status": None,
        "paper_expectancy": None,
        "backtest_expectancy": None,
    }
