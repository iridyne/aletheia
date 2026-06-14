"""Strategy evidence snapshot — deterministic per-strategy evidence for lifecycle decisions.

Answers: Should this strategy continue paper trading, pause, be reviewed, or be retired?

Read-only. No DB mutations. No LLM calls. No auto-actions.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aletheia.analytics.backtest_paper_drift import build_backtest_paper_drift
from aletheia.analytics.factor_feedback import build_factor_feedback
from aletheia.analytics.trade_path import build_trade_path_rows
from aletheia.storage.repositories import list_strategies

logger = logging.getLogger("aletheia.analytics.strategy_evidence")


def build_strategy_evidence_snapshot(limit: int = 200) -> list[dict]:
    """Build evidence snapshot for all strategies.

    Returns one row per strategy with combined evidence from backtest,
    paper trading, path analysis, and factor feedback.
    """
    strategies = list_strategies(limit=limit)
    if not strategies:
        return []

    # Pre-compute shared analytics
    drift_rows = build_backtest_paper_drift(limit=limit)
    drift_by_strategy = {r["strategy_id"]: r for r in drift_rows}

    path_rows = build_trade_path_rows(limit=500)
    path_by_strategy: dict[str, list[dict]] = {}
    for r in path_rows:
        sid = r.get("strategy_id")
        if sid:
            path_by_strategy.setdefault(sid, []).append(r)

    factor_rows = build_factor_feedback(limit=500)

    results: list[dict] = []
    for strat in strategies:
        sid = strat["id"]
        row = _build_evidence_row(
            strat,
            drift_by_strategy.get(sid),
            path_by_strategy.get(sid, []),
            factor_rows,
        )
        results.append(row)

    return results


def _build_evidence_row(
    strat: dict,
    drift: dict | None,
    path_rows: list[dict],
    factor_rows: list[dict],
) -> dict:
    """Build evidence row for a single strategy."""
    sid = strat["id"]
    params_raw = strat.get("parameters") or "{}"
    try:
        params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
    except (ValueError, TypeError):
        params = {}

    has_ruleset = bool(params.get("ruleset"))
    compile_status = "ok" if has_ruleset else "no_ruleset"
    if has_ruleset:
        try:
            from aletheia.strategy.dsl import RuleSet
            RuleSet.from_json(params["ruleset"])
        except Exception:
            compile_status = "compile_error"

    # Path summary
    path_summary = _summarize_paths(path_rows)

    # Factor summary for this strategy's scanner
    factor_summary = _summarize_factors(factor_rows, sid)

    # Drift summary
    drift_summary = None
    if drift:
        drift_summary = {
            "drift_status": drift.get("drift_status"),
            "paper_trade_count": drift.get("paper_trade_count"),
            "paper_expectancy": drift.get("paper_expectancy"),
            "backtest_expectancy": drift.get("backtest_expectancy"),
            "paper_to_backtest_ratio": drift.get("paper_to_backtest_ratio"),
        }

    # Paper summary
    paper_summary = None
    if drift:
        paper_summary = {
            "trade_count": drift.get("paper_trade_count"),
            "expectancy": drift.get("paper_expectancy"),
            "win_rate": drift.get("paper_win_rate"),
            "total_pnl": drift.get("paper_total_pnl"),
            "profit_factor": drift.get("paper_profit_factor"),
        }

    # Backtest summary
    latest_backtest = None
    if drift and drift.get("latest_backtest_run_id"):
        latest_backtest = {
            "run_id": drift.get("latest_backtest_run_id"),
            "trade_count": drift.get("backtest_trade_count"),
            "expectancy": drift.get("backtest_expectancy"),
            "win_rate": drift.get("backtest_win_rate"),
            "profit_factor": drift.get("backtest_profit_factor"),
            "max_drawdown_pct": drift.get("backtest_max_drawdown_pct"),
            "total_return_pct": drift.get("backtest_total_return_pct"),
        }

    # Evidence status classification
    evidence_status = _classify_evidence_status(
        has_ruleset, compile_status, latest_backtest, paper_summary, drift,
    )

    # Recommended action
    action, action_reasons = _recommend_action(evidence_status, drift, paper_summary, path_summary)

    return {
        "strategy_id": sid,
        "strategy_name": strat.get("name"),
        "state": strat.get("state"),
        "under_review": strat.get("under_review", False),
        "paper_trading_paused": strat.get("paper_trading_paused", False),
        "created_at": strat.get("created_at"),
        "updated_at": strat.get("updated_at"),
        "symbol": params.get("symbol") or drift.get("symbol") if drift else None,
        "timeframe": params.get("timeframe") or "1h",
        "direction_bias": params.get("direction") or params.get("direction_bias"),
        "has_ruleset": has_ruleset,
        "compile_status": compile_status,
        "latest_backtest": latest_backtest,
        "paper_summary": paper_summary,
        "path_summary": path_summary,
        "drift_summary": drift_summary,
        "factor_summary": factor_summary,
        "evidence_status": evidence_status,
        "recommended_action": action,
        "recommended_action_reasons": action_reasons,
    }


def _classify_evidence_status(
    has_ruleset: bool,
    compile_status: str,
    latest_backtest: dict | None,
    paper_summary: dict | None,
    drift: dict | None,
) -> str:
    """Classify strategy evidence status."""
    if not has_ruleset:
        return "not_executable"

    if compile_status == "compile_error":
        return "not_executable"

    if not latest_backtest:
        return "needs_backtest"

    bt_exp = latest_backtest.get("expectancy")
    bt_pf = latest_backtest.get("profit_factor")
    if bt_exp is not None and bt_exp < 0:
        return "backtest_killed"
    if bt_pf is not None and bt_pf < 0.8:
        return "backtest_killed"

    paper_count = paper_summary.get("trade_count", 0) if paper_summary else 0
    if paper_count < 3:
        return "needs_paper"

    paper_exp = paper_summary.get("expectancy", 0) if paper_summary else 0
    if paper_exp < 0:
        return "paper_failing"

    drift_status = drift.get("drift_status") if drift else None
    if drift_status in ("paper_confirming", "paper_outperforming"):
        return "paper_promising"

    return "review_needed"


def _recommend_action(
    evidence_status: str,
    drift: dict | None,
    paper_summary: dict | None,
    path_summary: dict | None,
) -> tuple[str, list[str]]:
    """Generate deterministic recommendation."""
    reasons: list[str] = []

    if evidence_status == "not_executable":
        return "needs_executable_ruleset", ["no_compilable_ruleset"]

    if evidence_status == "needs_backtest":
        return "needs_backtest", ["has_ruleset_but_no_backtest_run"]

    if evidence_status == "backtest_killed":
        reasons.append("backtest_negative_expectancy_or_low_pf")
        return "retire_candidate", reasons

    if evidence_status == "needs_paper":
        return "needs_more_paper", ["backtest_ok_but_insufficient_paper_evidence"]

    if evidence_status == "paper_failing":
        drift_status = drift.get("drift_status") if drift else None
        if drift_status == "backtest_false_positive":
            reasons.append("backtest_false_positive")
            return "retire_candidate", reasons
        reasons.append("paper_expectancy_negative")
        return "pause_paper_trading", reasons

    if evidence_status == "paper_promising":
        reasons.append("paper_confirming_backtest")
        return "keep_running", reasons

    # review_needed
    reasons.append("borderline_or_conflicting_evidence")
    if path_summary and path_summary.get("dominant_bucket") == "bad_entry":
        reasons.append("entry_quality_problem")
    return "mark_under_review", reasons


def _summarize_paths(path_rows: list[dict]) -> dict | None:
    """Summarize path analysis for a strategy."""
    if not path_rows:
        return None

    buckets = [r.get("path_bucket") for r in path_rows if r.get("path_bucket")]
    if not buckets:
        return None

    from collections import Counter
    bucket_counts = Counter(buckets)
    dominant = bucket_counts.most_common(1)[0][0]

    mfe_values = [r["mfe_pct"] for r in path_rows if r.get("mfe_pct") is not None]
    mae_values = [r["mae_pct"] for r in path_rows if r.get("mae_pct") is not None]

    return {
        "trade_count": len(path_rows),
        "dominant_bucket": dominant,
        "bucket_distribution": dict(bucket_counts),
        "avg_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
        "avg_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
    }


def _summarize_factors(factor_rows: list[dict], strategy_id: str) -> dict | None:
    """Summarize factor feedback relevant to this strategy."""
    # Find scanner-level factors
    scanner_factors = [f for f in factor_rows if f.get("group_type") == "scanner"]
    if not scanner_factors:
        return None

    blocked = [f for f in scanner_factors if f.get("status") == "blocked_candidate"]
    preferred = [f for f in scanner_factors if f.get("status") == "preferred"]

    return {
        "total_factors": len(scanner_factors),
        "blocked_count": len(blocked),
        "preferred_count": len(preferred),
    }
