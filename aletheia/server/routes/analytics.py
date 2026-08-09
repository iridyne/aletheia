"""Read-only analytics API routes."""
from __future__ import annotations

from copy import deepcopy
import time
from datetime import datetime, timedelta, timezone
import json
from threading import RLock

from fastapi import APIRouter, Response

router = APIRouter(prefix="/api/analytics", tags=["analytics"])

_DECISION_COUNT_KEYS = (
    "trade",
    "operator_skip",
    "operator_watch",
    "portfolio_guard_reject",
    "user_reject",
    "auto_reject",
    "reject_unknown",
)


def _rolling_cutoff(hours: int) -> tuple[int, str]:
    """Return a normalized UTC cutoff for analytics rows with mixed ISO formats."""
    hours_i = max(1, int(hours or 24))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_i)
    return hours_i, cutoff.isoformat()


def _decision_policy_context(db) -> tuple[dict | None, list[dict]]:
    rows = [
        dict(row)
        for row in db.execute(
            "SELECT epoch_id, started_at, ended_at FROM learning_policy_epochs "
            "WHERE epoch_kind='system_operator_skip_remediation' "
            "ORDER BY julianday(started_at) DESC, rowid DESC"
        )
    ]
    current = next((row for row in rows if not row.get("ended_at")), None)
    return current or (rows[0] if rows else None), rows


def _analytics_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _policy_for_instant(epochs: list[dict], instant: str | None) -> str | None:
    decision_instant = _analytics_instant(instant)
    if decision_instant is None:
        return None
    for epoch in epochs:
        started_at = _analytics_instant(epoch.get("started_at"))
        ended_at = _analytics_instant(epoch.get("ended_at"))
        if started_at and decision_instant >= started_at and (
            ended_at is None or decision_instant <= ended_at
        ):
            return str(epoch["epoch_id"])
    return None


def _decision_attribution(
    *,
    decided_at: str | None,
    evidence_manifest_json: str | None,
    runtime_session_id: str | None,
    runtime_id: str | None,
    producer_commit: str | None,
    current_policy_id: str | None,
    epochs: list[dict],
    current_runtime_session_id: str | None,
) -> dict:
    manifest: dict = {}
    if evidence_manifest_json:
        try:
            parsed = json.loads(evidence_manifest_json)
            if isinstance(parsed, dict):
                manifest = parsed
        except (TypeError, ValueError):
            pass
    policy_epoch_id = manifest.get("skip_policy_epoch_id")
    if policy_epoch_id:
        policy_epoch_id = str(policy_epoch_id)
    policy_basis = "decision_manifest" if policy_epoch_id else "epoch_time_window"
    if not policy_epoch_id:
        policy_epoch_id = _policy_for_instant(epochs, decided_at)
    if not policy_epoch_id:
        policy_basis = "unavailable"
    policy_relation = (
        "current"
        if policy_epoch_id and policy_epoch_id == current_policy_id
        else "historical"
        if policy_epoch_id
        else "unavailable"
    )
    runtime_relation = (
        "current_session"
        if runtime_session_id and runtime_session_id == current_runtime_session_id
        else "historical_runtime"
        if runtime_session_id
        else "unavailable"
    )
    return {
        "policy_epoch_id": policy_epoch_id,
        "policy_basis": policy_basis,
        "policy_relation": policy_relation,
        "runtime_session_id": runtime_session_id,
        "runtime_id": runtime_id,
        "producer_commit": producer_commit,
        "runtime_relation": runtime_relation,
    }


def _decision_counts(items: list[dict], policy_relation: str) -> dict[str, int]:
    counts = {key: 0 for key in _DECISION_COUNT_KEYS}
    for item in items:
        if item.get("attribution", {}).get("policy_relation") != policy_relation:
            continue
        outcome = item.get("outcome")
        if outcome in counts:
            counts[outcome] += 1
    return counts


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


