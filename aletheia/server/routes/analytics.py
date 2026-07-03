"""Read-only analytics API routes."""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


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
    from aletheia.config.loader import load_config
    db = get_db()

    mode = "full_cumulative"
    try:
        mode = load_config().app.equity_curve_mode
    except Exception:
        pass

    # In full_cumulative mode the running total must include trades BEFORE the
    # visible window, so we never filter the accumulation by `days` — we compute
    # cumulative PnL over all history, then slice the window for display. That
    # keeps the curve's end point identical across 7D/30D/90D/All (a real equity
    # curve). In window_relative mode the cumulative restarts at 0 inside the
    # window, so we DO filter by days at the SQL level (period-attribution view).
    conditions = ["status = 'closed'", "closed_at IS NOT NULL"]
    params: list = []
    if execution_mode:
        conditions.append("execution_mode = ?")
        params.append(execution_mode)
    if days and days > 0 and mode == "window_relative":
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

    # Window cutoff (full_cumulative only): accumulate over everything but emit
    # points whose closed_at is within the requested window. Compared as ISO
    # strings — both sides are tz-aware UTC isoformat, lexicographically ordered.
    cutoff: str | None = None
    if days and days > 0 and mode == "full_cumulative":
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    cum = 0.0
    points = []
    for r in rows:
        pnl = r["realized_pnl"] or 0.0
        cum += pnl  # always over full history in full_cumulative mode
        if cutoff is not None and (r["closed_at"] or "") < cutoff:
            continue  # accumulated into cum, but outside the visible window
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


@router.get("/strategy-performance")
def get_strategy_performance():
    """Per-strategy detailed performance KPIs. Backtest run summary removed with
    the rules engine (Position C 2026-07-02); latest_backtest kept as an empty
    object for response-shape back-compat."""
    from aletheia.analytics.trade_stats import build_strategy_kpis
    return {"strategies": build_strategy_kpis(), "latest_backtest": {}}


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


# --- Round 1 progress / pipeline-funnel / learning-funnel ---
# Read-only observability endpoints added 2026-06-24 to make the freeze-period
# experiment status visible without manual SQL. None of these affect AI decisions.

_ROUND1_START_UTC = "2026-06-22"
_G6_TARGET_TRADES = 60
_PF_GO_LINE = 1.5
_WR_GO_LINE = 50.0


