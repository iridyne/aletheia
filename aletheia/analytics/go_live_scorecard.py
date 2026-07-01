"""Go-Live Scorecard — quantitative readiness assessment for demo→live transition.

Aggregates 9 criteria into a composite score (0-100) with traffic light status.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def compute_scorecard() -> dict:
    """Compute the go-live readiness scorecard. Pure read-only, no side effects."""
    criteria = [
        _check_trade_volume(),
        _check_profit_factor(),
        _check_win_rate(),
        _check_max_drawdown(),
        _check_exchange_connectivity(),
        _check_risk_violations(),
        _check_data_coverage(),
        _check_model_health(),
        _check_reconciliation(),
        _check_calibration(),
    ]

    composite = sum(c["score"] * c["weight"] for c in criteria)
    if composite < 50:
        traffic_light = "red"
    elif composite <= 75:
        traffic_light = "yellow"
    else:
        traffic_light = "green"

    blockers = [c["detail"] for c in criteria if c["status"] == "fail"]

    return {
        "composite_score": round(composite, 1),
        "traffic_light": traffic_light,
        "criteria": criteria,
        "blockers": blockers,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def _genuine_closed_pnls(order_by_closed_at: bool = False) -> list[float]:
    """realized_pnl of GENUINE closed positions (synthetic/administrative excluded).

    Reconciler orphan sweeps (reconciled_gone) and bug/demo cleanups are not trade
    outcomes; counting them pollutes PF / win-rate / drawdown / volume (Win-line A).
    NULL close_reason is kept (unknown ≠ synthetic). See close_reasons.py +
    docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.analytics.close_reasons import SYNTHETIC_CLOSE_REASONS
    syn = tuple(SYNTHETIC_CLOSE_REASONS)
    placeholders = ",".join("?" * len(syn))
    order = " ORDER BY closed_at ASC" if order_by_closed_at else ""
    sql = (
        "SELECT realized_pnl FROM positions WHERE status='closed' "
        f"AND (close_reason IS NULL OR close_reason NOT IN ({placeholders})){order}"
    )
    rows = get_db().execute(sql, syn).fetchall()
    return [r[0] for r in rows if r[0] is not None]


def _criterion(key: str, label: str, score: float, weight: float, detail: str) -> dict:
    if score >= 100:
        status = "pass"
    elif score <= 0:
        status = "fail"
    else:
        status = "warning"
    return {
        "key": key,
        "label": label,
        "score": min(100.0, max(0.0, round(score, 1))),
        "weight": weight,
        "weighted_contribution": round(min(100.0, max(0.0, score)) * weight, 1),
        "detail": detail,
        "status": status,
    }


def _check_trade_volume() -> dict:
    try:
        count = len(_genuine_closed_pnls())
        target = 50
        score = min(count / target, 1.0) * 100
        return _criterion("trade_volume", "Trade Volume (≥50 closed)", score, 0.135,
                          f"{count} trades (target ≥{target})")
    except Exception:
        return _criterion("trade_volume", "Trade Volume (≥50 closed)", 0, 0.135, "unavailable")


