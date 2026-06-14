"""Backtest vs paper drift — compare backtest results against forward paper evidence.

Answers: Did the strategy only look good in backtest, or is it also working in
chronological paper trading?

Read-only. No DB mutations. No LLM calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from aletheia.analytics.trade_attribution import build_trade_attribution_rows
from aletheia.analytics.trade_path import build_trade_path_rows
from aletheia.storage.repositories import list_strategies
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.analytics.backtest_paper_drift")


def build_backtest_paper_drift(limit: int = 200, min_paper_trades: int = 3) -> list[dict]:
    """Build backtest-vs-paper drift analysis for each strategy.

    Returns one row per strategy with both backtest summary and paper
    performance, plus drift classification.
    """
    strategies = list_strategies(limit=limit)
    if not strategies:
        return []

    # Pre-fetch all paper attribution and path rows
    paper_rows = build_trade_attribution_rows(limit=500)
    path_rows = build_trade_path_rows(limit=500)

    # Index by strategy_id
    paper_by_strategy: dict[str, list[dict]] = {}
    for r in paper_rows:
        sid = r.get("strategy_id")
        if sid:
            paper_by_strategy.setdefault(sid, []).append(r)

    path_by_strategy: dict[str, list[dict]] = {}
    for r in path_rows:
        sid = r.get("strategy_id")
        if sid:
            path_by_strategy.setdefault(sid, []).append(r)

    results: list[dict] = []
    for strat in strategies:
        sid = strat["id"]
        row = _build_drift_row(
            strat,
            paper_by_strategy.get(sid, []),
            path_by_strategy.get(sid, []),
            min_paper_trades,
        )
        results.append(row)

    return results


def _build_drift_row(
    strat: dict,
    paper_rows: list[dict],
    path_rows: list[dict],
    min_paper_trades: int,
) -> dict:
    """Build drift row for a single strategy."""
    sid = strat["id"]
    params_raw = strat.get("parameters") or "{}"
    try:
        params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
    except (ValueError, TypeError):
        params = {}

    has_ruleset = bool(params.get("ruleset"))
    symbol = params.get("symbol") or strat.get("symbol") or _infer_symbol(paper_rows)
    timeframe = params.get("timeframe") or "1h"

    # Latest backtest
    bt = _get_latest_backtest(sid)

    # Paper summary
    paper_count = len(paper_rows)
    paper_wins = [r for r in paper_rows if (r.get("realized_pnl") or 0) > 0]
    paper_losses = [r for r in paper_rows if (r.get("realized_pnl") or 0) < 0]
    paper_win_rate = len(paper_wins) / paper_count if paper_count else 0.0
    paper_total_pnl = sum(r.get("realized_pnl", 0) for r in paper_rows)

    paper_avg_win = (
        sum(r.get("realized_pnl", 0) for r in paper_wins) / len(paper_wins)
        if paper_wins else 0.0
    )
    paper_avg_loss = (
        sum(abs(r.get("realized_pnl", 0)) for r in paper_losses) / len(paper_losses)
        if paper_losses else 0.0
    )
    paper_expectancy = paper_avg_win * paper_win_rate - paper_avg_loss * (1 - paper_win_rate)

    paper_gross_profit = sum(r.get("realized_pnl", 0) for r in paper_wins)
    paper_gross_loss = sum(abs(r.get("realized_pnl", 0)) for r in paper_losses)
    paper_profit_factor = (
        paper_gross_profit / paper_gross_loss if paper_gross_loss > 0
        else (999.0 if paper_gross_profit > 0 else 0.0)
    )

    # Path-based metrics
    paper_avg_mfe = _avg_field(path_rows, "mfe_pct")
    paper_avg_mae = _avg_field(path_rows, "mae_pct")

    # Drift calculation
    bt_expectancy = float(bt["expectancy"]) if bt else None
    drift_expectancy = None
    paper_to_backtest_ratio = None
    if bt_expectancy is not None and bt_expectancy != 0 and paper_count >= min_paper_trades:
        drift_expectancy = paper_expectancy - bt_expectancy
        paper_to_backtest_ratio = paper_expectancy / bt_expectancy if bt_expectancy != 0 else None

    # Drift status classification
    drift_status, drift_reasons = _classify_drift(
        bt, paper_count, min_paper_trades, bt_expectancy,
        paper_expectancy, paper_to_backtest_ratio,
    )

    return {
        "strategy_id": sid,
        "strategy_name": strat.get("name"),
        "state": strat.get("state"),
        "symbol": symbol,
        "timeframe": timeframe,
        "has_ruleset": has_ruleset,
        # Backtest summary
        "latest_backtest_run_id": bt["id"] if bt else None,
        "backtest_created_at": bt["created_at"] if bt else None,
        "backtest_trade_count": int(bt["trade_count"]) if bt else None,
        "backtest_expectancy": float(bt["expectancy"]) if bt else None,
        "backtest_win_rate": float(bt["win_rate"]) if bt else None,
        "backtest_profit_factor": float(bt["profit_factor"]) if bt else None,
        "backtest_max_drawdown_pct": float(bt["max_drawdown_pct"]) if bt else None,
        "backtest_total_return_pct": float(bt["total_return_pct"]) if bt else None,
        # Paper summary
        "paper_trade_count": paper_count,
        "paper_expectancy": round(paper_expectancy, 4),
        "paper_win_rate": round(paper_win_rate, 4),
        "paper_total_pnl": round(paper_total_pnl, 4),
        "paper_profit_factor": round(paper_profit_factor, 4),
        "paper_avg_mfe_pct": paper_avg_mfe,
        "paper_avg_mae_pct": paper_avg_mae,
        # Drift
        "drift_expectancy": round(drift_expectancy, 4) if drift_expectancy is not None else None,
        "paper_to_backtest_ratio": round(paper_to_backtest_ratio, 4) if paper_to_backtest_ratio is not None else None,
        "drift_status": drift_status,
        "drift_reasons": drift_reasons,
    }


def _classify_drift(
    bt: dict | None,
    paper_count: int,
    min_paper_trades: int,
    bt_expectancy: float | None,
    paper_expectancy: float,
    paper_to_backtest_ratio: float | None,
) -> tuple[str, list[str]]:
    """Classify drift status."""
    reasons: list[str] = []

    if bt is None:
        return "no_backtest", ["no_backtest_run_found"]

    if paper_count == 0:
        return "no_paper_evidence", ["zero_paper_trades"]

    if paper_count < min_paper_trades:
        return "paper_not_enough", [f"paper_count={paper_count}<{min_paper_trades}"]

    if bt_expectancy is None:
        return "no_backtest", ["backtest_expectancy_unavailable"]

    if bt_expectancy > 0 and paper_expectancy < 0:
        reasons.append(f"bt_exp={bt_expectancy:.4f}_paper_exp={paper_expectancy:.4f}")
        return "backtest_false_positive", reasons

    if bt_expectancy <= 0 and paper_expectancy <= 0:
        reasons.append("both_negative_expectancy")
        return "backtest_bad_and_paper_bad", reasons

    if paper_expectancy > bt_expectancy:
        reasons.append(f"paper_outperforms_bt_by={paper_expectancy - bt_expectancy:.4f}")
        return "paper_outperforming", reasons

    if paper_to_backtest_ratio is not None and paper_to_backtest_ratio < 0.5:
        reasons.append(f"ratio={paper_to_backtest_ratio:.2f}<0.5")
        return "paper_underperforming", reasons

    if paper_to_backtest_ratio is not None and paper_to_backtest_ratio >= 0.5:
        reasons.append(f"ratio={paper_to_backtest_ratio:.2f}>=0.5")
        return "paper_confirming", reasons

    return "paper_confirming", ["default_classification"]


def _get_latest_backtest(strategy_id: str) -> dict | None:
    """Get latest completed backtest run for a strategy."""
    try:
        db = get_db()
        cursor = db.execute(
            "SELECT * FROM backtest_runs WHERE strategy_id = ? AND status = 'completed' "
            "ORDER BY created_at DESC LIMIT 1",
            (strategy_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _infer_symbol(paper_rows: list[dict]) -> str | None:
    """Infer symbol from paper rows."""
    if paper_rows:
        return paper_rows[0].get("symbol")
    return None


def _avg_field(rows: list[dict], field: str) -> float | None:
    """Average a numeric field, skipping None values."""
    values = [r[field] for r in rows if r.get(field) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)