@router.get("/round1-progress")
def get_round1_progress():
    """Round 1 experiment progress vs the Go/Kill thresholds.

    Read-only aggregation of `positions` since the Round 1 start date.
    Designed so `aletheia doctor` and the UI share one source of truth.
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()
    row = db.execute(
        "SELECT "
        "  COUNT(*) AS closed_trades, "
        "  COALESCE(ROUND(SUM(realized_pnl), 4), 0) AS total_realized, "
        "  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) AS wins, "
        "  COALESCE(SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END), 0) AS losses, "
        "  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit, "
        "  COALESCE(-SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0) AS gross_loss, "
        "  MIN(closed_at) AS first_closed_at, "
        "  MAX(closed_at) AS last_closed_at "
        "FROM positions WHERE status='closed' AND closed_at >= ?",
        (_ROUND1_START_UTC,),
    ).fetchone()
    closed = row["closed_trades"] or 0
    wins = row["wins"] or 0
    gross_profit = float(row["gross_profit"] or 0)
    gross_loss = float(row["gross_loss"] or 0)
    win_rate = round(wins / closed * 100.0, 2) if closed else None
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 0 else (
        None if gross_profit == 0 else float("inf")
    )

    # by_close_reason breakdown — judgement-critical: lets the UI / a Round 1
    # reviewer distinguish AI-driven exits (stop_loss / take_profit / pi_close /
    # manual_close / flip) from reconciler sweeps (reconciled_gone) and pre-Round
    # bug cleanup (units_bug_cleanup). See ROUND1_JUDGMENT_PREVIEW.md §1D for
    # why the headline PF / WR above can be dominated by non-AI events.
    by_reason: list[dict] = []
    for r in db.execute(
        "SELECT close_reason, COUNT(*) n, "
        "  COALESCE(ROUND(SUM(realized_pnl), 4), 0) net, "
        "  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) wins "
        "FROM positions WHERE status='closed' AND closed_at >= ? "
        "GROUP BY close_reason ORDER BY n DESC",
        (_ROUND1_START_UTC,),
    ):
        by_reason.append({
            "close_reason": r["close_reason"],
            "n": int(r["n"]),
            "net_realized_pnl": float(r["net"]),
            "wins": int(r["wins"]),
        })

    # Strict-cohort: only count AI-driven exits (skip reconciler sweeps + bug cleanups).
    _AI_REASONS = ("stop_loss", "take_profit", "pi_close", "manual_close", "flip")
    placeholders = ",".join("?" * len(_AI_REASONS))
    strict = db.execute(
        f"SELECT COUNT(*) n, "
        f"  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) wins, "
        f"  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) gp, "
        f"  COALESCE(-SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0) gl "
        f"FROM positions WHERE status='closed' AND closed_at >= ? "
        f"  AND close_reason IN ({placeholders})",
        (_ROUND1_START_UTC, *_AI_REASONS),
    ).fetchone()
    s_n = int(strict["n"])
    s_gp = float(strict["gp"])
    s_gl = float(strict["gl"])
    s_wins = int(strict["wins"])
    strict_pf = round(s_gp / s_gl, 4) if s_gl > 0 else (None if s_gp == 0 else float("inf"))
    strict_wr = round(s_wins / s_n * 100.0, 2) if s_n else None

    # Half-strict: drop only units_bug_cleanup (Round 1 pre-bug cleanup), keep reconciler.
    hs = db.execute(
        "SELECT COUNT(*) n, "
        "  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END), 0) wins, "
        "  COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) gp, "
        "  COALESCE(-SUM(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END), 0) gl "
        "FROM positions WHERE status='closed' AND closed_at >= ? "
        "  AND close_reason != 'units_bug_cleanup'",
        (_ROUND1_START_UTC,),
    ).fetchone()
    hs_n = int(hs["n"])
    hs_gp = float(hs["gp"])
    hs_gl = float(hs["gl"])
    hs_wins = int(hs["wins"])
    hs_pf = round(hs_gp / hs_gl, 4) if hs_gl > 0 else (None if hs_gp == 0 else float("inf"))
    hs_wr = round(hs_wins / hs_n * 100.0, 2) if hs_n else None

    return {
        "round1_start_utc": _ROUND1_START_UTC,
        "closed_trades": closed,
        "g6_target": _G6_TARGET_TRADES,
        "g6_gap": max(0, _G6_TARGET_TRADES - closed),
        "total_realized_pnl": float(row["total_realized"] or 0),
        "wins": wins,
        "losses": row["losses"] or 0,
        "win_rate_pct": win_rate,
        "wr_go_line_pct": _WR_GO_LINE,
        "profit_factor": profit_factor,
        "pf_go_line": _PF_GO_LINE,
        "first_closed_at": row["first_closed_at"],
        "last_closed_at": row["last_closed_at"],
        "by_close_reason": by_reason,
        "cohorts": {
            "raw": {
                "n": closed, "wr_pct": win_rate, "pf": profit_factor,
                "note": "all close_reason — judgement-noisy",
            },
            "half_strict": {
                "n": hs_n, "wr_pct": hs_wr, "pf": hs_pf,
                "note": "excludes units_bug_cleanup (pre-Round bug)",
            },
            "strict_ai_driven": {
                "n": s_n, "wr_pct": strict_wr, "pf": strict_pf,
                "note": f"only {','.join(_AI_REASONS)} — strongest AI-decision signal",
            },
        },
    }


@router.get("/pipeline-funnel")
def get_pipeline_funnel(hours: int = 24):
    """24h profit-loop funnel: opportunities → signals → PI → risk → fills.

    Answers \"why didn't we open positions in the last N hours?\" by quantifying
    the conversion rate at each stage. None of the data here affects PI decisions.
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()
    window = f"-{int(hours)} hours"

    def _count(sql: str) -> int:
        try:
            return int(db.execute(sql, (window,)).fetchone()[0])
        except Exception:
            return 0

    opps = _count("SELECT COUNT(*) FROM opportunities WHERE created_at >= datetime('now', ?)")
    sig_pending = _count(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='signal_pending' "
        "AND created_at >= datetime('now', ?)"
    )
    pi_trade = _count(
        "SELECT COUNT(*) FROM pi_decisions WHERE action IN ('trade','scale_in','flip') "
        "AND created_at >= datetime('now', ?)"
    )
    pi_skip = _count(
        "SELECT COUNT(*) FROM pi_decisions WHERE action='skip' "
        "AND created_at >= datetime('now', ?)"
    )

    # Risk decisions: count approved/rejected + top rejection violations.
    try:
        rd_rows = list(db.execute(
            "SELECT approved, COUNT(*) n FROM risk_decisions "
            "WHERE created_at >= datetime('now', ?) GROUP BY approved",
            (window,),
        ))
        risk_approved = next((int(r["n"]) for r in rd_rows if r["approved"] == 1), 0)
        risk_rejected = next((int(r["n"]) for r in rd_rows if r["approved"] == 0), 0)
    except Exception:
        risk_approved = risk_rejected = 0

    rejection_top: list[dict] = []
    try:
        for r in db.execute(
            "SELECT message, COUNT(*) n FROM risk_decisions "
            "WHERE approved=0 AND created_at >= datetime('now', ?) "
            "GROUP BY message ORDER BY n DESC LIMIT 5",
            (window,),
        ):
            rejection_top.append({"message": (r["message"] or "")[:200], "count": int(r["n"])})
    except Exception:
        pass

    fills = 0
    try:
        # fills has filled_at not created_at
        fills = int(db.execute(
            "SELECT COUNT(*) FROM fills WHERE filled_at >= datetime('now', ?)",
            (window,),
        ).fetchone()[0])
    except Exception:
        fills = 0

    risk_total = risk_approved + risk_rejected
    risk_approve_rate = round(risk_approved / risk_total * 100.0, 2) if risk_total else None

    return {
        "window_hours": hours,
        "opportunities": opps,
        "signal_pending": sig_pending,
        "pi_decisions": {
            "trade": pi_trade,
            "skip": pi_skip,
        },
        "risk_decisions": {
            "approved": risk_approved,
            "rejected": risk_rejected,
            "approve_rate_pct": risk_approve_rate,
            "top_rejection_reasons": rejection_top,
        },
        "fills": fills,
        "conversion": {
            "opp_to_signal_pct": round(sig_pending / opps * 100.0, 2) if opps else None,
            "signal_to_pi_trade_pct": round(pi_trade / sig_pending * 100.0, 2) if sig_pending else None,
            "pi_trade_to_fill_pct": round(fills / pi_trade * 100.0, 2) if pi_trade else None,
        },
    }