def count_consecutive_zero_fill_days() -> int:
    """Count consecutive calendar days with zero new position opens (D8-S04 / product §8 K2).

    Walks backwards from today counting days with no new opens. Returns the integer
    day count. Three or more → trading loop is stalled (K2 kill condition).
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT DATE(opened_at) as open_date FROM positions "
            "WHERE status IN ('open','closed') ORDER BY opened_at DESC LIMIT 500"
        ).fetchall()
        open_dates = {r[0] for r in rows if r[0]}
        from datetime import date, timedelta
        day = date.today()
        consecutive = 0
        for _ in range(30):  # check at most 30 days back
            if day.isoformat() in open_dates:
                break
            consecutive += 1
            day -= timedelta(days=1)
        return consecutive
    except Exception:
        return 0


def check_kill_condition_consecutive_rounds(window_days: int = 14) -> dict:
    """Detect consecutive experiment rounds where PF < 0.5 (D8-S02 / product §8 K1).

    Splits closed positions into 14-day windows (one per experiment round) and
    counts how many consecutive trailing windows have PF below the kill floor.
    Returns {"consecutive_kill_rounds": int, "kill_threshold": 0.5, "window_days": int}.
    Two consecutive kill rounds → recommend terminating the experiment.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT realized_pnl, closed_at FROM positions "
            "WHERE status='closed' ORDER BY closed_at DESC"
        ).fetchall()
        if not rows:
            return {"consecutive_kill_rounds": 0, "kill_threshold": 0.5, "window_days": window_days}

        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        kill_floor = 0.5
        consecutive = 0

        for i in range(8):  # check up to 8 trailing windows
            start = now - timedelta(days=window_days * (i + 1))
            end = now - timedelta(days=window_days * i)
            window_rows = [r for r in rows if start.isoformat()[:10] <= str(r[1])[:10] < end.isoformat()[:10]]
            if not window_rows:
                break  # no data → stop counting
            gp = sum(r[0] for r in window_rows if r[0] > 0)
            gl = sum(abs(r[0]) for r in window_rows if r[0] < 0)
            pf = gp / gl if gl > 0 else 99.0
            if pf < kill_floor:
                consecutive += 1
            else:
                break  # streak broken

        return {"consecutive_kill_rounds": consecutive, "kill_threshold": kill_floor, "window_days": window_days}
    except Exception:
        return {"consecutive_kill_rounds": 0, "kill_threshold": 0.5, "window_days": window_days}


