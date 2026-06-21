"""Read-only analytics API routes."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/strategy-evidence")
def get_strategy_evidence():
    """Per-strategy evidence snapshot with recommended actions."""
    from aletheia.analytics.strategy_evidence import build_strategy_evidence_snapshot
    return build_strategy_evidence_snapshot(limit=100)


@router.get("/backtest-paper-drift")
def get_backtest_paper_drift():
    """Backtest vs paper drift analysis per strategy."""
    from aletheia.analytics.backtest_paper_drift import build_backtest_paper_drift
    return build_backtest_paper_drift(limit=100)


@router.get("/exit-policy-advice")
def get_exit_policy_advice():
    """Exit policy diagnosis and suggestions."""
    from aletheia.analytics.exit_policy_advisor import build_exit_policy_advice
    return build_exit_policy_advice(limit=500)


@router.get("/trade-paths")
def get_trade_paths():
    """MFE/MAE trade path analysis for closed trades."""
    from aletheia.analytics.trade_path import build_trade_path_rows
    return build_trade_path_rows(limit=200)


@router.get("/trade-stats")
def get_trade_stats():
    """Trade analytics: by-source and by-family aggregation for the Analytics panel."""
    from aletheia.analytics.trade_stats import build_trade_stats
    return build_trade_stats()


@router.get("/strategy-kpis")
def get_strategy_kpis():
    """Per-strategy KPI summary: trade_count, win_rate, total_pnl."""
    from aletheia.analytics.trade_stats import build_strategy_kpis
    return build_strategy_kpis()


@router.get("/trade-insights")
def get_trade_insights():
    """Comprehensive trade insights for charts: equity curve, drawdown, direction split, etc."""
    from aletheia.analytics.trade_insights import build_trade_insights
    return build_trade_insights()


@router.get("/backtest/runs")
def get_backtest_runs(strategy_id: str | None = None, limit: int = 20):
    """List backtest runs, optionally filtered by strategy."""
    from aletheia.strategy.repositories import list_backtest_runs
    runs = list_backtest_runs(strategy_id=strategy_id, limit=limit)
    for r in runs:
        r.pop("equity_curve_json", None)
        r.pop("ruleset_json", None)
    return runs


@router.get("/backtest/runs/{run_id}")
def get_backtest_run_detail(run_id: str):
    """Single backtest run with equity curve and trades."""
    import json as _json
    from aletheia.strategy.repositories import get_backtest_run, list_backtest_trades
    run = get_backtest_run(run_id)
    if not run:
        from fastapi import HTTPException
        raise HTTPException(404, "backtest run not found")
    curve_raw = run.pop("equity_curve_json", None)
    run["equity_curve"] = _json.loads(curve_raw) if curve_raw else []
    run["trades"] = list_backtest_trades(run_id, limit=500)
    return run


@router.get("/backtest/compare/{strategy_id}")
def get_backtest_compare(strategy_id: str):
    """Latest backtest equity curve vs paper trade PnL for a strategy."""
    import json as _json
    from aletheia.strategy.repositories import list_backtest_runs
    from aletheia.storage.repositories import list_strategy_positions

    runs = list_backtest_runs(strategy_id=strategy_id, limit=1)
    if not runs:
        return {"strategy_id": strategy_id, "backtest": [], "paper": []}

    run = runs[0]
    curve_raw = run.get("equity_curve_json")
    backtest_curve = _json.loads(curve_raw) if curve_raw else []

    # Paper: closed positions for this strategy, cumulative PnL
    positions = list_strategy_positions(strategy_id, status="closed", limit=500)
    positions.sort(key=lambda p: p.get("closed_at") or "")
    cumulative = 100.0
    paper_curve = [cumulative]
    for p in positions:
        pnl_pct = float(p.get("realized_pnl_pct") or 0)
        cumulative = round(cumulative * (1 + pnl_pct / 100), 4)
        paper_curve.append(cumulative)

    return {"strategy_id": strategy_id, "backtest": backtest_curve, "paper": paper_curve}
