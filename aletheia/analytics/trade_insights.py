"""Comprehensive trade analytics for the Insights visualization panel.

Single SQL query over closed positions; all chart data computed in Python.
Date filter aligned with analytics.py _ROUND1_START_UTC so all PnL numbers
are consistent across the Analytics tab and Trade Insights panel.
"""
from __future__ import annotations

from datetime import datetime, timezone

# Keep in sync with aletheia/server/routes/analytics.py _ROUND1_START_UTC
_SINCE_UTC = "2026-06-22"


def build_trade_insights() -> dict:
    from aletheia.analytics.outcome_integrity import (
        build_outcome_cohort_summary,
        eligible_strategy_outcomes,
        raw_account_events,
    )

    rows = eligible_strategy_outcomes(closed_after=_SINCE_UTC)
    raw_rows = raw_account_events(closed_after=_SINCE_UTC, limit=10_000)
    
    # #1254: truncation metadata
    source_total = len(raw_rows)
    loaded_count = len(rows)
    cohort_complete = loaded_count < 10_000
    loaded_from = rows[0]["closed_at"] if rows else None
    loaded_through = rows[-1]["closed_at"] if rows else None

    return {
        **build_outcome_cohort_summary(closed_after=_SINCE_UTC),
        "since":                  _SINCE_UTC,
        "trade_count":            loaded_count,
        "source_total_raw_events": source_total,
        "loaded_count":           loaded_count,
        "cohort_complete":        cohort_complete,
        "loaded_from":            loaded_from,
        "loaded_through":         loaded_through,
        "equity_curve":           _equity_curve(rows),
        "drawdown_curve":         _drawdown_curve(rows),
        "by_direction":           _by_direction(rows),
        "profit_factor":          _profit_factor(rows),
        "by_day_of_week":         _by_day_of_week(rows),
        "by_hour":                _by_hour(rows),
        "pnl_distribution":       _pnl_distribution(rows),
        "by_regime":              _by_regime(rows),
        "streaks":                _streaks(rows),
        "hold_vs_pnl":            _hold_vs_pnl(rows),
        "hour_day_heatmap":       _hour_day_heatmap(rows),
        "trade_frequency":        _trade_frequency(rows),
        "symbol_direction_matrix": _symbol_direction_matrix(rows),
        "hold_duration_buckets":  _hold_duration_buckets(rows),
        "r_multiple_distribution": _r_multiple_distribution(rows),
        "by_exit_reason":         _by_exit_reason(rows),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pnl_pct(row) -> float:
    pnl = row["realized_pnl"] or 0.0
    notional = (row["entry_price"] or 1.0) * (row["quantity"] or 1.0)
    return pnl / notional * 100.0 if notional > 0 else 0.0


def _hold_hours(row) -> float | None:
    try:
        o = datetime.fromisoformat(str(row["opened_at"]).replace("Z", "+00:00"))
        c = datetime.fromisoformat(str(row["closed_at"]).replace("Z", "+00:00"))
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        return max(0.0, (c - o).total_seconds() / 3600.0)
    except Exception:
        return None


def _fmt_time(iso: str) -> str:
    """Shorten ISO timestamp for chart x-axis labels."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%m/%d")
    except Exception:
        return iso[:10]


# ---------------------------------------------------------------------------
# Chart builders
# ---------------------------------------------------------------------------

def _equity_curve(rows) -> list[dict]:
    cum = 0.0
    result = []
    for r in rows:
        pnl = r["realized_pnl"] or 0.0
        cum += pnl
        result.append({
            "time": _fmt_time(r["closed_at"] or ""),
            "pnl": round(pnl, 4),
            "cumulative_pnl": round(cum, 4),
        })
    return result


def _drawdown_curve(rows) -> list[dict]:
    cum = 0.0
    peak = 0.0
    result = []
    for r in rows:
        cum += r["realized_pnl"] or 0.0
        if cum > peak:
            peak = cum
        dd = cum - peak  # always <= 0
        result.append({
            "time": _fmt_time(r["closed_at"] or ""),
            "drawdown": round(dd, 4),
        })
    return result


def _by_direction(rows) -> dict:
    buckets: dict[str, dict] = {}
    for r in rows:
        d = r["direction"] or "unknown"
        if d not in buckets:
            buckets[d] = {"trade_count": 0, "wins": 0, "total_pnl": 0.0,
                          "total_pnl_pct": 0.0, "total_hold": 0.0}
        b = buckets[d]
        b["trade_count"] += 1
        pnl = r["realized_pnl"] or 0.0
        b["total_pnl"] += pnl
        b["total_pnl_pct"] += _pnl_pct(r)
        if pnl > 0:
            b["wins"] += 1
        h = _hold_hours(r)
        if h is not None:
            b["total_hold"] += h

    result = {}
    for d, b in buckets.items():
        tc = b["trade_count"]
        result[d] = {
            "trade_count": tc,
            "win_rate": round(b["wins"] / tc * 100, 1) if tc else 0.0,
            "total_pnl": round(b["total_pnl"], 4),
            "avg_pnl_pct": round(b["total_pnl_pct"] / tc, 2) if tc else 0.0,
            "avg_hold_hours": round(b["total_hold"] / tc, 1) if tc else 0.0,
        }
    return result


def _profit_factor(rows) -> dict:
    gross_profit = 0.0
    gross_loss = 0.0
    wins = []
    losses = []
    for r in rows:
        pnl = r["realized_pnl"] or 0.0
        if pnl > 0:
            gross_profit += pnl
            wins.append(pnl)
        elif pnl < 0:
            gross_loss += abs(pnl)
            losses.append(pnl)

    # Determine profit_factor_state
    if gross_profit == 0 and gross_loss == 0:
        pf = None
        pf_state = "unavailable_no_outcomes"
    elif gross_loss == 0:
        pf = None
        pf_state = "unbounded_no_losses"
    else:
        pf = round(gross_profit / gross_loss, 2)
        pf_state = "finite"

    return {
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": pf,
        "profit_factor_state": pf_state,
        "avg_win": round(sum(wins) / len(wins), 4) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0.0,
        "largest_win": round(max(wins), 4) if wins else 0.0,
        "largest_loss": round(min(losses), 4) if losses else 0.0,
        "win_count": len(wins),
        "loss_count": len(losses),
    }


_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _by_day_of_week(rows) -> list[dict]:
    buckets: dict[int, dict] = {i: {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0} for i in range(7)}
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["closed_at"]).replace("Z", "+00:00"))
            dow = dt.weekday()  # 0=Mon
        except Exception:
            continue
        b = buckets[dow]
        b["trade_count"] += 1
        pnl = r["realized_pnl"] or 0.0
        if pnl > 0:
            b["wins"] += 1
        b["total_pnl_pct"] += _pnl_pct(r)

    result = []
    for i in range(7):
        b = buckets[i]
        tc = b["trade_count"]
        result.append({
            "day": _DOW[i],
            "trade_count": tc,
            "win_rate": round(b["wins"] / tc * 100, 1) if tc else 0.0,
            "avg_pnl_pct": round(b["total_pnl_pct"] / tc, 2) if tc else 0.0,
        })
    return result


def _by_hour(rows) -> list[dict]:
    buckets: dict[int, dict] = {h: {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0} for h in range(24)}
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["closed_at"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hour = dt.hour
        except Exception:
            continue
        b = buckets[hour]
        b["trade_count"] += 1
        if (r["realized_pnl"] or 0.0) > 0:
            b["wins"] += 1
        b["total_pnl_pct"] += _pnl_pct(r)

    result = []
    for h in range(24):
        b = buckets[h]
        tc = b["trade_count"]
        result.append({
            "hour": h,
            "trade_count": tc,
            "win_rate": round(b["wins"] / tc * 100, 1) if tc else 0.0,
            "avg_pnl_pct": round(b["total_pnl_pct"] / tc, 2) if tc else 0.0,
        })
    return result


def _pnl_distribution(rows) -> list[dict]:
    # 1% bins from -10% to +10%, plus tails
    bins = []
    for lo in range(-10, 10):
        bins.append((f"{lo}%~{lo+1}%", lo, lo + 1))
    bins = [("<-10%", -1e9, -10)] + bins + [(">+10%", 10, 1e9)]

    counts = {label: 0 for label, _, _ in bins}
    for r in rows:
        pct = _pnl_pct(r)
        for label, lo, hi in bins:
            if lo <= pct < hi:
                counts[label] += 1
                break

    return [{"bin": label, "count": counts[label]} for label, _, _ in bins]


def _by_regime(rows) -> list[dict]:
    from aletheia.regime.detector import REGIME_BULL, REGIME_BEAR, REGIME_SIDEWAYS, REGIME_VOLATILE

    buckets: dict[str, dict] = {}
    for r in rows:
        tag = r["regime_tag"]
        regime = tag if tag and str(tag).strip() else "unknown"
        if regime not in buckets:
            buckets[regime] = {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0}
        b = buckets[regime]
        b["trade_count"] += 1
        if (r["realized_pnl"] or 0.0) > 0:
            b["wins"] += 1
        b["total_pnl_pct"] += _pnl_pct(r)

    # #1259: canonical states always emitted; historical extras appended
    canonical = [REGIME_BULL, REGIME_BEAR, REGIME_SIDEWAYS, REGIME_VOLATILE, "unknown"]
    extras = sorted(k for k in buckets if k not in canonical)

    result = []
    for regime in canonical + extras:
        b = buckets.get(regime, {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0})
        tc = b["trade_count"]
        result.append({
            "regime": regime,
            "trade_count": tc,
            "win_rate": round(b["wins"] / tc * 100, 1) if tc else 0.0,
            "avg_pnl_pct": round(b["total_pnl_pct"] / tc, 2) if tc else 0.0,
        })

    # Conservation: every input row must appear exactly once
    assert sum(r["trade_count"] for r in result) == len(rows)

    return result


def _streaks(rows) -> dict:
    max_wins = 0
    max_losses = 0
    cur_wins = 0
    cur_losses = 0
    overall_wins = 0
    overall_losses = 0

    for r in rows:
        pnl = r["realized_pnl"] or 0.0
        if pnl > 0:
            overall_wins += 1
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            overall_losses += 1
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)

    if cur_wins > 0:
        current_streak = cur_wins
        current_streak_type = "win"
    elif cur_losses > 0:
        current_streak = cur_losses
        current_streak_type = "loss"
    else:
        current_streak = 0
        current_streak_type = "none"

    return {
        "max_consecutive_wins": max_wins,
        "max_consecutive_losses": max_losses,
        "current_streak": current_streak,
        "current_streak_type": current_streak_type,
        "total_wins": overall_wins,
        "total_losses": overall_losses,
    }


def _hold_vs_pnl(rows) -> list[dict]:
    result = []
    for r in rows:
        h = _hold_hours(r)
        if h is None:
            continue
        result.append({
            "hold_hours": round(h, 2),
            "pnl_pct": round(_pnl_pct(r), 2),
            "direction": r["direction"] or "unknown",
            "symbol": r["symbol"] or "",
            "market_type": r["market_type"] or "",
        })
    return result


# ---------------------------------------------------------------------------
# New multi-dimensional analytics
# ---------------------------------------------------------------------------

def _hour_day_heatmap(rows) -> list[dict]:
    """7 days × 24 hours heatmap: avg PnL% per time cell."""
    from collections import defaultdict

    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["opened_at"]).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            cells[(dt.weekday(), dt.hour)].append(_pnl_pct(r))
        except (ValueError, TypeError):
            continue

    result = []
    for day in range(7):
        for hour in range(24):
            pnls = cells.get((day, hour), [])
            tc = len(pnls)
            avg = round(sum(pnls) / tc, 2) if tc else 0.0
            result.append({"day": day, "hour": hour, "avg_pnl_pct": avg, "trade_count": tc})
    return result


def _trade_frequency(rows) -> list[dict]:
    """Per-day trade dots: date + list of individual trade pnl_pct/direction."""
    from collections import defaultdict

    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            dt = datetime.fromisoformat(str(r["closed_at"]).replace("Z", "+00:00"))
            date_str = dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        by_date[date_str].append({
            "pnl_pct": round(_pnl_pct(r), 2),
            "direction": r["direction"] or "unknown",
        })

    # Last 90 days max
    sorted_dates = sorted(by_date.keys())[-90:]
    return [{"date": d, "trades": by_date[d]} for d in sorted_dates]


def _symbol_direction_matrix(rows) -> list[dict]:
    """Symbol × Direction win rate matrix."""
    from collections import defaultdict

    cells: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        sym = r["symbol"] or "unknown"
        direction = r["direction"] or "unknown"
        cells[(sym, direction)].append(r["realized_pnl"] or 0.0)

    result = []
    for (sym, direction), pnls in cells.items():
        tc = len(pnls)
        if tc < 2:
            continue
        wins = sum(1 for p in pnls if p > 0)
        result.append({
            "symbol": sym,
            "direction": direction,
            "win_rate": round(wins / tc * 100, 1),
            "avg_pnl_pct": round(sum(_pnl_pct(r) for r in rows
                                      if (r["symbol"] or "") == sym and (r["direction"] or "") == direction)
                                  / tc, 2),
            "trade_count": tc,
        })
    # Sort by trade_count desc
    result.sort(key=lambda x: x["trade_count"], reverse=True)
    return result[:30]


def _hold_duration_buckets(rows) -> list[dict]:
    """Group trades into hold duration buckets and compute stats."""
    _BUCKETS = [
        ("<1h", 0, 1),
        ("1-4h", 1, 4),
        ("4-12h", 4, 12),
        ("12-24h", 12, 24),
        ("1-3d", 24, 72),
        (">3d", 72, 1e9),
    ]

    from collections import defaultdict
    bucket_data: dict[str, list[float]] = defaultdict(list)

    for r in rows:
        h = _hold_hours(r)
        if h is None:
            continue
        pct = _pnl_pct(r)
        for label, lo, hi in _BUCKETS:
            if lo <= h < hi:
                bucket_data[label].append(pct)
                break

    result = []
    for label, _, _ in _BUCKETS:
        pnls = bucket_data.get(label, [])
        tc = len(pnls)
        if tc == 0:
            result.append({"bucket": label, "trade_count": 0, "win_rate": 0,
                           "avg_pnl_pct": 0, "avg_win_pct": 0, "avg_loss_pct": 0})
            continue
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        result.append({
            "bucket": label,
            "trade_count": tc,
            "win_rate": round(len(wins) / tc * 100, 1),
            "avg_pnl_pct": round(sum(pnls) / tc, 2),
            "avg_win_pct": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss_pct": round(sum(losses) / len(losses), 2) if losses else 0,
        })
    return result


def _r_multiple_distribution(rows) -> dict:
    """R-Multiple distribution (risk-normalized returns).

    #1256: Only uses frozen verified initial risk from the position plan.
    Rows without verified initial risk are counted as unavailable; 2%-of-notional
    proxy is explicitly removed.
    """
    from aletheia.analytics.entry_exit_geometry import _initial_risk as _compute_initial_risk
    from aletheia.storage import repositories as repo
    try:
        from aletheia.storage.sqlite import readonly_db
        _db_context = readonly_db
    except Exception:
        _db_context = None

    _BINS = [
        ("<-3R", -1e9, -3), ("-3R~-2R", -3, -2), ("-2R~-1R", -2, -1),
        ("-1R~0", -1, 0), ("0~+1R", 0, 1), ("+1R~+2R", 1, 2),
        ("+2R~+3R", 2, 3), ("+3R~+5R", 3, 5), (">+5R", 5, 1e9),
    ]
    counts = {label: 0 for label, _, _ in _BINS}
    unavailability_reasons: dict[str, int] = {}
    eligible_r_count = 0
    unavailable_r_count = 0

    def _get_initial_risk_verified(row, db):
        """Try to get verified initial risk from frozen plan evidence."""
        try:
            position_id = str(row.get("id") or row.get("position_id") or "")
            position = (db or repo.get_db()).execute(
                "SELECT * FROM positions WHERE id=?", (position_id,)
            ).fetchone()
            if position is None:
                return None, "position_not_found"
            position = dict(position)
            proposal_id = position.get("proposal_id")
            proposal = None
            if proposal_id:
                p_row = (db or repo.get_db()).execute(
                    "SELECT * FROM trade_proposals WHERE id=?", (proposal_id,)
                ).fetchone()
                if p_row:
                    proposal = dict(p_row)
            plans_rows = (db or repo.get_db()).execute(
                "SELECT * FROM position_exit_plan_versions WHERE position_id=? ORDER BY version ASC",
                (position_id,),
            ).fetchall()
            plans = [dict(r) for r in plans_rows] if plans_rows else None

            # Build a minimal geometry dict (we only need proposal_geometry_status)
            # Use a simple proxy: if proposal fields are present, mark available
            from aletheia.analytics.entry_exit_geometry import _proposal_geometry
            geometry = _proposal_geometry(row if isinstance(row, dict) else dict(row), proposal, position, db=db)
            result = _compute_initial_risk(row if isinstance(row, dict) else dict(row), position, proposal, plans, geometry, db=db)
            if result.get("initial_risk_status") == "available":
                return result.get("initial_risk_amount"), None
            return None, result.get("initial_risk_reason", "unavailable")
        except Exception as exc:
            return None, f"read_error:{type(exc).__name__}"

    db_ctx = None
    try:
        if _db_context is not None:
            db_cm = _db_context()
            db_ctx = db_cm.__enter__()
    except Exception:
        db_ctx = None
        db_cm = None

    try:
        for r in rows:
            pnl = r["realized_pnl"] or 0.0
            initial_risk, reason = _get_initial_risk_verified(r, db_ctx)
            if initial_risk is None or initial_risk <= 0:
                reject_reason = reason or "initial_risk_unavailable"
                unavailability_reasons[reject_reason] = unavailability_reasons.get(reject_reason, 0) + 1
                unavailable_r_count += 1
                continue
            r_mult = pnl / initial_risk
            eligible_r_count += 1
            for label, lo, hi in _BINS:
                if lo <= r_mult < hi:
                    counts[label] += 1
                    break
    finally:
        if db_ctx is not None and db_cm is not None:
            try:
                db_cm.__exit__(None, None, None)
            except Exception:
                pass

    return {
        "bins": [{"bin": label, "count": counts[label]} for label, _, _ in _BINS],
        "eligible_r_multiple_count": eligible_r_count,
        "unavailable_r_multiple_count": unavailable_r_count,
        "unavailability_reasons": unavailability_reasons,
    }



def _reason_of(row) -> str:
    """Extract the bucketing key used by _by_exit_reason.

    #158 fix: the inline ternary this replaces —
      `r["close_reason"] if "close_reason" in r.keys() else None or "unknown"`
    — parses as `r["close_reason"] if ... else (None or "unknown")` because
    `or` binds tighter than the ternary else. So when the column IS present but
    the row VALUE is None (SQLite null), the expression returned None, not
    "unknown"; the outer `if reason_of(r) == "unknown"` then matched nothing,
    the unknown bucket's avg_pnl_pct summed zero rows and divided by a nonzero
    trade_count, and every unknown-bucket avg came back ~0. Pulling it into a
    real function fixes precedence AND makes the intent auditable.
    """
    reason = row["close_reason"] if "close_reason" in row.keys() else None
    return reason or "unknown"


def _by_exit_reason(rows) -> list[dict]:
    """Group trades by close_reason and compute stats."""
    from collections import defaultdict

    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        buckets[_reason_of(r)].append(r["realized_pnl"] or 0.0)

    result = []
    total_pnl_all = sum(r["realized_pnl"] or 0.0 for r in rows)
    for reason, pnls in buckets.items():
        tc = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        total = sum(pnls)
        pct_sum = sum(_pnl_pct(r) for r in rows if _reason_of(r) == reason)
        result.append({
            "reason": reason,
            "trade_count": tc,
            "win_rate": round(wins / tc * 100, 1) if tc else 0,
            "avg_pnl_pct": round(pct_sum / tc, 2) if tc else 0,
            "total_pnl": round(total, 4),
            "pnl_contribution_pct": round(total / total_pnl_all * 100, 1) if total_pnl_all != 0 else 0,
        })
    result.sort(key=lambda x: x["trade_count"], reverse=True)
    return result
