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


@router.get("/equity-curve")
def get_equity_curve(
    execution_mode: str | None = None,
    days: int | None = None,
    limit: int = 5000,
):
    """Trade-by-trade equity curve for the PnL chart on Overview.

    Query params:
      execution_mode — filter to a single mode (e.g. 'paper_internal', 'okx_demo')
      days — only include positions closed within the last N days
      limit — max data points returned (default 5000)
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()

    conditions = ["status = 'closed'", "closed_at IS NOT NULL"]
    params: list = []
    if execution_mode:
        conditions.append("execution_mode = ?")
        params.append(execution_mode)
    if days and days > 0:
        conditions.append("closed_at >= datetime('now', ?)")
        params.append(f"-{days} days")

    where = " AND ".join(conditions)
    sql = (
        f"SELECT realized_pnl, closed_at "
        f"FROM positions WHERE {where} "
        f"ORDER BY closed_at ASC LIMIT ?"
    )
    params.append(limit)
    rows = db.execute(sql, params).fetchall()

    cum = 0.0
    points = []
    for r in rows:
        pnl = r["realized_pnl"] or 0.0
        cum += pnl
        points.append({
            "time": r["closed_at"],
            "pnl": round(pnl, 4),
            "cumulative_pnl": round(cum, 4),
        })

    # Unrealized PnL for open positions (same mode filter if specified)
    upnl_conditions = ["status='open'"]
    upnl_params: list = []
    if execution_mode:
        upnl_conditions.append("execution_mode = ?")
        upnl_params.append(execution_mode)
    upnl_where = " AND ".join(upnl_conditions)
    open_row = db.execute(
        f"SELECT SUM(unrealized_pnl) as total_upnl FROM positions WHERE {upnl_where}",
        upnl_params,
    ).fetchone()
    total_upnl = round((open_row["total_upnl"] or 0.0), 4)

    # Prefer authoritative cross-broker exchange uPnL (real mark-price + funding)
    # when not filtering by a single mode — keeps the chart's end point consistent
    # with the Overview UNREALIZED card, which also reads UTA uPnL.
    if not execution_mode:
        try:
            from aletheia.portfolio import exchange_sync
            unified = exchange_sync.get_unified_cached_state()
            if unified is not None:
                uta_age = unified.get("age_seconds")
                uta_upnl = unified.get("total_unrealized_pnl")
                if uta_age is not None and uta_age < 600 and uta_upnl is not None:
                    total_upnl = round(float(uta_upnl), 4)
        except Exception:
            pass

    return {
        "points": points,
        "current_unrealized": total_upnl,
        "total_realized": round(cum, 4),
    }


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


@router.get("/go-live-scorecard")
def get_go_live_scorecard():
    """Quantitative go-live readiness scorecard."""
    from aletheia.analytics.go_live_scorecard import compute_scorecard
    return compute_scorecard()


@router.get("/cost-summary")
def get_cost_summary():
    """Aggregate fee/slippage totals across all closed positions."""
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT SUM(COALESCE(entry_fee,0)) as total_entry_fees, "
        "SUM(COALESCE(exit_fee,0)) as total_exit_fees, "
        "SUM(COALESCE(slippage_cost,0)) as total_slippage "
        "FROM positions WHERE status='closed'"
    ).fetchone()
    total_fees = round((row["total_entry_fees"] or 0) + (row["total_exit_fees"] or 0), 8)
    total_slippage = round(row["total_slippage"] or 0, 8)
    return {
        "total_entry_fees": round(row["total_entry_fees"] or 0, 8),
        "total_exit_fees": round(row["total_exit_fees"] or 0, 8),
        "total_fees": total_fees,
        "total_slippage": total_slippage,
        "total_costs": round(total_fees + total_slippage, 8),
    }


@router.get("/confidence-calibration")
def get_confidence_calibration():
    """Conviction calibration report — actual vs expected win rate per conviction bucket."""
    from aletheia.analytics.confidence_calibration import get_latest_calibration_report, build_calibration_report
    report = get_latest_calibration_report()
    if not report:
        report = build_calibration_report()
    return report or {"status": "no_data", "buckets": [], "ece": None, "ece_grade": "insufficient_data"}


@router.get("/reasoning-feedback")
def get_reasoning_feedback():
    """Reasoning quality feedback — proposal rejection patterns and approval win rates."""
    from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback
    return get_latest_reasoning_feedback() or {"status": "no_data"}
