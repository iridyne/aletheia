"""Edge Lab: per-scanner-family performance scorecard.

Aggregates win_rate, expectancy, trade_count, and alpha vs BTC from closed
positions and shadow trades, grouped by scanner_family.

No new table — computed on-demand from existing positions + shadow_trades data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import (
    OUTCOME_SEMANTICS_VERSION,
    eligible_strategy_outcomes,
)

logger = logging.getLogger("aletheia.analytics.family_scorecard")


def compute_family_scorecard() -> list[dict]:
    """Compute per-scanner_family performance statistics.

    Returns a list of dicts, one per family:
        {family, trade_count, win_rate, expectancy_pct, avg_hold_hours,
         shadow_count, alpha_vs_btc_pct}
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    families: dict[str, dict] = {}

    # Metadata joins do not decide eligibility; the central read model does.
    rows = []
    for position in eligible_strategy_outcomes():
        scanner_family = "unknown"
        proposal_id = position.get("proposal_id")
        if proposal_id:
            proposal = db.execute(
                "SELECT opportunity_id FROM trade_proposals WHERE id = ?", (proposal_id,),
            ).fetchone()
            if proposal and proposal["opportunity_id"]:
                opportunity = db.execute(
                    "SELECT scanner_family FROM opportunities WHERE id = ?",
                    (proposal["opportunity_id"],),
                ).fetchone()
                if opportunity and opportunity["scanner_family"]:
                    scanner_family = opportunity["scanner_family"]
        rows.append({**position, "scanner_family": scanner_family})

    for r in rows:
        family = r.get("scanner_family") or "unknown"
        if family not in families:
            families[family] = _empty_family(family)
        f = families[family]
        f["trade_count"] += 1

        pnl = r.get("realized_pnl") or 0.0
        notional = (r.get("entry_price") or 1.0) * (r.get("quantity") or 1.0)
        pnl_pct = (pnl / notional * 100.0) if notional > 0 else 0.0

        if pnl > 0:
            f["wins"] += 1
        f["total_pnl_pct"] += pnl_pct

        # Alpha vs BTC
        btc_entry = r.get("btc_entry_price")
        btc_exit = r.get("btc_exit_price")
        if btc_entry and btc_exit and btc_entry > 0:
            btc_return = (btc_exit - btc_entry) / btc_entry * 100.0
            alpha = pnl_pct - btc_return
            f["total_alpha_pct"] += alpha
            f["alpha_count"] += 1

        # Hold time
        opened = _parse_iso(r.get("opened_at"))
        closed = _parse_iso(r.get("closed_at"))
        if opened and closed:
            hours = (closed - opened).total_seconds() / 3600.0
            f["total_hold_hours"] += hours

    # Count shadow trades per family
    shadow_rows = db.execute(
        "SELECT scanner_family, count(*) as cnt "
        "FROM shadow_trades WHERE scanner_family IS NOT NULL "
        "GROUP BY scanner_family"
    ).fetchall()
    for sr in shadow_rows:
        family = sr["scanner_family"] or "unknown"
        if family not in families:
            families[family] = _empty_family(family)
        families[family]["shadow_count"] = sr["cnt"]

    # Compute final metrics
    result = []
    for family, f in sorted(families.items()):
        tc = f["trade_count"]
        result.append({
            "family": family,
            "trade_count": tc,
            "win_rate": round(f["wins"] / tc * 100.0, 1) if tc > 0 else None,
            "expectancy_pct": round(f["total_pnl_pct"] / tc, 2) if tc > 0 else None,
            "avg_hold_hours": round(f["total_hold_hours"] / tc, 1) if tc > 0 else None,
            "shadow_count": f["shadow_count"],
            "alpha_vs_btc_pct": round(f["total_alpha_pct"] / f["alpha_count"], 2) if f["alpha_count"] > 0 else None,
            "outcome_status": "available" if tc else "insufficient",
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
        })
    return result


def _empty_family(name: str) -> dict:
    return {
        "family": name,
        "trade_count": 0,
        "wins": 0,
        "total_pnl_pct": 0.0,
        "total_alpha_pct": 0.0,
        "alpha_count": 0,
        "total_hold_hours": 0.0,
        "shadow_count": 0,
    }


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None
