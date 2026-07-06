"""Guard Effectiveness Analytics.

Tracks how guards perform: how often they fire, what they blocked/penalized,
and whether blocked trades would have been losses (counterfactual validation).

Deterministic. No LLM. Read-only (queries attribution + ranker logs).
"""
from __future__ import annotations

import logging
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
    if not rows:
        return []

    stats = []
    for guard in guards:
        fired_rows = _rows_matching_guard(guard, rows)
        if not fired_rows:
            stats.append({
                "guard_id": guard.guard_id,
                "title": guard.title,
                "fire_count": 0,
                "loss_rate_when_fired": 0.0,
                "avg_loss_pct": 0.0,
                "estimated_savings": 0.0,
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
        })

    return stats


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