@router.get("/learning-funnel")
def get_learning_funnel():
    """Learning subsystem visibility: what gets written vs what PI actually sees.

    Quantifies the freeze-period \"writes active, consumption blocked\" pattern
    (knowledge_items 24h writes vs PI's limit=30 window contents). Read-only.
    """
    from aletheia.storage.sqlite import get_db
    db = get_db()

    # Full-table item_type distribution
    type_total: list[dict] = []
    for r in db.execute(
        "SELECT item_type, COUNT(*) n FROM knowledge_items GROUP BY item_type ORDER BY n DESC"
    ):
        type_total.append({"item_type": r["item_type"], "count": int(r["n"])})

    # Most recent 30 (what PI's _build_layer3_wisdom guards-window actually sees)
    pi_window_30: list[dict] = []
    from collections import Counter
    cnt: Counter = Counter()
    try:
        for r in db.execute(
            "SELECT item_type FROM knowledge_items ORDER BY rowid DESC LIMIT 30"
        ):
            cnt[r["item_type"]] += 1
        pi_window_30 = [{"item_type": k, "count": v} for k, v in cnt.most_common()]
    except Exception:
        pass

    # Most recent 20 (what PI's lessons-window sees)
    pi_window_20: list[dict] = []
    cnt20: Counter = Counter()
    try:
        for r in db.execute(
            "SELECT item_type FROM knowledge_items ORDER BY rowid DESC LIMIT 20"
        ):
            cnt20[r["item_type"]] += 1
        pi_window_20 = [{"item_type": k, "count": v} for k, v in cnt20.most_common()]
    except Exception:
        pass

    # ps_lessons summary (status + freshness)
    ps_summary: dict = {"total": 0, "by_status": [], "latest_created_at": None}
    try:
        ps_summary["total"] = int(db.execute("SELECT COUNT(*) FROM ps_lessons").fetchone()[0])
        for r in db.execute("SELECT status, COUNT(*) n FROM ps_lessons GROUP BY status"):
            ps_summary["by_status"].append({"status": r["status"], "count": int(r["n"])})
        ps_summary["latest_created_at"] = db.execute(
            "SELECT MAX(created_at) FROM ps_lessons"
        ).fetchone()[0]
    except Exception:
        pass

    # Serenity opportunities active count (already directly consumed by PI; useful as reference)
    serenity_active = 0
    try:
        serenity_active = int(db.execute(
            "SELECT COUNT(*) FROM serenity_opportunities WHERE status='active'"
        ).fetchone()[0])
    except Exception:
        pass

    # Layer3 actual visibility: what PI really reads each cycle.
    # L-Bug-A fix (b9ad957): layer3 lessons = trade_case_study (not the old
    # 'lesson'/'accepted_lesson'/'trading_guard' types that had no producers).
    # L-Bug-A' fix: guard_candidate queried by item_type directly (not limit window).
    layer3_lesson_hits = int(db.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE item_type = 'trade_case_study'"
    ).fetchone()[0])
    # PI reads up to 3 guard_candidates by item_type filter (L-Bug-A' fix).
    # Count from full table (cnt only covers last 30 rows and guard_candidate is rare there).
    total_by_type = {row["item_type"]: row["count"] for row in type_total}
    layer3_guards_in_window = min(int(total_by_type.get("guard_candidate", 0)), 3)

    contradictions: list[dict] = []
    try:
        from aletheia.knowledge.manager import detect_contradictions
        from aletheia.knowledge.guards import load_trading_guards

        contradiction_items = [
            dict(r) for r in db.execute(
                "SELECT id, item_type, title, confidence, status, tags, file_path "
                "FROM knowledge_items "
                "WHERE status='active' "
                "AND item_type IN ("
                "'research_note','strategy_memory','trading_memo','trading_guard',"
                "'guard_candidate','trade_case_study','calibration_report'"
                ") "
                "ORDER BY rowid DESC LIMIT 500"
            )
        ]
        for guard in load_trading_guards(status="active"):
            meta = guard.model_dump()
            applies = meta.get("applies_to") or {}
            symbols = applies.get("symbol") or [None]
            directions = applies.get("direction") or [None]
            for symbol in symbols:
                for direction in directions:
                    contradiction_items.append({
                        "id": guard.guard_id,
                        "item_type": "trading_guard",
                        "title": guard.title,
                        "status": guard.status,
                        "metadata": {
                            "symbol": symbol,
                            "direction": direction,
                            "block_candidate": guard.block_candidate,
                            "enforcement": guard.enforcement,
                        },
                    })
        contradictions = detect_contradictions(contradiction_items)
    except Exception:
        contradictions = []

    return {
        "knowledge_items_by_type": type_total,
        "pi_lessons_window_limit_20_distribution": pi_window_20,
        "pi_guards_window_limit_30_distribution": pi_window_30,
        "pi_actual_visibility": {
            "layer3_lesson_hits": layer3_lesson_hits,
            "layer3_guards_available": layer3_guards_in_window,
            "note": "PI reads up to 7 trade_case_study + up to 3 guard_candidate per cycle (L-Bug-A/A' fixed b9ad957)",
        },
        "ps_lessons": ps_summary,
        "serenity_opportunities_active": serenity_active,
        "contradictions": {
            "checked": True,
            "count": len(contradictions),
            "items": contradictions[:10],
            "note": "Conservative support-vs-block detector; findings require human review.",
        },
    }
