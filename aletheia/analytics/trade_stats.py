"""Trade analytics: aggregated stats by source type, scanner family, and hold-time distribution."""

from __future__ import annotations

from datetime import datetime, timezone


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
