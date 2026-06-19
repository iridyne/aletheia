"""Comprehensive trade analytics for the Insights visualization panel.

Single SQL query over closed positions; all chart data computed in Python.
"""
from __future__ import annotations

from datetime import datetime, timezone


def build_trade_insights() -> dict:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = db.execute(
        "SELECT id, symbol, direction, realized_pnl, entry_price, quantity, "
        "       opened_at, closed_at, regime_tag "
        "FROM positions "
        "WHERE status = 'closed' "
        "ORDER BY closed_at ASC"
    ).fetchall()

    return {
        "equity_curve":     _equity_curve(rows),
        "drawdown_curve":   _drawdown_curve(rows),
        "by_direction":     _by_direction(rows),
        "profit_factor":    _profit_factor(rows),
        "by_day_of_week":   _by_day_of_week(rows),
        "by_hour":          _by_hour(rows),
        "pnl_distribution": _pnl_distribution(rows),
        "by_regime":        _by_regime(rows),
        "streaks":          _streaks(rows),
        "hold_vs_pnl":      _hold_vs_pnl(rows),
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

    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else None
    return {
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "profit_factor": pf,
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
    buckets: dict[str, dict] = {}
    for r in rows:
        regime = r["regime_tag"] or "unknown"
        if regime not in buckets:
            buckets[regime] = {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0}
        b = buckets[regime]
        b["trade_count"] += 1
        if (r["realized_pnl"] or 0.0) > 0:
            b["wins"] += 1
        b["total_pnl_pct"] += _pnl_pct(r)

    result = []
    for regime in ("risk_on", "risk_off", "unknown"):
        b = buckets.get(regime, {"trade_count": 0, "wins": 0, "total_pnl_pct": 0.0})
        tc = b["trade_count"]
        result.append({
            "regime": regime,
            "trade_count": tc,
            "win_rate": round(b["wins"] / tc * 100, 1) if tc else 0.0,
            "avg_pnl_pct": round(b["total_pnl_pct"] / tc, 2) if tc else 0.0,
        })
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
        })
    return result