# Perf (Phase 2): short-TTL cache for /equity-curve. The endpoint accumulates
# raw_account_events over full history — cost grows linearly with trade count.
# 5s TTL absorbs back-to-back polling without locking the response to a stale
# PnL (closes are rare; 5s staleness is invisible on the chart).
_equity_curve_cache: dict[tuple, tuple[float, dict]] = {}
_equity_curve_cache_lock = RLock()
_EQUITY_CURVE_CACHE_TTL_SEC = 5.0


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

    # Perf (Phase 2): cache key includes all params that change the payload.
    cache_key = (execution_mode, days, limit)
    now_mono = time.monotonic()
    with _equity_curve_cache_lock:
        cached = _equity_curve_cache.get(cache_key)
        if cached is not None:
            cached_at, payload = cached
            if now_mono - cached_at < _EQUITY_CURVE_CACHE_TTL_SEC:
                return payload

    mode = "full_cumulative"
    uses_external_exchange = False
    try:
        # deep_copy=False: read-only access to config.
        config = load_config(deep_copy=False)
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

    payload = {
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

    # Perf (Phase 2): write to the short-TTL cache.
    with _equity_curve_cache_lock:
        _equity_curve_cache[cache_key] = (time.monotonic(), payload)

    return payload


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
    """Profit-loop funnel for one linked Opportunity cohort.

    The cohort is anchored by Opportunities created inside the rolling window;
    every downstream stage is counted by distinct linked ``opportunity_id`` as
    observed at response time. Event totals from unrelated generations or old
    Opportunities must never be divided into this cohort, and close fills are
    not entry conversions. None of these read-only facts affect PI decisions.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    hours_i, cutoff = _rolling_cutoff(hours)

    opportunity_rows = list(
        db.execute(
            "SELECT id, status, created_at FROM opportunities "
            "WHERE julianday(created_at) >= julianday(?)",

            (cutoff,),
        )
    )
    opportunity_ids = {str(row["id"]) for row in opportunity_rows}
    opportunity_statuses = {
        str(row["id"]): str(row["status"] or "") for row in opportunity_rows
    }
    opps = len(opportunity_ids)

    # ``signal_pending`` is a recoverable queue carrier and is intentionally
    # pruned after a settled final decision. Historical funnel evidence therefore
    # comes from the append-only enqueue filter fact written atomically with the
    # carrier, not from whichever transient carrier rows still happen to exist.
    signal_rows = list(
        db.execute(
            "SELECT fd.opportunity_id, fd.dedup_key FROM filter_decisions fd "
            "JOIN opportunities o ON o.id=fd.opportunity_id "
            "WHERE julianday(o.created_at) >= julianday(?) "
            "AND fd.filter_name='signal_enqueue' "
            "AND fd.stage='paper_trader_enqueue' "
            "AND fd.decision='passed' "
            "AND fd.reason_code='signal_enqueued'",

            (cutoff,),
        )
    )
    signal_ids = {str(row["opportunity_id"]) for row in signal_rows}
    carrier_generations = len(signal_rows)

    # Parsed time plus rowid keeps final-decision diagnostics deterministic for
    # mixed SQLite/ISO timestamps and same-instant retries.
    decision_rows = list(
        db.execute(
            "SELECT p.rowid AS _rowid, p.* FROM pi_decisions p "
            "JOIN opportunities o ON o.id=p.opportunity_id "
            "WHERE julianday(o.created_at) >= julianday(?) "
            "AND COALESCE(p.signal_source, 'market_observer')='market_observer' "
            "ORDER BY p.opportunity_id, "
            "COALESCE(p.signal_source, 'market_observer'), "
            "julianday(p.created_at), p.rowid",
            (cutoff,),
        )
    )

    all_trade_ids: set[str] = set()
    skip_candidates: set[str] = set()
    retained_pending_rows = 0
    for row in decision_rows:
        opportunity_id = str(row["opportunity_id"] or "")
        action = str(row["action"] or "")
        if action == "signal_pending":
            retained_pending_rows += 1
        elif action in {"trade", "scale_in", "flip"}:
            all_trade_ids.add(opportunity_id)
        elif action in {"skip", "portfolio_guard_skip"}:
            skip_candidates.add(opportunity_id)

    # Only signalled members can advance through this conversion funnel. A
    # direct trade remains an integrity fact instead of inflating the rate.
    trade_ids = all_trade_ids & signal_ids
    trade_without_signal = all_trade_ids - signal_ids
    skip_ids = (skip_candidates & signal_ids) - trade_ids

    proposal_to_opportunity: dict[str, str] = {}
    for row in decision_rows:
        opportunity_id = str(row["opportunity_id"] or "")
        proposal_id = str(row["proposal_id"] or "")
        if (
            opportunity_id in trade_ids
            and str(row["action"] or "") in {"trade", "scale_in", "flip"}
            and proposal_id
        ):
            proposal_to_opportunity[proposal_id] = opportunity_id
    proposal_ids = set(proposal_to_opportunity)

    # Resolve at most one authority decision per proposal. Preflight decisions
    # are advisory; an authoritative row supersedes a legacy compatibility row.
    risk_by_proposal: dict[str, dict] = {}
    if proposal_ids:
        placeholders = ",".join("?" for _ in proposal_ids)
        rows = db.execute(
            "SELECT rowid AS _rowid, * FROM risk_decisions "
            f"WHERE proposal_id IN ({placeholders}) "
            "AND COALESCE(decision_role, 'legacy')!='preflight' "
            "ORDER BY julianday(created_at), rowid",
            tuple(sorted(proposal_ids)),
        )
        for raw in rows:
            row = dict(raw)
            proposal_id = str(row.get("proposal_id") or "")
            existing = risk_by_proposal.get(proposal_id)
            role = str(row.get("decision_role") or "legacy")
            existing_role = str((existing or {}).get("decision_role") or "legacy")
            if (
                existing is None
                or role == "authoritative"
                or existing_role != "authoritative"
            ):
                risk_by_proposal[proposal_id] = row

    approved_opportunities = {
        proposal_to_opportunity[proposal_id]
        for proposal_id, row in risk_by_proposal.items()
        if int(row.get("approved") or 0) == 1
    }
    rejected_candidates = {
        proposal_to_opportunity[proposal_id]
        for proposal_id, row in risk_by_proposal.items()
        if int(row.get("approved") or 0) == 0
    }
    rejected_opportunities = rejected_candidates - approved_opportunities
    risk_approved = len(approved_opportunities)
    risk_rejected = len(rejected_opportunities)

    rejection_messages: dict[str, set[str]] = {}
    for proposal_id, row in risk_by_proposal.items():
        opportunity_id = proposal_to_opportunity[proposal_id]
        if (
            opportunity_id not in rejected_opportunities
            or int(row.get("approved") or 0) == 1
        ):
            continue
        message = str(row.get("message") or "")[:200]
        rejection_messages.setdefault(message, set()).add(opportunity_id)
    rejection_top = [
        {"message": message, "count": len(ids)}
        for message, ids in sorted(
            rejection_messages.items(),
            key=lambda item: (-len(item[1]), item[0]),
        )[:5]
    ]

    entry_fill_opportunities: set[str] = set()
    close_fills_excluded = 0
    if proposal_ids:
        placeholders = ",".join("?" for _ in proposal_ids)
        rows = db.execute(
            "SELECT o.proposal_id, o.close_reason, f.id AS fill_id "
            "FROM orders o JOIN fills f ON f.order_id=o.id "
            f"WHERE o.proposal_id IN ({placeholders})",
            tuple(sorted(proposal_ids)),
        )
        for row in rows:
            opportunity_id = proposal_to_opportunity[str(row["proposal_id"])]
            if row["close_reason"] is not None:
                close_fills_excluded += 1
            else:
                entry_fill_opportunities.add(opportunity_id)

    approved_entry_fills = entry_fill_opportunities & approved_opportunities
    entry_fill_without_approved_risk = (
        entry_fill_opportunities - approved_opportunities
    )
    fills = len(approved_entry_fills)


    risk_total = risk_approved + risk_rejected
    risk_approve_rate = round(risk_approved / risk_total * 100.0, 2) if risk_total else None

    signal_count = len(signal_ids)
    trade_count = len(trade_ids)
    source_terminal_without_pi_terminal = {
        opportunity_id
        for opportunity_id in signal_ids - trade_ids - skip_ids
        if opportunity_statuses.get(opportunity_id) in {"reviewed", "expired"}
    }
    source_terminal_breakdown = {
        status: sum(
            opportunity_statuses.get(opportunity_id) == status
            for opportunity_id in source_terminal_without_pi_terminal
        )
        for status in ("expired", "reviewed")
    }
    signal_without_terminal = (
        signal_ids
        - trade_ids
        - skip_ids
        - source_terminal_without_pi_terminal
    )

    def _rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator * 100.0, 2) if denominator else None

    return {
        "window_hours": hours_i,
        "cohort": {
            "unit": "opportunity_id_created_in_window",
            "signal_stage_evidence": (
                "filter_decisions:signal_enqueue/passed/signal_enqueued"
            ),
            "window_start": cutoff,
            "downstream_observed_through": datetime.now(timezone.utc).isoformat(),
            "right_censored": True,
        },
        "opportunities": opps,
        "signal_pending": signal_count,
        "signal_carrier_generations": carrier_generations,
        "pi_decisions": {
            "trade": trade_count,
            "skip": len(skip_ids),
        },
        "risk_decisions": {
            "approved": risk_approved,
            "rejected": risk_rejected,
            "approve_rate_pct": risk_approve_rate,
            "top_rejection_reasons": rejection_top,
        },
        "fills": fills,
        "conversion": {
            "opp_to_signal_pct": _rate(signal_count, opps),
            "signal_to_pi_trade_pct": _rate(trade_count, signal_count),
            "pi_trade_to_risk_pct": _rate(risk_total, trade_count),
            "risk_approved_to_fill_pct": _rate(fills, risk_approved),
            "pi_trade_to_fill_pct": _rate(fills, trade_count),
        },
        "integrity": {
            "trade_without_signal_evidence": len(trade_without_signal),
            "trade_without_proposal": len(
                trade_ids - set(proposal_to_opportunity.values())
            ),
            "entry_fill_without_approved_risk": len(
                entry_fill_without_approved_risk
            ),
            "signal_without_terminal": len(signal_without_terminal),
            "source_terminal_without_pi_terminal": len(
                source_terminal_without_pi_terminal
            ),
            "source_terminal_breakdown": source_terminal_breakdown,
            "close_fills_excluded": close_fills_excluded,
            "retained_signal_pending_rows": retained_pending_rows,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Funnel endpoint helpers (extracted so /opportunity-funnel and the faster
# /opportunity-funnel/current-snapshot child route reuse the same base query
# set — see docs/design/frontend/blotter_design.md#performance-backlog).
# ─────────────────────────────────────────────────────────────────────────────

# Perf item 2: process-local TTL cache for the current-snapshot payload.
# Serves back-to-back mounts + the frontend's 30s polling from a single
# compute. Deliberately short (5s) so operator state visible in the UI is
# never more than that many seconds stale — the frontend already applies its
# own 20s staleTime on top, so tightening the server side buys nothing.
#
# Keyed on window_hours alone: the underlying inputs (opportunities table,
# positions table, ranker runs, candidate-supply snapshot) can only shift
# ~1-2s apart from each other in normal operation, and a 5s ceiling means
# the operator sees at most one ranker cycle's lag.

_current_snapshot_cache: dict[int, tuple[float, dict]] = {}
_current_snapshot_cache_lock = RLock()
_CURRENT_SNAPSHOT_CACHE_TTL_SEC = 5.0
_CURRENT_SNAPSHOT_CACHE_MAX_SIZE = 64  # Bound cache so arbitrary query params can't bloat memory.


# Perf item 5: Server-Timing helpers. Emit a machine-parseable phase-timing
# header on the slow analytics endpoints so future regressions can be
# attributed to a specific stage (SQL cohort vs. build_current_entry_status
# vs. snapshot payload parse) instead of guessed.

def _now_ms() -> float:
    return time.perf_counter() * 1000.0


class _PhaseTimer:
    """Context manager that measures wall-clock ms into a shared dict."""
    def __init__(self, phases: dict[str, float], name: str) -> None:
        self.phases = phases
        self.name = name
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = _now_ms()
        return self

    def __exit__(self, *_exc):
        self.phases[self.name] = self.phases.get(self.name, 0.0) + (_now_ms() - self._t0)
        return False


def _format_server_timing(phases: dict[str, float]) -> str:
    """RFC-8673 Server-Timing header value: `phase;dur=NNN.N, phase2;dur=...`."""
    return ", ".join(f"{k};dur={v:.1f}" for k, v in phases.items())


def _compute_current_snapshot_payload(
    db, hours: int, phases: dict[str, float] | None = None,
) -> dict:
    """Non-cached version of the /current-snapshot response body.

    Isolated as a helper so both the cached endpoint route and the full
    /opportunity-funnel endpoint can call the underlying compute without
    also going through the cache lookup — the full endpoint always needs
    the freshly computed inputs alongside its own extra queries.

    Optional `phases` dict receives `cohort` and `build_current` timings
    for Server-Timing emission.
    """
    from aletheia.config.loader import load_config
    from aletheia.universe.allocator import _FAMILY_TTL
    from aletheia.analytics.opportunity_progress import build_current_entry_status

    if phases is not None:
        with _PhaseTimer(phases, "cohort"):
            inp = _funnel_cohort_inputs(db, hours)
        with _PhaseTimer(phases, "build_current"):
            current = build_current_entry_status(
                db,
                now=inp["now"],
                window_hours=inp["hours_i"],
                emitted_count=inp["emitted_count"],
                open_opportunities=inp["still_open"],
                proposed_count=inp["proposed_count"],
                setup_count=inp["setup_count"],
                active_setup_count=inp["active_setup_count"],
                terminal_breakdown=inp["terminal_breakdown"],
            )
    else:
        inp = _funnel_cohort_inputs(db, hours)
        current = build_current_entry_status(
            db,
            now=inp["now"],
            window_hours=inp["hours_i"],
            emitted_count=inp["emitted_count"],
            open_opportunities=inp["still_open"],
            proposed_count=inp["proposed_count"],
            setup_count=inp["setup_count"],
            active_setup_count=inp["active_setup_count"],
            terminal_breakdown=inp["terminal_breakdown"],
        )
    # deep_copy=False: read-only access (only reads min_confidence_to_trade).
    cfg = load_config(deep_copy=False)
    return {
        "window_hours": inp["hours_i"],
        "current": current,
        "config": {
            "min_confidence_to_trade": getattr(cfg.trading, "min_confidence_to_trade", "medium"),
        },
        "family_ttl_sec": dict(_FAMILY_TTL),
    }


def _cached_current_snapshot(
    db, hours: int, phases: dict[str, float] | None = None,
) -> dict:
    """TTL-cached wrapper around _compute_current_snapshot_payload.

    Cache lookup + write are guarded by a re-entrant lock; the compute itself
    runs OUTSIDE the lock so concurrent misses don't block on each other's
    SQL. Two racing misses will each recompute (mild waste), but subsequent
    reads inside the same 5s window all hit cache.

    Optional `phases` dict receives a `cache_hit` or `compute` timing entry
    for Server-Timing emission.
    """
    now_mono = time.monotonic()
    with _current_snapshot_cache_lock:
        cached = _current_snapshot_cache.get(hours)
        if cached is not None:
            cached_at, payload = cached
            if now_mono - cached_at < _CURRENT_SNAPSHOT_CACHE_TTL_SEC:
                if phases is not None:
                    phases["cache_hit"] = 0.0
                return payload

    # Cache miss: propagate the phases dict so the compute internals surface
    # in Server-Timing as `cohort;dur=... build_current;dur=...`.
    payload = _compute_current_snapshot_payload(db, hours, phases=phases)
    with _current_snapshot_cache_lock:
        _current_snapshot_cache[hours] = (time.monotonic(), payload)
        # Bound the cache: evict oldest entries beyond the max size.
        if len(_current_snapshot_cache) > _CURRENT_SNAPSHOT_CACHE_MAX_SIZE:
            oldest = sorted(
                _current_snapshot_cache.items(), key=lambda kv: kv[1][0],
            )
            for k, _ in oldest[:len(_current_snapshot_cache) - _CURRENT_SNAPSHOT_CACHE_MAX_SIZE]:
                _current_snapshot_cache.pop(k, None)
    return payload


def _funnel_cohort_inputs(db, hours: int) -> dict:
    """Compute the inputs `build_current_entry_status` requires, plus the
    window bounds so callers can extend with additional stats.

    Returns a dict with:
      - hours_i, window_start, window_end, now
      - emitted_count, still_open, queued, reviewed, expired, proposed_count
      - setup_count, active_setup_count
      - terminal_breakdown

    Perf item 3: the six status-count SELECTs (emitted, still_open, queued,
    reviewed, expired) previously ran as separate `SELECT COUNT(*)` queries
    over the same cohort; they now share one `SUM(CASE WHEN ...)` scan.
    Same trick collapses setup_count + active_setup_count into one grouped
    aggregate. Net: 7 SQL round-trips → 2 for the count group. The full
    endpoint still uses queued/reviewed/expired; the fast /current-snapshot
    endpoint keeps them in the payload but doesn't render them (payload
    remains small).
    """
    hours_i, window_start = _rolling_cutoff(hours)
    now = datetime.now(timezone.utc)
    window_end = now.isoformat()

    # Perf (Phase 1): ISO-8601 UTC string comparison — see pipeline-funnel note
    # above. window_start/window_end are UTC ISO from _rolling_cutoff + now().
    cohort_where = (
        "created_at>=? "
        "AND created_at<=?"
    )
    cohort_params = (window_start, window_end)

    # Query 1 of 2 for the cohort — one scan, five aggregates.
    status_row = db.execute(
        f"""
        SELECT
          COUNT(*)                                             AS emitted,
          SUM(CASE WHEN status='open'     THEN 1 ELSE 0 END)   AS still_open,
          SUM(CASE WHEN status='queued'   THEN 1 ELSE 0 END)   AS queued,
          SUM(CASE WHEN status='reviewed' THEN 1 ELSE 0 END)   AS reviewed,
          SUM(CASE WHEN status='expired'  THEN 1 ELSE 0 END)   AS expired
        FROM opportunities
        WHERE {cohort_where}
        """,
        cohort_params,
    ).fetchone()
    emitted     = int(status_row["emitted"]    or 0)
    still_open  = int(status_row["still_open"] or 0)
    queued      = int(status_row["queued"]     or 0)
    reviewed    = int(status_row["reviewed"]   or 0)
    expired     = int(status_row["expired"]    or 0)

    # Query 2 of 2 for the cohort — one scan, two setup aggregates.
    # The setup identity string uses SQLite's char(1) as a separator so no
    # legitimate symbol/family value can collide across boundaries.
    setup_row = db.execute(
        f"""
        SELECT
          COUNT(DISTINCT
            symbol || char(1) || market || char(1)
            || COALESCE(direction_bias,'') || char(1)
            || COALESCE(scanner_family,'')
          ) AS setup_count,
          COUNT(DISTINCT CASE WHEN status IN ('open','queued') THEN
            symbol || char(1) || market || char(1)
            || COALESCE(direction_bias,'') || char(1)
            || COALESCE(scanner_family,'')
          END) AS active_setup_count
        FROM opportunities
        WHERE {cohort_where}
        """,
        cohort_params,
    ).fetchone()
    setup_count        = int(setup_row["setup_count"]        or 0)
    active_setup_count = int(setup_row["active_setup_count"] or 0)

    proposed = int(db.execute(
        "SELECT COUNT(DISTINCT tp.opportunity_id) FROM trade_proposals tp "
        "JOIN opportunities o ON o.id=tp.opportunity_id "
        "WHERE o.created_at>=? "
        "AND o.created_at<=? "
        "AND tp.created_at<=?",
        (window_start, window_end, window_end),
    ).fetchone()[0] or 0)

    terminal_breakdown = {
        "snapshot_superseded": 0,
        "ttl_expired": 0,
        "other_expired": 0,
        "operator_trade": 0,
        "operator_skip": 0,
        "operator_watch": 0,
        "deterministic_review": 0,
        "other_reviewed": 0,
    }
    expired_rows = db.execute(
        "SELECT CASE "
        " WHEN EXISTS (SELECT 1 FROM filter_decisions fd "
        "              WHERE fd.candidate_key='opportunity:' || o.id "
        "              AND fd.reason_code='universe_snapshot_superseded') "
        " THEN 'snapshot_superseded' "
        " WHEN EXISTS (SELECT 1 FROM filter_decisions fd "
        "              WHERE fd.candidate_key='opportunity:' || o.id "
        "              AND fd.reason_code='ttl_expired') "
        " THEN 'ttl_expired' ELSE 'other_expired' END AS terminal_reason, "
        "COUNT(*) AS n FROM opportunities o WHERE " + cohort_where + " "
        "AND o.status='expired' GROUP BY terminal_reason",
        cohort_params,
    ).fetchall()
    for row in expired_rows:
        terminal_breakdown[str(row["terminal_reason"])] = int(row["n"] or 0)

    reviewed_rows = db.execute(
        "SELECT CASE "
        " WHEN EXISTS (SELECT 1 FROM pi_decisions p WHERE p.opportunity_id=o.id "
        "              AND p.action IN ('trade','scale_in','flip')) "
        " THEN 'operator_trade' "
        " WHEN EXISTS (SELECT 1 FROM pi_decisions p WHERE p.opportunity_id=o.id "
        "              AND p.action='portfolio_guard_skip') THEN 'portfolio_guard_reject' "
        " WHEN EXISTS (SELECT 1 FROM pi_decisions p WHERE p.opportunity_id=o.id "
        "              AND p.action='skip') THEN 'operator_skip' "
        " WHEN EXISTS (SELECT 1 FROM pi_decisions p WHERE p.opportunity_id=o.id "
        "              AND p.action='watch') THEN 'operator_watch' "
        " WHEN EXISTS (SELECT 1 FROM filter_decisions fd "
        "              WHERE fd.candidate_key='opportunity:' || o.id "
        "              AND fd.decision IN ('rejected','error')) "
        " THEN 'deterministic_review' ELSE 'other_reviewed' END AS terminal_reason, "
        "COUNT(*) AS n FROM opportunities o WHERE " + cohort_where + " "
        "AND o.status='reviewed' GROUP BY terminal_reason",
        cohort_params,
    ).fetchall()
    for row in reviewed_rows:
        terminal_breakdown[str(row["terminal_reason"])] = int(row["n"] or 0)

    return {
        "hours_i": hours_i,
        "window_start": window_start,
        "window_end": window_end,
        "now": now,
        "emitted_count": emitted,
        "still_open": still_open,
        "queued": queued,
        "reviewed": reviewed,
        "expired": expired,
        "proposed_count": proposed,
        "setup_count": setup_count,
        "active_setup_count": active_setup_count,
        "terminal_breakdown": terminal_breakdown,
    }


@router.get("/opportunity-funnel/current-snapshot")
def get_opportunity_funnel_current_snapshot(hours: int = 24, response: Response = None):
    """Fast subset of /opportunity-funnel — just enough for the Blotter's
    Candidates panel and status-token strip to render.

    Skips the queued/reviewed/expired cohort counts, the candidate-supply
    snapshot payload parsing, and the universe-snapshot lookup that the
    full endpoint carries. Roughly 6 SQL + `build_current_entry_status`
    (which does another handful of small SQLs internally) versus ~15+ SQL
    for the full endpoint.

    Backed by a 5s process-local TTL cache (`_cached_current_snapshot`), so
    back-to-back mounts and the frontend's 30s poll share one compute.

    Emits `Server-Timing` with either `cache_hit;dur=0.0` or
    `cohort;dur=... build_current;dur=...` — see perf item 5.

    Response shape:
      - window_hours: echoed
      - current: build_current_entry_status(...) — the important payload
      - config: { min_confidence_to_trade }
      - family_ttl_sec: dict from allocator._FAMILY_TTL
    """
    from aletheia.storage.sqlite import get_db
    phases: dict[str, float] = {}
    with _PhaseTimer(phases, "total"):
        payload = _cached_current_snapshot(get_db(), hours, phases=phases)
    if response is not None:
        response.headers["Server-Timing"] = _format_server_timing(phases)
    return payload


# Perf (Phase 2): process-local TTL cache for the full /opportunity-funnel
# payload. The endpoint runs ~15 SQL + build_current_entry_status; the 12s
# TTL serves back-to-back mounts + dashboard polling from one compute. Keyed
# on window_hours (the only request param that changes the payload).
_funnel_cache: dict[int, tuple[float, dict]] = {}
_funnel_cache_lock = RLock()
_FUNNEL_CACHE_TTL_SEC = 12.0
_FUNNEL_CACHE_MAX_SIZE = 64


@router.get("/opportunity-funnel")
def get_opportunity_funnel(hours: int = 24, response: Response = None):
    """Market Observer → Operator funnel: exposes WHY opportunities don't reach the operator.

    Causal cohort counts (window):
      - emitted_count: per-snapshot Opportunity generations created in window
      - setup_count: unique symbol/market/direction/scanner-family setups
      - open/queued/reviewed/expired: exact stored status within that cohort
      - terminal_breakdown: evidence-backed supersede/TTL/Operator/filter exits
      - proposed_count: distinct cohort Opportunities linked to any Proposal by now

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
        written; generation churn is not presented as market TTL failure.

    Read-only. Consumes existing telemetry — never invokes the ranker/operator.

    For the compact "just enough to render current pipeline state" version see
    /opportunity-funnel/current-snapshot — it skips the extras below.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    phases: dict[str, float] = {}

    # Perf (Phase 2): TTL cache lookup. The Server-Timing header reports a
    # `cache_hit;dur=0.0` entry when the payload is served from cache.
    now_mono = time.monotonic()
    with _funnel_cache_lock:
        cached = _funnel_cache.get(hours)
        if cached is not None:
            cached_at, payload = cached
            if now_mono - cached_at < _FUNNEL_CACHE_TTL_SEC:
                phases["cache_hit"] = 0.0
                if response is not None:
                    response.headers["Server-Timing"] = _format_server_timing(phases)
                return payload

    total_timer = _PhaseTimer(phases, "total")
    total_timer.__enter__()

    # Shared inputs: base counts + terminal_breakdown + window bounds.
    with _PhaseTimer(phases, "cohort"):
        inp = _funnel_cohort_inputs(db, hours)
    hours_i = inp["hours_i"]
    window_end = inp["window_end"]
    now = inp["now"]
    emitted = inp["emitted_count"]
    still_open = inp["still_open"]
    proposed = inp["proposed_count"]
    setup_count = inp["setup_count"]
    active_setup_count = inp["active_setup_count"]
    terminal_breakdown = inp["terminal_breakdown"]

    # queued / reviewed / expired now come from the consolidated
    # _funnel_cohort_inputs() scan (perf item 3), so no extra queries here.
    queued = inp["queued"]
    reviewed = inp["reviewed"]
    expired = inp["expired"]

    # Latest ranker-view snapshot (what the operator saw on its last eval)
    snapshot: dict = {}
    snapshot_read_failed = False
    with _PhaseTimer(phases, "snapshot"):
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
            snapshot_read_failed = True

    executable_count = int(snapshot.get("executable_count") or 0)
    excluded_by_reason = list(snapshot.get("excluded_candidates_summary") or [])

    # Enrich TTL-expired examples with expired_seconds so the UI doesn't have to
    # re-parse timestamps in JS. Values are Best-effort: older snapshots may not
    # carry valid_until (pre-enrichment) — we simply leave the field null.
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

    # Universe snapshot age is generation context, not a queued-carrier deadline.
    snapshot_age_sec = None
    with _PhaseTimer(phases, "universe"):
        try:
            srow = db.execute(
                "SELECT created_at FROM universe_snapshots "
                "WHERE market_type='crypto' AND status='active' "
                "AND (expires_at IS NULL OR datetime(expires_at)>datetime(?)) "
                "ORDER BY created_at DESC LIMIT 1",
                (window_end,),
            ).fetchone()
            if srow and srow["created_at"]:
                parsed = datetime.fromisoformat(str(srow["created_at"]).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                snapshot_age_sec = int((now - parsed).total_seconds())
        except Exception:
            snapshot_age_sec = None

    candidate_snapshot_age_sec = None
    candidate_snapshot_freshness = "unavailable" if snapshot_read_failed else "missing"
    candidate_snapshot_stale_after_sec = 10 * 60
    candidate_snapshot_at = snapshot.get("snapshot_at")
    if candidate_snapshot_at:
        try:
            parsed = datetime.fromisoformat(
                str(candidate_snapshot_at).replace("Z", "+00:00")
            )
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            candidate_snapshot_age_sec = max(0, int((now - parsed).total_seconds()))
            candidate_snapshot_freshness = (
                "fresh"
                if candidate_snapshot_age_sec <= candidate_snapshot_stale_after_sec
                else "stale"
            )
        except (TypeError, ValueError):
            candidate_snapshot_freshness = "unavailable"

    # Perf (Phase 2): deep_copy=False — this endpoint only reads config
    # (never mutates it), so return the cached object directly and skip the
    # per-request deep copy of the entire config tree.
    from aletheia.config.loader import load_config
    from aletheia.universe.allocator import _FAMILY_TTL
    cfg = load_config(deep_copy=False)
    min_confidence = getattr(cfg.trading, "min_confidence_to_trade", "medium")
    from aletheia.analytics.opportunity_progress import build_current_entry_status

    with _PhaseTimer(phases, "build_current"):
        current = build_current_entry_status(
            db,
            now=now,
            window_hours=hours_i,
            emitted_count=emitted,
            open_opportunities=still_open,
            proposed_count=proposed,
            setup_count=setup_count,
            active_setup_count=active_setup_count,
            terminal_breakdown=terminal_breakdown,
        )
    # Close the total timer + attach the Server-Timing header before returning.
    total_timer.__exit__(None, None, None)
    if response is not None:
        response.headers["Server-Timing"] = _format_server_timing(phases)

    payload = {
        "window_hours": hours_i,
        "emitted_count": emitted,
        "setup_count": setup_count,
        "active_setup_count": active_setup_count,
        "setup_identity_basis": "symbol+market+direction+scanner_family",
        "reviewed_count": reviewed,
        "still_open_count": still_open,
        "queued_count": queued,
        "expired_count": expired,
        "terminal_breakdown": terminal_breakdown,
        "proposed_count": proposed,
        "cohort_basis": "opportunities_emitted_in_window",
        "current": current,
        "snapshot": {
            "executable_count": executable_count,
            "excluded_by_reason": excluded_by_reason,
            "main_reason": snapshot.get("main_reason"),
            "below_floor": snapshot.get("below_floor"),
            "floor": snapshot.get("floor"),
            "snapshot_at": snapshot.get("snapshot_at"),
            "checked_at": snapshot.get("checked_at"),
            "freshness": candidate_snapshot_freshness,
            "age_sec": candidate_snapshot_age_sec,
            "stale_after_sec": candidate_snapshot_stale_after_sec,
        },
        "config": {
            "min_confidence_to_trade": min_confidence,
        },
        "family_ttl_sec": dict(_FAMILY_TTL),
        "universe_snapshot_age_sec": snapshot_age_sec,
    }

    # Perf (Phase 2): write the freshly computed payload to the TTL cache.
    with _funnel_cache_lock:
        _funnel_cache[hours] = (time.monotonic(), payload)
        if len(_funnel_cache) > _FUNNEL_CACHE_MAX_SIZE:
            oldest = sorted(_funnel_cache.items(), key=lambda kv: kv[1][0])
            for k, _ in oldest[:len(_funnel_cache) - _FUNNEL_CACHE_MAX_SIZE]:
                _funnel_cache.pop(k, None)

    return payload


_VALID_OUTCOMES: frozenset[str] = frozenset({
    "trade", "operator_skip", "operator_watch",
    "portfolio_guard_reject",
    "user_reject", "auto_reject", "reject_unknown",
    "conviction_gate_defer",
})


@router.get("/operator-decisions")
def get_operator_decisions(
    hours: int = 24,
    limit: int = 50,
    outcome: str | None = None,
    symbol: str | None = None,
):
    """Recent operator decisions — the "final-stage" companion to the Funnel card.

    Merges three sources into one time-ordered list so the Blotter can surface
    not only pending / accepted proposals but also every kind of rejection that
    happened after a candidate reached the operator:

    - `operator_skip`: pi_decisions.action = 'skip' and no portfolio guard fired —
      the LLM operator saw an executable candidate and decided not to trade
      (reason in `reasoning`).
    - `portfolio_guard_reject`: pi_decisions.action = 'portfolio_guard_skip' (new
      data) or action = 'skip' with guard_block_reason from filter_decisions (legacy
      data) — the LLM approved a trade but a portfolio guard (direction_bias_brake,
      equity guard, etc.) blocked execution after operator action assessment passed
      (#330).
    - `trade`: pi_decisions.action IN ('trade','flip','scale_in') — operator
      chose to trade; a proposal typically follows.
    - `conviction_defer`: operator_conviction_gate rejected after LLM approved —
      conviction below minimum threshold; opportunity re-queued for a later
      evaluation. Sourced from filter_decisions (no pi_decisions row). (issue #327)
    - `user_reject`: trade_proposals.status='rejected' with matching
      audit_events row actor='human', source='dashboard' — a human pressed
      Reject in the dashboard before execution began.
    - `auto_reject`: trade_proposals.status='rejected' with matching
      audit_events row actor='system', source='execution_gateway' — pre-flight/risk
      auto-rejected before execution.
    - `reject_unknown`: rejected Proposal with missing or inconsistent audit actor/source.
    - `conviction_defer`: LLM produced action='trade' but conviction was below the
      minimum threshold — operator_conviction_gate fired, no proposal created, candidate
      re-queued for evaluation after a material context change (#327).

    The reject timestamp defines window membership when available. Counts cover the
    full window before the display list is truncated, and a rejection supersedes the
    earlier trade-intent row for the same Proposal/Opportunity.

    Query parameters:

    - `hours` (default 24) — rolling window in hours.
    - `limit` (default 50, capped at 200) — maximum items returned AFTER the
      optional outcome/symbol filter is applied.
    - `outcome` (comma-separated, optional) — restrict `items` to these outcomes.
      Invalid values are silently dropped; a non-empty parameter whose values
      are all invalid returns zero items (honest to the request). `counts` and
      `total_count` remain over the FULL window so a filter never lies about
      totals — the Blotter's facet-chip counts rely on this invariant.
    - `symbol` (optional) — restrict `items` to a single symbol, canonicalized
      via `to_canonical` (so 'btc/usdt', 'BTC-USDT', 'BTCUSDT' all match).

    All decisions are keyed by `opportunity_id` where available so the UI can
    request the full lifecycle trace via /analytics/opportunity-trace/{id}.

    Read-only. Purely reconstructive over already-persisted evidence.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.data.symbols import to_canonical

    db = get_db()
    hours_i, window_start = _rolling_cutoff(hours)
    limit_i = max(1, min(int(limit or 50), 200))
    window_end = datetime.now(timezone.utc).isoformat()
    current_policy, policy_epochs = _decision_policy_context(db)
    current_policy_id = (
        str(current_policy["epoch_id"]) if current_policy is not None else None
    )
    from aletheia.agent_runtime.runtime_session import current_session

    active_runtime = current_session() or {}
    current_runtime_session_id = active_runtime.get("session_id")

    # Parse optional filters up-front so they're validated once and reused
    # for every item comparison below.
    outcome_filter: set[str] | None = None
    if outcome is not None:
        parsed = {s.strip() for s in outcome.split(",") if s.strip()}
        # Keep only the recognized outcomes. If the caller passed a non-empty
        # value that yielded no valid outcomes, we still enter the filtered
        # branch (returning zero rows) rather than silently ignoring the
        # request — this is honest to the intent.
        outcome_filter = parsed & _VALID_OUTCOMES
        if parsed and not outcome_filter:
            # Sentinel: an outcome nobody matches so `all(x != sentinel)`.
            outcome_filter = {"__no_match__"}

    target_symbol: str | None = None
    if symbol:
        raw = symbol.strip()
        if raw:
            try:
                target_symbol = to_canonical(raw)
            except Exception:
                target_symbol = raw

    pi_items: list[dict] = []
    reject_items: list[dict] = []

    # pi_decisions — operator trade / skip.
    # The guard_blocked subquery detects cases where the LLM approved the trade
    # (operator_action_assessment passed) but direction_bias_brake rejected it
    # before a proposal was created — those skips are portfolio_guard_reject,
    # not operator_skip (#330).
    rows = list(db.execute(
        "SELECT p.id, p.created_at, p.symbol, p.direction, p.market_type, p.action, "
        "p.conviction, p.reasoning, p.opportunity_id, p.proposal_id, "
        "p.evidence_manifest_json, p.runtime_session_id, "
        "COALESCE(p.producer_commit, rs.boot_commit) AS producer_commit, "
        "rs.runtime_id AS attributed_runtime_id, "
        "julianday(p.created_at) AS sort_instant, "
        "(SELECT fd.reason_code FROM filter_decisions fd "
        " WHERE fd.candidate_key='pi_decision:'||p.id "
        " AND fd.filter_name='direction_bias_brake' "
        " AND fd.decision='rejected' LIMIT 1) AS guard_block_reason "
        "FROM pi_decisions p "
        "LEFT JOIN runtime_sessions rs ON rs.session_id=p.runtime_session_id "
        "WHERE p.action IN ('trade','skip','flip','scale_in','watch','portfolio_guard_skip') "
        "AND COALESCE(p.signal_source, 'market_observer') != 'system_cascade' "
        "AND julianday(p.created_at)>=julianday(?) "
        "AND julianday(p.created_at)<=julianday(?) "
        "AND (p.symbol IS NOT NULL AND p.symbol != '') "
        "ORDER BY julianday(p.created_at) DESC, p.rowid DESC",
        (window_start, window_end),
    ))
    for r in rows:
        act = str(r["action"] or "")
        guard_block = r["guard_block_reason"] if "guard_block_reason" in r.keys() else None
        if act == "portfolio_guard_skip" or (act == "skip" and guard_block):
            outcome = "portfolio_guard_reject"
        elif act == "skip":
            outcome = "operator_skip"
        elif act == "watch":
            outcome = "operator_watch"
        else:
            outcome = "trade"
        item: dict = {
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
            "attribution": _decision_attribution(
                decided_at=r["created_at"],
                evidence_manifest_json=r["evidence_manifest_json"],
                runtime_session_id=r["runtime_session_id"],
                runtime_id=r["attributed_runtime_id"],
                producer_commit=r["producer_commit"],
                current_policy_id=current_policy_id,
                epochs=policy_epochs,
                current_runtime_session_id=current_runtime_session_id,
            ),
            "_sort_instant": float(r["sort_instant"] or 0),
        }
        if guard_block:
            item["guard_filter"] = "direction_bias_brake"
            item["guard_block_reason"] = guard_block
        pi_items.append(item)

    # trade_proposals rejected — user vs auto reject determined by paired audit event
    rows = list(db.execute(
        "SELECT tp.id AS proposal_id, tp.opportunity_id, tp.created_at, "
        "tp.symbol, tp.direction, tp.market_type, ae.actor, ae.source, ae.message, "
        "tp.runtime_session_id, COALESCE(tp.producer_commit, rs.boot_commit) "
        "AS producer_commit, rs.runtime_id AS attributed_runtime_id, "
        "ae.timestamp AS rejected_at, "
        "julianday(COALESCE(ae.timestamp,tp.created_at)) AS sort_instant "
        "FROM trade_proposals tp "
        "LEFT JOIN runtime_sessions rs ON rs.session_id=tp.runtime_session_id "
        "LEFT JOIN audit_events ae ON ae.rowid=("
        " SELECT candidate.rowid FROM audit_events candidate "
        " WHERE candidate.entity_id=tp.id "
        " AND candidate.event_type='proposal_rejected' "
        " ORDER BY julianday(candidate.timestamp) DESC, candidate.rowid DESC LIMIT 1"
        ") "
        "WHERE tp.status='rejected' AND ("
        " (ae.timestamp IS NOT NULL AND julianday(ae.timestamp)>=julianday(?) "
        "  AND julianday(ae.timestamp)<=julianday(?)) "
        " OR (ae.timestamp IS NULL AND julianday(tp.created_at)>=julianday(?) "
        "  AND julianday(tp.created_at)<=julianday(?))"
        ") ORDER BY julianday(COALESCE(ae.timestamp,tp.created_at)) DESC, tp.rowid DESC",
        (window_start, window_end, window_start, window_end),
    ))
    for r in rows:
        actor = str(r["actor"] or "")
        source = str(r["source"] or "")
        if actor == "human" and source == "dashboard":
            outcome = "user_reject"
        elif actor == "system" and source == "execution_gateway":
            outcome = "auto_reject"
        else:
            outcome = "reject_unknown"
        reject_items.append({
            "opportunity_id": r["opportunity_id"],
            "proposal_id": r["proposal_id"],
            "decision_id": None,
            "symbol": r["symbol"],
            "direction": r["direction"],
            "market_type": r["market_type"],
            "decided_at": r["rejected_at"] or r["created_at"],
            "outcome": outcome,
            "action": "reject",
            "conviction": None,
            "one_line_reason": (
                _first_line(r["message"], 140)
                or "Rejected; audit attribution unavailable"
            ),
            "rejected_actor": actor or None,
            "rejected_source": source or None,
            "time_basis": "rejected_at" if r["rejected_at"] else "proposal_created_fallback",
            "attribution": _decision_attribution(
                decided_at=r["rejected_at"] or r["created_at"],
                evidence_manifest_json=None,
                runtime_session_id=r["runtime_session_id"],
                runtime_id=r["attributed_runtime_id"],
                producer_commit=r["producer_commit"],
                current_policy_id=current_policy_id,
                epochs=policy_epochs,
                current_runtime_session_id=current_runtime_session_id,
            ),
            "_sort_instant": float(r["sort_instant"] or 0),
        })


    rejected_proposals = {item["proposal_id"] for item in reject_items if item["proposal_id"]}
    rejected_opportunities = {
        item["opportunity_id"] for item in reject_items if item["opportunity_id"]
    }
    pi_items = [
        item for item in pi_items
        if not (
            item["outcome"] == "trade"
            and (
                (
                    item.get("proposal_id") is not None
                    and item.get("proposal_id") in rejected_proposals
                )
                or (
                    item.get("proposal_id") is None
                    and item.get("opportunity_id") in rejected_opportunities
                )
            )
        )
    ]

    # conviction gate deferrals — LLM said trade but conviction was below minimum.
    # These never produce a pi_decisions row (DL-S47 non-terminal defer), so the
    # operator-decisions API was previously blind to them. Fixes #327: source from
    # filter_decisions where the conviction gate rejected AFTER the action assessment
    # passed, identified by matching candidate_key + evaluated_at across both rows.
    import json as _json
    conv_rows = list(db.execute(
        "SELECT fd.opportunity_id, fd.symbol, fd.direction, fd.market_type, "
        "fd.evaluated_at, fd.reason_code, fd.input_snapshot, "
        "julianday(fd.evaluated_at) AS sort_instant "
        "FROM filter_decisions fd "
        "JOIN filter_decisions fd2 ON fd2.candidate_key = fd.candidate_key "
        "  AND fd2.evaluated_at = fd.evaluated_at "
        "  AND fd2.filter_name = 'operator_action_assessment' "
        "  AND fd2.decision = 'passed' "
        "WHERE fd.filter_name = 'operator_conviction_gate' "
        "AND fd.decision = 'rejected' "
        "AND julianday(fd.evaluated_at) >= julianday(?) "
        "AND julianday(fd.evaluated_at) <= julianday(?) "
        "ORDER BY julianday(fd.evaluated_at) DESC, fd.rowid DESC",
        (window_start, window_end),
    ))
    terminal_opportunity_ids = {
        item["opportunity_id"] for item in pi_items + reject_items
        if item["opportunity_id"]
    }
    conv_items: list[dict] = []
    for r in conv_rows:
        opp_id = r["opportunity_id"]
        # Suppress if the opportunity already has a terminal decision in this window.
        if opp_id and opp_id in terminal_opportunity_ids:
            continue
        try:
            inputs = _json.loads(r["input_snapshot"] or "{}")
            conviction_val = inputs.get("conviction")
        except Exception:
            conviction_val = None
        conv_items.append({
            "opportunity_id": opp_id,
            "proposal_id": None,
            "decision_id": None,
            "symbol": r["symbol"],
            "direction": r["direction"],
            "market_type": r["market_type"],
            "decided_at": r["evaluated_at"],
            "outcome": "conviction_gate_defer",
            "action": "conviction_gate_defer",
            "conviction": conviction_val,
            "one_line_reason": (
                f"conviction_below_minimum (conviction: {conviction_val})"
                if conviction_val is not None
                else "conviction_below_minimum"
            ),
            "_sort_instant": float(r["sort_instant"] or 0),
        })

    # conviction_deferred: operator_decision_deferred_low_conviction events from
    # operator_evaluation_events (#327 follow-up — low-conviction defer via evaluation loop).
    defer_rows = []
    try:
        defer_rows = list(db.execute(
            "SELECT id, queue_snapshot_json, created_at, "
            "julianday(created_at) AS sort_instant "
            "FROM operator_evaluation_events "
            "WHERE event_kind='operator_decision_deferred_low_conviction' "
            "AND julianday(created_at) >= julianday(?) "
            "AND julianday(created_at) <= julianday(?) "
            "ORDER BY julianday(created_at) DESC, rowid DESC",
            (window_start, window_end),
        ))
    except Exception:
        pass
    terminal_opportunity_ids_for_defer = {
        item["opportunity_id"] for item in pi_items + reject_items + conv_items
        if item.get("opportunity_id")
    }
    defer_items: list[dict] = []
    for r in defer_rows:
        try:
            snapshot = _json.loads(r["queue_snapshot_json"] or "{}")
        except Exception:
            snapshot = {}
        opp_id = snapshot.get("opportunity_id")
        defer_reason = snapshot.get("defer_reason")
        if opp_id and opp_id in terminal_opportunity_ids_for_defer:
            continue
        defer_items.append({
            "opportunity_id": opp_id,
            "proposal_id": None,
            "decision_id": r["id"],
            "symbol": None,
            "direction": None,
            "market_type": None,
            "decided_at": r["created_at"],
            "outcome": "conviction_deferred",
            "action": "conviction_deferred",
            "conviction": None,
            "one_line_reason": defer_reason,
            "_sort_instant": float(r["sort_instant"] or 0),
        })

    all_items = pi_items + reject_items + conv_items + defer_items
    all_items.sort(key=lambda x: float(x.get("_sort_instant") or 0), reverse=True)

    counts = {"trade": 0, "operator_skip": 0, "operator_watch": 0,
              "portfolio_guard_reject": 0,
              "user_reject": 0, "auto_reject": 0, "reject_unknown": 0,
              "conviction_gate_defer": 0, "conviction_deferred": 0}
    for it in all_items:
        oc = it.get("outcome")
        if oc in counts:
            counts[oc] += 1

    # Apply optional outcome / symbol filters AFTER the full-window counts
    # so `counts` stays honest (facet chips on the Blotter show unfiltered
    # totals). The rejection-supersedes-trade merge above (see line ~929)
    # already ran, so filtering here doesn't reintroduce hidden duplicates.
    has_filter = outcome_filter is not None or target_symbol is not None
    if has_filter:
        def _matches(item: dict) -> bool:
            if outcome_filter is not None and item.get("outcome") not in outcome_filter:
                return False
            if target_symbol is not None:
                raw = item.get("symbol") or ""
                try:
                    canon = to_canonical(raw) if raw else raw
                except Exception:
                    canon = raw
                if canon != target_symbol:
                    return False
            return True
        filtered_all = [it for it in all_items if _matches(it)]
    else:
        filtered_all = all_items

    items = filtered_all[:limit_i]
    for item in items:
        item.pop("_sort_instant", None)

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
        # total_count remains the FULL-window count so downstream UIs can
        # distinguish "matched" from "total available".
        "total_count": len(all_items),
        # matched_count = rows the filter matched (before truncation to limit).
        # Equals total_count when no filter is applied.
        "matched_count": len(filtered_all),
        "counts": counts,
        "current_context": {
            "policy_epoch_id": current_policy_id,
            "runtime_session_id": current_runtime_session_id,
            "runtime_id": active_runtime.get("runtime_id"),
            "producer_commit": active_runtime.get("boot_commit"),
        },
        "current_policy_counts": _decision_counts(all_items, "current"),
        "historical_policy_counts": _decision_counts(all_items, "historical"),
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
    query_errors: dict[str, str] = {}

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
        query_errors["pi_guards_window"] = "read_failed"

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
        query_errors["pi_lessons_window"] = "read_failed"

    # ps_lessons summary (status + freshness)
    ps_summary: dict = {
        "availability": "available",
        "total": 0,
        "by_status": [],
        "latest_created_at": None,
    }
    try:
        ps_summary["total"] = int(db.execute("SELECT COUNT(*) FROM ps_lessons").fetchone()[0])
        for r in db.execute("SELECT status, COUNT(*) n FROM ps_lessons GROUP BY status"):
            ps_summary["by_status"].append({"status": r["status"], "count": int(r["n"])})
        ps_summary["latest_created_at"] = db.execute(
            "SELECT MAX(created_at) FROM ps_lessons"
        ).fetchone()[0]
    except Exception:
        ps_summary = {
            "availability": "unavailable",
            "total": None,
            "by_status": None,
            "latest_created_at": None,
        }
        query_errors["ps_lessons"] = "read_failed"

    # Serenity opportunities active count (already directly consumed by PI; useful as reference)
    serenity_active: int | None = 0
    try:
        serenity_active = int(db.execute(
            "SELECT COUNT(*) FROM serenity_opportunities WHERE status='active'"
        ).fetchone()[0])
    except Exception:
        serenity_active = None
        query_errors["serenity_opportunities"] = "read_failed"

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
    contradictions_checked = True
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
        contradictions_checked = False
        query_errors["contradictions"] = "read_failed"

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
            "checked": contradictions_checked,
            "count": len(contradictions) if contradictions_checked else None,
            "items": contradictions[:10],
            "note": "Conservative support-vs-block detector; findings require human review.",
        },
        "query_errors": query_errors,
    }


def _compute_learning_contribution():
    """Read-only measured contribution plus compatibility activity aggregates.

    The ``lco-v1`` fields distinguish current measurement, integrity, operations
    and authority. Legacy aggregate fields remain temporarily for existing
    consumers and are activity context, not evidence of contribution.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.learning.control import get_learning_job_health, job_health_view

    db = get_db()
    legacy_query_errors: dict[str, str] = {}

    # Exposure counts by path_id
    exposures_by_path: list[dict] = []
    try:
        for r in db.execute(
            "SELECT path_id, COUNT(*) n, "
            "COUNT(DISTINCT experiment_id) experiment_count, "
            "MAX(exposed_at) latest_exposed_at "
            "FROM learning_exposures GROUP BY path_id ORDER BY n DESC"
        ):
            exposures_by_path.append({
                "path_id": r["path_id"],
                "exposure_n": int(r["n"]),
                "experiment_count": int(r["experiment_count"]),
                "latest_exposed_at": r["latest_exposed_at"],
            })
    except Exception:
        legacy_query_errors["exposures_by_path"] = "read_failed"

    # Verdict distribution — keyed by the actual `verdict` column (not `classification`).
    # Schema errors surface in the `error` key (not silently as total=0).
    verdicts_by_verdict: list[dict] = []
    total_verdicts = 0
    verdicts_error: str | None = None
    try:
        for r in db.execute(
            "SELECT verdict, COUNT(*) n, MAX(created_at) latest_at "
            "FROM learning_contribution_verdicts "
            "GROUP BY verdict ORDER BY n DESC"
        ):
            verdicts_by_verdict.append({
                "verdict": r["verdict"],
                "n": int(r["n"]),
                "latest_at": r["latest_at"],
            })
            total_verdicts += int(r["n"])
    except Exception:
        verdicts_error = "read_failed"
        total_verdicts = None  # type: ignore[assignment]
        legacy_query_errors["verdicts"] = "read_failed"

    # Experiment stage distribution
    experiments_by_stage: list[dict] = []
    try:
        for r in db.execute(
            "SELECT stage, status, COUNT(*) n "
            "FROM learning_experiments GROUP BY stage, status ORDER BY n DESC"
        ):
            experiments_by_stage.append({
                "stage": r["stage"],
                "status": r["status"],
                "n": int(r["n"]),
            })
    except Exception:
        legacy_query_errors["experiments_by_stage"] = "read_failed"

    # Lifecycle events written (non-zero = lifecycle controller is active)
    lifecycle_event_n: int | None = 0
    try:
        lifecycle_event_n = int(
            db.execute("SELECT COUNT(*) FROM learning_lifecycle_events").fetchone()[0]
        )
    except Exception:
        lifecycle_event_n = None
        legacy_query_errors["lifecycle_event_n"] = "read_failed"

    # authority_applied rate (treatment applied vs total treatment exposures)
    authority_applied_n: int | None = 0
    total_treatment_n: int | None = 0
    try:
        r = db.execute(
            "SELECT COUNT(*) total, "
            "SUM(CASE WHEN json_extract(manifest_json,'$.authority_applied')=1 THEN 1 ELSE 0 END) applied "
            "FROM learning_exposures WHERE assignment='treatment'"
        ).fetchone()
        if r:
            total_treatment_n = int(r["total"] or 0)
            authority_applied_n = int(r["applied"] or 0)
    except Exception:
        authority_applied_n = None
        total_treatment_n = None
        legacy_query_errors["authority_applied"] = "read_failed"

    # Job health for the four key learning jobs
    job_health: dict[str, dict] = {}
    for job_id in (
        "learning_contribution_evaluator",
        "learning_lifecycle_controller",
        "lesson_reviewer",
        "dreaming_daily",
    ):
        try:
            h = get_learning_job_health(job_id)
            health = job_health_view(h)
            health.pop("last_error", None)
            health.pop("last_failure_reason", None)
            job_health[job_id] = health
        except Exception:
            job_health[job_id] = {"status": "unknown"}

    legacy = {
        "exposures_by_path": exposures_by_path,
        "verdicts": {
            "total": total_verdicts,
            "by_verdict": verdicts_by_verdict,
            **({"query_error": verdicts_error} if verdicts_error else {}),
        },
        "experiments_by_stage": experiments_by_stage,
        "lifecycle_event_n": lifecycle_event_n,
        "authority_applied": {
            "treatment_n": total_treatment_n,
            "applied_n": authority_applied_n,
            "applied_rate": (
                round(authority_applied_n / total_treatment_n, 4)
                if authority_applied_n is not None and total_treatment_n
                else None
            ),
        },
        "job_health": job_health,
        "legacy_query_errors": legacy_query_errors,
    }
    try:
        from aletheia.learning.read_models import (
            build_learning_contribution_projection,
        )

        projection = build_learning_contribution_projection()
    except Exception:
        from aletheia.learning.read_models import (
            configured_learning_stale_after_sec,
            unavailable_learning_contribution_projection,
        )

        try:
            stale_after_sec = configured_learning_stale_after_sec()
        except Exception:
            stale_after_sec = None
        projection = unavailable_learning_contribution_projection(
            "projection_read_failed",
            stale_after_sec=stale_after_sec,
        )
    return {**legacy, **projection}


_learning_contribution_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_learning_contribution_cache_lock = RLock()
_LEARNING_CONTRIBUTION_CACHE_TTL_SEC = 30.0


def _learning_contribution_monotonic() -> float:
    return time.monotonic()


@router.get("/learning-contribution")
def get_learning_contribution(response: Response = None):
    """Return the bounded owner projection with an explainable monotonic TTL."""
    from aletheia.config.loader import get_data_dir

    if response is None:
        return _compute_learning_contribution()
    started = _now_ms()
    phases: dict[str, float] = {}
    cache_key = (
        "lco-v1",
        str(get_data_dir()),
    )
    now_mono = _learning_contribution_monotonic()
    with _learning_contribution_cache_lock:
        cached = _learning_contribution_cache.get(cache_key)
        if cached is not None and now_mono - cached[0] < _LEARNING_CONTRIBUTION_CACHE_TTL_SEC:
            payload = deepcopy(cached[1])
            phases["cache_hit"] = 0.0
        else:
            payload = None

    if payload is None:
        with _PhaseTimer(phases, "ledger"):
            payload = _compute_learning_contribution()
        with _learning_contribution_cache_lock:
            current = _learning_contribution_cache.get(cache_key)
            if current is None or current[0] <= now_mono:
                _learning_contribution_cache[cache_key] = (
                    _learning_contribution_monotonic(),
                    deepcopy(payload),
                )

    phases["total"] = _now_ms() - started
    if response is not None:
        response.headers["Server-Timing"] = _format_server_timing(phases)
    return payload
