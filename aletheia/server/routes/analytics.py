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
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    from aletheia.analytics.trade_stats import build_strategy_kpis
    return {"strategies": build_strategy_kpis(), **build_outcome_cohort_summary()}


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
    from aletheia.analytics.outcome_integrity import (
        assess_reportable_outcome,
        build_outcome_cohort_summary,
        raw_account_events,
    )
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    db = get_db()

    mode = "full_cumulative"
    uses_external_exchange = False
    try:
        config = load_config()
        mode = config.app.equity_curve_mode
        uses_external_exchange = config.exchange.paper_execution_mode != "paper_internal"
    except Exception:
        pass

    # In full_cumulative mode the running total must include trades BEFORE the
    # visible window, so we never filter the accumulation by `days` — we compute
    # cumulative PnL over all history, then slice the window for display. That
    # keeps the curve's end point identical across 7D/30D/90D/All (a real equity
    # curve). In window_relative mode the cumulative restarts at 0 inside the
    # window, so we DO filter by days at the SQL level (period-attribution view).
    cutoff: str | None = None
    if days and days > 0:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    read_after = cutoff if mode == "window_relative" else None
    rows = raw_account_events(
        execution_mode=execution_mode,
        closed_after=read_after,
    )
    rows = rows[-max(1, min(limit, 5000)):]
    genuine_rows = [row for row in rows if assess_reportable_outcome(row).eligible]

    def _points(source_rows: list[dict]) -> tuple[list[dict], float | None]:
        cumulative = 0.0
        recorded = 0
        points: list[dict] = []
        for row in source_rows:
            value = row.get("realized_pnl")
            if value is None:
                continue
            pnl = float(value)
            cumulative += pnl
            recorded += 1
            if cutoff is not None and str(row.get("closed_at") or "") < cutoff:
                continue
            points.append({
                "time": row["closed_at"],
                "pnl": round(pnl, 4),
                "cumulative_pnl": round(cumulative, 4),
            })
        return points, round(cumulative, 4) if recorded else None

    raw_points, raw_total = _points(rows)
    genuine_points, genuine_total = _points(genuine_rows)
    cohort = build_outcome_cohort_summary(
        execution_mode=execution_mode,
        closed_after=cutoff,
    )

    # Unrealized PnL for open positions (same mode filter if specified)
    upnl_conditions = ["status='open'"]
    upnl_params: list = []
    if execution_mode:
        upnl_conditions.append("execution_mode = ?")
        upnl_params.append(execution_mode)
    upnl_where = " AND ".join(upnl_conditions)
    open_row = db.execute(
        f"SELECT SUM(unrealized_pnl) as total_upnl, COUNT(*) AS open_count FROM positions WHERE {upnl_where}",
        upnl_params,
    ).fetchone()
    total_upnl = round((open_row["total_upnl"] or 0.0), 4)
    open_count = int(open_row["open_count"] or 0)

    # Prefer authoritative cross-broker exchange uPnL (real mark-price + funding)
    # when not filtering by a single mode — keeps the chart's end point consistent
    # with the Overview UNREALIZED card, which also reads UTA uPnL.
    if not execution_mode and uses_external_exchange:
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

    # The live point is a mark-to-market read, not a close event.  Keeping it
    # out of both curves prevents open PnL from looking like either a recorded
    # realization or a verified strategy outcome, while still making Overview
    # visibly move between closes.
    current_mark_point = None
    if raw_total is not None or open_count:
        from datetime import datetime, timezone
        closed_raw_pnl = raw_total or 0.0
        current_mark_point = {
            "time": datetime.now(timezone.utc).isoformat(),
            "closed_raw_pnl": round(closed_raw_pnl, 4),
            "current_unrealized": total_upnl,
            "cumulative_pnl": round(closed_raw_pnl + total_upnl, 4),
        }

    return {
        **cohort,
        "default_curve": "genuine_verified",
        "raw_curve_label": "raw_recorded_account_events",
        "genuine_curve_label": "verified_strategy_outcomes",
        "raw_recorded_points": raw_points,
        "genuine_points": genuine_points,
        # Compatibility aliases now explicitly default to the genuine cohort.
        "points": genuine_points,
        "current_unrealized": total_upnl,
        "current_mark_point": current_mark_point,
        "total_realized": genuine_total,
        "raw_total_recorded_realized": raw_total,
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
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    from aletheia.analytics.trade_stats import build_strategy_kpis
    return {
        "strategies": build_strategy_kpis(),
        "latest_backtest": {},
        **build_outcome_cohort_summary(),
    }


@router.get("/go-live-scorecard")
def get_go_live_scorecard():
    """Quantitative go-live readiness scorecard."""
    from aletheia.analytics.go_live_scorecard import compute_scorecard
    return compute_scorecard()


@router.get("/cost-summary")
def get_cost_summary():
    """Verified outcome costs plus separately labelled raw recorded costs."""
    from aletheia.analytics.outcome_integrity import (
        build_outcome_cohort_summary,
        eligible_strategy_outcomes,
        raw_account_events,
    )

    def _costs(rows: list[dict]) -> dict[str, float | None]:
        if not rows or not any(
            row.get(field) is not None
            for row in rows
            for field in ("entry_fee", "exit_fee", "slippage_cost")
        ):
            return {
                "total_entry_fees": None,
                "total_exit_fees": None,
                "total_fees": None,
                "total_slippage": None,
                "total_costs": None,
            }
        entry = sum(float(row.get("entry_fee") or 0) for row in rows)
        exit_ = sum(float(row.get("exit_fee") or 0) for row in rows)
        slippage = sum(float(row.get("slippage_cost") or 0) for row in rows)
        return {
            "total_entry_fees": round(entry, 8),
            "total_exit_fees": round(exit_, 8),
            "total_fees": round(entry + exit_, 8),
            "total_slippage": round(slippage, 8),
            "total_costs": round(entry + exit_ + slippage, 8),
        }

    verified = _costs(eligible_strategy_outcomes())
    raw = _costs(raw_account_events())
    return {
        **verified,
        "raw_recorded_costs": raw,
        **build_outcome_cohort_summary(),
    }


@router.get("/confidence-calibration")
def get_confidence_calibration():
    """Conviction calibration report — actual vs expected win rate per conviction bucket."""
    from aletheia.analytics.confidence_calibration import get_latest_calibration_report, build_calibration_report
    report = get_latest_calibration_report()
    if not report:
        report = build_calibration_report()
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    result = report or {"status": "no_data", "buckets": [], "ece": None, "ece_grade": "insufficient_data"}
    return {**result, **build_outcome_cohort_summary()}


@router.get("/reasoning-feedback")
def get_reasoning_feedback():
    """Reasoning quality feedback — proposal rejection patterns and approval win rates."""
    from aletheia.analytics.reasoning_feedback import get_latest_reasoning_feedback
    return get_latest_reasoning_feedback() or {"status": "no_data"}


@router.get("/alpha-attribution")
def get_alpha_attribution(days: int = 30):
    """Read-only five-layer alpha attribution audit."""
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit
    return build_alpha_attribution_audit(days=days)


@router.get("/filter-controller")
def get_filter_controller():
    """Read-only filter feedback controller report (Phase 1 shadow).

    Exact-version pass/reject net-outcome findings with sample/coverage gates.
    Observational only; never mutates config."""
    from aletheia.analytics.filter_controller import build_filter_controller_report
    return build_filter_controller_report()


@router.get("/thesis-lineage")
def get_thesis_lineage(
    days: int = 30,
    position_id: str | None = None,
    order_id: str | None = None,
    proposal_id: str | None = None,
    opportunity_id: str | None = None,
    limit: int = 50,
):
    """Read-only thesis lineage records layered on top of the causal trade chain."""
    from aletheia.analytics.thesis_lineage import filter_thesis_lineage_records

    return {
        "records": filter_thesis_lineage_records(
            days=days,
            position_id=position_id,
            order_id=order_id,
            proposal_id=proposal_id,
            opportunity_id=opportunity_id,
            limit=limit,
        )
    }


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


@router.get("/opportunity-funnel")
def get_opportunity_funnel(hours: int = 24):
    """Market Observer → Operator funnel: exposes WHY opportunities don't reach the operator.

    Cumulative counts (window):
      - emitted_count: opportunities.created_at in window
      - reviewed_count: emitted AND status != 'open' (settled by ranker)
      - still_open_count: emitted AND status = 'open' (waiting for ranker)
      - proposed_count: trade_proposals.created_at in window

    Point-in-time snapshot (latest candidate_supply_snapshot event, written by
    trading_context every operator eval):
      - executable_count: candidates that survived the ranker gates AND have a
        matching pending signal (= "accepted" in the working set)
      - excluded_by_reason: bucketed skip reasons (confidence_below, guard_blocked,
        ttl_expired, ...) with a few example candidates. Each example carries
        opportunity_id + valid_until so the UI can render "expired X min ago".
      - main_reason / below_floor: what the operator loop reads to decide whether
        confidence recovery should fire.

    Auxiliary state:
      - family_ttl_sec: per-scanner-family TTL from allocator._FAMILY_TTL — the UI
        needs this to render "ttl was N min" without a second round trip.
      - universe_snapshot_age_sec: how long since the active universe snapshot was
        written. Combined with valid_until, this exposes the change-suppression
        interaction with TTL that drives the "everything ttl_expired" pattern.

    Read-only. Consumes existing telemetry — never invokes the ranker/operator.
    """
    from datetime import datetime, timezone
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    from aletheia.universe.allocator import _FAMILY_TTL

    db = get_db()
    hours_i = max(1, int(hours or 24))
    window = f"-{hours_i} hours"

    def _count(sql: str) -> int:
        try:
            return int(db.execute(sql, (window,)).fetchone()[0])
        except Exception:
            return 0

    emitted = _count(
        "SELECT COUNT(*) FROM opportunities WHERE created_at >= datetime('now', ?)"
    )
    still_open = _count(
        "SELECT COUNT(*) FROM opportunities "
        "WHERE created_at >= datetime('now', ?) AND status = 'open'"
    )
    reviewed = max(0, emitted - still_open)
    proposed = _count(
        "SELECT COUNT(*) FROM trade_proposals WHERE created_at >= datetime('now', ?)"
    )

    # Latest ranker-view snapshot (what the operator saw on its last eval)
    snapshot: dict = {}
    try:
        row = db.execute(
            "SELECT payload, created_at FROM agent_shell_events "
            "WHERE event_type = 'candidate_supply_snapshot' "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            import json as _json
            try:
                snapshot = _json.loads(row["payload"]) or {}
            except (TypeError, ValueError):
                snapshot = {}
            snapshot["snapshot_at"] = row["created_at"]
    except Exception:
        snapshot = {}

    executable_count = int(snapshot.get("executable_count") or 0)
    excluded_by_reason = list(snapshot.get("excluded_candidates_summary") or [])

    # Enrich TTL-expired examples with expired_seconds so the UI doesn't have to
    # re-parse timestamps in JS. Values are Best-effort: older snapshots may not
    # carry valid_until (pre-enrichment) — we simply leave the field null.
    now = datetime.now(timezone.utc)
    for bucket in excluded_by_reason:
        for ex in bucket.get("examples") or []:
            valid_until = ex.get("valid_until")
            if valid_until:
                try:
                    parsed = datetime.fromisoformat(str(valid_until).replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    ex["expired_seconds"] = int((now - parsed).total_seconds())
                except ValueError:
                    ex["expired_seconds"] = None

    # Universe snapshot age — critical to interpret the ttl_expired pattern,
    # because the reuse_previous_snapshot short-circuit skips valid_until refresh.
    snapshot_age_sec = None
    try:
        srow = db.execute(
            "SELECT created_at FROM universe_snapshots "
            "WHERE market_type = 'crypto' AND (expires_at IS NULL OR expires_at > datetime('now')) "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if srow and srow["created_at"]:
            parsed = datetime.fromisoformat(str(srow["created_at"]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            snapshot_age_sec = int((now - parsed).total_seconds())
    except Exception:
        snapshot_age_sec = None

    cfg = load_config()
    min_confidence = getattr(cfg.trading, "min_confidence_to_trade", "medium")

    return {
        "window_hours": hours_i,
        "emitted_count": emitted,
        "reviewed_count": reviewed,
        "still_open_count": still_open,
        "proposed_count": proposed,
        "snapshot": {
            "executable_count": executable_count,
            "excluded_by_reason": excluded_by_reason,
            "main_reason": snapshot.get("main_reason"),
            "below_floor": snapshot.get("below_floor"),
            "floor": snapshot.get("floor"),
            "snapshot_at": snapshot.get("snapshot_at"),
            "checked_at": snapshot.get("checked_at"),
        },
        "config": {
            "min_confidence_to_trade": min_confidence,
        },
        "family_ttl_sec": dict(_FAMILY_TTL),
        "universe_snapshot_age_sec": snapshot_age_sec,
    }


@router.get("/operator-decisions")
def get_operator_decisions(hours: int = 24, limit: int = 50):
    """Recent operator decisions — the "final-stage" companion to the Funnel card.

    Merges three sources into one time-ordered list so the Approval Desk can
    surface not only pending / accepted proposals but also every kind of
    rejection that happened after a candidate reached the operator:

    - `operator_skip`: pi_decisions.action = 'skip' — the LLM operator saw an
      executable candidate and decided not to trade (reason in `reasoning`).
    - `trade`: pi_decisions.action IN ('trade','flip','scale_in') — operator
      chose to trade; a proposal typically follows.
    - `user_reject`: trade_proposals.status='rejected' with matching
      audit_events row actor='human', source='dashboard' — a human pressed
      Reject in the dashboard before execution began.
    - `auto_reject`: trade_proposals.status='rejected' with matching
      audit_events row actor='system' — gateway pre-flight or risk-engine
      auto-rejected before execution.

    All decisions are keyed by `opportunity_id` where available so the UI can
    request the full lifecycle trace via /analytics/opportunity-trace/{id}.

    Read-only. Purely reconstructive over already-persisted evidence.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.data.symbols import to_canonical

    db = get_db()
    hours_i = max(1, int(hours or 24))
    limit_i = max(1, min(int(limit or 50), 200))
    window = f"-{hours_i} hours"

    items: list[dict] = []

    # pi_decisions — operator trade / skip
    try:
        rows = list(db.execute(
            "SELECT id, created_at, symbol, direction, market_type, action, "
            "conviction, reasoning, opportunity_id, proposal_id "
            "FROM pi_decisions "
            "WHERE action IN ('trade','skip','flip','scale_in','watch') "
            "AND created_at >= datetime('now', ?) "
            "AND (symbol IS NOT NULL AND symbol != '') "
            "ORDER BY created_at DESC LIMIT ?",
            (window, limit_i * 2),
        ))
        for r in rows:
            act = str(r["action"] or "")
            if act == "skip":
                outcome = "operator_skip"
            elif act == "watch":
                outcome = "operator_watch"
            else:
                outcome = "trade"  # trade / flip / scale_in — operator chose to act
            items.append({
                "opportunity_id": r["opportunity_id"],
                "proposal_id": r["proposal_id"],
                "decision_id": r["id"],
                "symbol": r["symbol"],
                "direction": r["direction"],
                "market_type": r["market_type"],
                "decided_at": r["created_at"],
                "outcome": outcome,
                "action": act,
                "conviction": r["conviction"],
                "one_line_reason": _first_line(r["reasoning"], 140),
            })
    except Exception:
        pass

    # trade_proposals rejected — user vs auto reject determined by paired audit event
    try:
        rows = list(db.execute(
            "SELECT tp.id AS proposal_id, tp.opportunity_id, tp.created_at, "
            "tp.symbol, tp.direction, tp.market_type, "
            "ae.actor, ae.source, ae.message, ae.timestamp AS rejected_at "
            "FROM trade_proposals tp "
            "LEFT JOIN audit_events ae ON ae.entity_id = tp.id "
            "  AND ae.event_type = 'proposal_rejected' "
            "WHERE tp.status = 'rejected' "
            "AND tp.created_at >= datetime('now', ?) "
            "ORDER BY COALESCE(ae.timestamp, tp.created_at) DESC LIMIT ?",
            (window, limit_i * 2),
        ))
        seen_props: set[str] = set()
        for r in rows:
            pid = r["proposal_id"]
            if pid in seen_props:
                continue  # dedupe if multiple audit rows for the same proposal
            seen_props.add(pid)
            actor = str(r["actor"] or "")
            if actor == "human":
                outcome = "user_reject"
            else:
                outcome = "auto_reject"
            items.append({
                "opportunity_id": r["opportunity_id"],
                "proposal_id": pid,
                "decision_id": None,
                "symbol": r["symbol"],
                "direction": r["direction"],
                "market_type": r["market_type"],
                "decided_at": r["rejected_at"] or r["created_at"],
                "outcome": outcome,
                "action": "reject",
                "conviction": None,
                "one_line_reason": _first_line(r["message"], 140),
                "rejected_actor": actor or None,
                "rejected_source": r["source"],
            })
    except Exception:
        pass

    items.sort(key=lambda x: str(x.get("decided_at") or ""), reverse=True)
    items = items[:limit_i]

    counts = {"trade": 0, "operator_skip": 0, "operator_watch": 0,
              "user_reject": 0, "auto_reject": 0}
    for it in items:
        oc = it.get("outcome")
        if oc in counts:
            counts[oc] += 1

    # Canonicalize symbols so the UI's fmtSymbol path matches the pending queue
    for it in items:
        sym = it.get("symbol")
        if sym:
            try:
                it["symbol"] = to_canonical(sym)
            except Exception:
                pass

    return {
        "window_hours": hours_i,
        "limit": limit_i,
        "counts": counts,
        "items": items,
    }


def _first_line(text: str | None, max_chars: int) -> str | None:
    """Return the first non-empty line trimmed to max_chars, or None."""
    if not text:
        return None
    for line in str(text).splitlines():
        s = line.strip()
        if s:
            return s if len(s) <= max_chars else s[: max_chars - 1] + "…"
    return None


@router.get("/opportunity-trace/{opportunity_id}")
def get_opportunity_trace(opportunity_id: str):
    """Read-only lifecycle trace for one opportunity.

    Thin route over analytics.opportunity_flow.trace_opportunity_flow — returns
    the full timeline (emit → filter_trace → pi_decision → risk_decision →
    proposal → order → fill → position → close), reasons at each stage, and
    the counterfactual shadow/forward slice if present. Powers the Approval
    Desk's per-row drill-down drawer.
    """
    from aletheia.analytics.opportunity_flow import trace_opportunity_flow
    return trace_opportunity_flow(opportunity_id)


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

        # Hot dashboard path: keep this DB-only. Passing file_path would make the
        # detector read hundreds of Markdown files on every Learning refresh.
        contradiction_items = [
            dict(r) for r in db.execute(
                "SELECT id, item_type, title, confidence, status, tags "
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