def compute_pnl_by_trade_school() -> dict:
    """Return profit_factor and win_rate grouped by trade_school (D2-S03).

    Enables serenity vs main-track PnL isolation without mixing the two pools.
    Returns {trade_school: {"profit_factor": float, "win_rate": float, "count": int}}.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        rows = db.execute(
            "SELECT COALESCE(trade_school, 'short_term') AS trade_school, realized_pnl "
            "FROM positions WHERE status='closed'"
        ).fetchall()
        result: dict = {}
        from collections import defaultdict
        buckets: dict = defaultdict(list)
        for r in rows:
            buckets[r[0]].append(r[1] or 0.0)
        for school, pnls in buckets.items():
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = sum(abs(p) for p in pnls if p < 0)
            wins = sum(1 for p in pnls if p > 0)
            pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
            wr = wins / len(pnls) if pnls else 0.0
            result[school] = {"profit_factor": round(pf, 3), "win_rate": round(wr, 3), "count": len(pnls)}
        return result
    except Exception:
        return {}


def _check_profit_factor() -> dict:
    try:
        pnls = _genuine_closed_pnls()
        if not pnls:
            return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18, "no trades")
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = sum(abs(p) for p in pnls if p < 0)
        pf = gross_profit / gross_loss if gross_loss > 0 else 99.0
        # Kill-floor: PF < 0.5 is a hard stop regardless of go-live target (product §8 K1)
        if pf < 0.5:
            return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18,
                              f"PF {pf:.2f} — below kill threshold 0.5")
        target = 1.2
        score = min(pf / target, 1.0) * 100 if pf < target else 100.0
        return _criterion("profit_factor", "Profit Factor (≥1.2)", score, 0.18,
                          f"PF {pf:.2f} (target ≥{target})")
    except Exception:
        return _criterion("profit_factor", "Profit Factor (≥1.2)", 0, 0.18, "unavailable")


def _check_win_rate() -> dict:
    try:
        pnls = _genuine_closed_pnls()
        if not pnls:
            return _criterion("win_rate", "Win Rate (≥45%)", 0, 0.135, "no trades")
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        wr = wins / total * 100
        target = 45.0
        score = min(wr / target, 1.0) * 100 if wr < target else 100.0
        return _criterion("win_rate", "Win Rate (≥45%)", score, 0.135,
                          f"{wr:.1f}% (target ≥{target}%)")
    except Exception:
        return _criterion("win_rate", "Win Rate (≥45%)", 0, 0.135, "unavailable")


def _check_max_drawdown() -> dict:
    try:
        pnls = _genuine_closed_pnls(order_by_closed_at=True)
        if not pnls:
            return _criterion("max_drawdown", "Max Drawdown (<10%)", 100, 0.135, "no trades")
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
        dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0
        target = 10.0
        score = 100.0 if dd_pct <= target else max(0, (1 - (dd_pct - target) / target) * 100)
        return _criterion("max_drawdown", "Max Drawdown (<10%)", score, 0.135,
                          f"{dd_pct:.1f}% drawdown (target <{target}%)")
    except Exception:
        return _criterion("max_drawdown", "Max Drawdown (<10%)", 0, 0.135, "unavailable")


def _check_exchange_connectivity() -> dict:
    try:
        from aletheia.portfolio.exchange_sync import get_cached_state
        state = get_cached_state()
        if state and state.get("age_seconds", 999) < 300:
            return _criterion("exchange_ok", "Exchange Connected", 100, 0.09, "connected")
        return _criterion("exchange_ok", "Exchange Connected", 0, 0.09,
                          "stale or missing exchange state")
    except Exception:
        return _criterion("exchange_ok", "Exchange Connected", 0, 0.09, "unavailable")


def _check_risk_violations() -> dict:
    try:
        from aletheia.storage.sqlite import get_db
        import json
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        rows = db.execute(
            "SELECT violations FROM risk_decisions WHERE created_at >= ? ORDER BY created_at DESC LIMIT 20",
            (cutoff,),
        ).fetchall()
        violation_count = 0
        for r in rows:
            v = r[0]
            if v and v != "[]":
                parsed = json.loads(v) if isinstance(v, str) else v
                if parsed:
                    violation_count += 1
        score = 100.0 if violation_count == 0 else 0.0
        return _criterion("risk_clean", "Zero Risk Violations (24h)", score, 0.09,
                          f"{violation_count} violation events" if violation_count else "clean")
    except Exception:
        return _criterion("risk_clean", "Zero Risk Violations (24h)", 50, 0.09, "cannot verify")


def _check_data_coverage() -> dict:
    try:
        from aletheia.agent_shell.trading_context import _get_data_health_summary
        health = _get_data_health_summary()
        if health.get("ohlcv_ready"):
            return _criterion("data_coverage", "Data Coverage Complete", 100, 0.045, "all symbols covered")
        ready = health.get("members_with_full_coverage", 0)
        total = max(health.get("total_members", 1), 1)
        score = ready / total * 100
        return _criterion("data_coverage", "Data Coverage Complete", score, 0.045,
                          f"{ready}/{total} symbols have full OHLCV")
    except Exception:
        return _criterion("data_coverage", "Data Coverage Complete", 0, 0.045, "unavailable")


def _check_model_health() -> dict:
    try:
        from aletheia.model_provider.fallback import get_model_health_snapshot
        snap = get_model_health_snapshot()
        if any(p.get("available") for p in snap.get("providers", [])):
            return _criterion("model_health", "LLM Model Available", 100, 0.045, "at least one provider up")
        return _criterion("model_health", "LLM Model Available", 0, 0.045, "no providers available")
    except Exception:
        return _criterion("model_health", "LLM Model Available", 50, 0.045, "cannot verify")


def _check_reconciliation() -> dict:
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = db.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE event_type = 'reconciliation_error' AND timestamp >= ?",
            (cutoff,),
        ).fetchone()
        errors = row[0] if row else 0
        score = 100.0 if errors == 0 else 0.0
        return _criterion("reconciliation", "No Reconciliation Errors (7d)", score, 0.045,
                          f"{errors} errors in 7 days" if errors else "clean")
    except Exception:
        return _criterion("reconciliation", "No Reconciliation Errors (7d)", 50, 0.045, "cannot verify")


def _check_calibration() -> dict:
    try:
        from aletheia.analytics.confidence_calibration import compute_calibration_score, get_latest_calibration_report
        score = compute_calibration_score()
        report = get_latest_calibration_report()
        if not report:
            detail = "no data yet"
        else:
            total = report.get("total_executed", 0)
            ece = report.get("ece")
            grade = report.get("ece_grade", "?")
            detail = f"ECE={ece:.3f} ({grade}), {total} trades" if ece is not None else f"{total} trades"
        return _criterion("calibration", "Conviction Calibration (ECE<0.10)", score, 0.10, detail)
    except Exception:
        return _criterion("calibration", "Conviction Calibration (ECE<0.10)", 50, 0.10, "unavailable")
