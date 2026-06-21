"""Trade analytics: aggregated stats by source type, scanner family, and hold-time distribution."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def compute_rolling_trade_stats(days: int = 7) -> dict:
    """Compute windowed trade stats for the last N days.

    Returns: {total_trades, wins, losses, win_rate, total_pnl, avg_pnl,
              best_win, worst_loss, per_symbol: [{symbol, trades, wins, pnl}]}
    """
    from aletheia.storage.repositories import list_positions

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    closed = list_positions(status="closed", limit=500)
    recent = [p for p in closed if (p.get("closed_at") or "") >= cutoff]

    if not recent:
        return {
            "days": days, "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "total_pnl": 0.0, "avg_pnl": 0.0,
            "best_win": 0.0, "worst_loss": 0.0, "per_symbol": [],
        }

    wins = [p for p in recent if (p.get("realized_pnl") or 0) > 0]
    total_pnl = sum(p.get("realized_pnl", 0) for p in recent)
    pnls = [p.get("realized_pnl", 0) for p in recent]

    # Per-symbol breakdown (top 8)
    from collections import Counter
    sym_counts = Counter(p.get("symbol") for p in recent)
    per_symbol = []
    for sym, _ in sym_counts.most_common(8):
        sym_trades = [p for p in recent if p.get("symbol") == sym]
        sym_wins = [p for p in sym_trades if (p.get("realized_pnl") or 0) > 0]
        sym_pnl = sum(p.get("realized_pnl", 0) for p in sym_trades)
        per_symbol.append({
            "symbol": sym, "trades": len(sym_trades),
            "wins": len(sym_wins), "pnl": round(sym_pnl, 2),
        })

    return {
        "days": days,
        "total_trades": len(recent),
        "wins": len(wins),
        "losses": len(recent) - len(wins),
        "win_rate": round(len(wins) / len(recent) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(recent), 2),
        "best_win": round(max(pnls), 2),
        "worst_loss": round(min(pnls), 2),
        "per_symbol": per_symbol,
    }


def compute_order_stats(days: int = 7) -> dict:
    """Compute order fill rate and error breakdown for the last N days.

    Returns: {total, filled, errors, fill_rate_pct, error_breakdown: [{pattern, count, symbols}]}
    """
    from aletheia.storage.sqlite import get_db

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    rows = db.execute(
        "SELECT symbol, status, error_reason FROM orders WHERE created_at > ?",
        (cutoff,),
    ).fetchall()

    filled = sum(1 for r in rows if r["status"] == "filled")
    errored = [r for r in rows if r["status"] == "error"]
    total = filled + len(errored)  # fill rate = filled / (filled + errors)

    # Group errors by pattern — count occurrences + track affected symbols
    import re
    error_groups: dict[str, dict] = {}
    for r in errored:
        reason = r["error_reason"] or "unknown"
        pattern = re.sub(r"Parameter \w+ does not exist", "Parameter XXXUSDT does not exist", reason)
        pattern = re.sub(r"symbol=\w+", "symbol=XXX", pattern)
        if pattern not in error_groups:
            error_groups[pattern] = {"count": 0, "symbols": []}
        error_groups[pattern]["count"] += 1
        sym = r["symbol"] or "?"
        if sym not in error_groups[pattern]["symbols"]:
            error_groups[pattern]["symbols"].append(sym)

    breakdown = sorted(
        [{"pattern": p, "count": g["count"], "symbols": g["symbols"][:5]} for p, g in error_groups.items()],
        key=lambda x: x["count"], reverse=True,
    )

    return {
        "days": days,
        "total": total,
        "filled": filled,
        "errors": len(errored),
        "fill_rate_pct": round(filled / total * 100, 1) if total else 0.0,
        "error_breakdown": breakdown[:5],
    }


def build_trade_stats() -> dict:
    """Compute closed-position stats grouped by strategy source and scanner family."""
    from aletheia.storage.sqlite import get_db
    from aletheia.analytics.family_scorecard import compute_family_scorecard

    db = get_db()

    rows = db.execute(
        "SELECT "
        "  CASE WHEN json_extract(s.parameters, '$.ruleset') IS NOT NULL "
        "       THEN 'ruleset' ELSE 'opportunity' END AS source_type, "
        "  p.realized_pnl, p.entry_price, p.quantity, "
        "  p.opened_at, p.closed_at "
        "FROM positions p "
        "LEFT JOIN strategy_records s ON s.id = p.strategy_id "
        "WHERE p.status = 'closed'"
    ).fetchall()

    source_agg: dict[str, dict] = {}
    all_hold_hours: list[float] = []
    total_wins = 0
    total_pnl = 0.0

    for r in rows:
        src = r["source_type"] or "opportunity"
        if src not in source_agg:
            source_agg[src] = {
                "trade_count": 0, "wins": 0,
                "total_pnl": 0.0, "total_pnl_pct": 0.0, "total_hold": 0.0,
            }
        a = source_agg[src]
        a["trade_count"] += 1

        pnl = r["realized_pnl"] or 0.0
        notional = (r["entry_price"] or 1.0) * (r["quantity"] or 1.0)
        pnl_pct = pnl / notional * 100.0 if notional > 0 else 0.0
        total_pnl += pnl
        if pnl > 0:
            a["wins"] += 1
            total_wins += 1

        a["total_pnl"] += pnl
        a["total_pnl_pct"] += pnl_pct

        hours = _hold_hours(r["opened_at"], r["closed_at"])
        if hours is not None:
            a["total_hold"] += hours
            all_hold_hours.append(hours)

    by_source = []
    for src in ("ruleset", "opportunity"):
        a = source_agg.get(src, {"trade_count": 0, "wins": 0, "total_pnl": 0.0, "total_pnl_pct": 0.0, "total_hold": 0.0})
        tc = a["trade_count"]
        by_source.append({
            "source_type": src,
            "trade_count": tc,
            "win_rate": round(a["wins"] / tc * 100, 1) if tc else 0.0,
            "avg_hold_hours": round(a["total_hold"] / tc, 1) if tc else 0.0,
            "expectancy_pct": round(a["total_pnl_pct"] / tc, 2) if tc else 0.0,
            "total_pnl": round(a["total_pnl"], 4),
        })

    by_family = compute_family_scorecard()

    hold_distribution = _bucket_hold_times(all_hold_hours)
    total_count = len(rows)
    sorted_hold = sorted(all_hold_hours)

    totals = {
        "trade_count": total_count,
        "win_rate": round(total_wins / total_count * 100, 1) if total_count else 0.0,
        "avg_hold_hours": round(sum(all_hold_hours) / len(all_hold_hours), 1) if all_hold_hours else 0.0,
        "median_hold_hours": round(sorted_hold[len(sorted_hold) // 2], 2) if sorted_hold else 0.0,
        "total_pnl": round(total_pnl, 4),
    }

    return {
        "by_source": by_source,
        "by_family": by_family,
        "hold_distribution": hold_distribution,
        "totals": totals,
    }


def _hold_hours(opened_at, closed_at) -> float | None:
    if not opened_at or not closed_at:
        return None
    try:
        o = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        c = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        return max(0.0, (c - o).total_seconds() / 3600.0)
    except (ValueError, TypeError):
        return None


_BUCKETS = [
    ("<1h", 0, 1),
    ("1-4h", 1, 4),
    ("4-12h", 4, 12),
    ("12-24h", 12, 24),
    ("1-3d", 24, 72),
    (">3d", 72, 1e9),
]


def _bucket_hold_times(hours_list: list[float]) -> list[dict]:
    total = len(hours_list)
    result = []
    for label, lo, hi in _BUCKETS:
        count = sum(1 for h in hours_list if lo <= h < hi)
        result.append({
            "bucket": label,
            "count": count,
            "pct": round(count / total * 100, 1) if total else 0.0,
        })
    return result


