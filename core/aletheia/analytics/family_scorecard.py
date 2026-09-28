"""Edge Lab: per-scanner-family performance scorecard.

Aggregates win_rate, expectancy, trade_count, and alpha vs BTC from closed
positions and shadow trades, grouped by scanner_family.

No new table — computed on-demand from existing positions + shadow_trades data.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aletheia.analytics import outcome_integrity
from aletheia.analytics.execution_mode import execution_mode_identity
from aletheia.analytics.holding_time import holding_hours
from aletheia.analytics.learning_window import outcome_window_metadata

logger = logging.getLogger("aletheia.analytics.family_scorecard")
_read_shadow_cohort = None


def configure_shadow_cohort_reader(reader=None) -> None:
    """Bind optional Counterfactual enrichment without owning its cohort policy."""
    global _read_shadow_cohort
    _read_shadow_cohort = reader


def compute_family_scorecard(*, execution_mode: str | None = None) -> list[dict]:
    """Compute per-scanner_family performance statistics.

    Returns a list of dicts, one per family:
        {family, trade_count, win_rate, expectancy_pct, avg_hold_hours,
         shadow_count, alpha_vs_btc_pct}
    Scoped to a single ``execution_mode`` when provided (#1939).
    """
    return compute_family_scorecard_with_epoch(
        execution_mode=execution_mode,
    )["families"]


def compute_family_scorecard_with_epoch(
    *, execution_mode: str | None = None,
) -> dict:
    """Same as compute_family_scorecard() but also returns shadow epoch metadata.

    Returns:
        {
            "families": list[dict],      # per-family stats
            "shadow_epoch_id": str|None, # epoch used for shadow counts
            "shadow_epoch_status": str,  # "single"|"mixed"|"all_null"|"unavailable"
            "shadow_epoch_error": str|None,
            "errors": list[str],
        }
    Shadow counts are set to None for each family when epoch_status is not a
    usable single/legacy cohort to avoid presenting pooled or unavailable data
    as one valid measurement.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    families: dict[str, dict] = {}

    from datetime import datetime, timezone
    from aletheia.analytics.candidate_lifecycle import for_proposal

    # Metadata joins do not decide eligibility; the central read model does.
    rows = []
    for position in outcome_integrity.eligible_strategy_outcomes(
        execution_mode=execution_mode,
    ):
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
            elif proposal and not proposal["opportunity_id"]:
                # Universe branch: resolve scanner_family via lineage reader
                cutoff = datetime.now(timezone.utc)
                lineage = for_proposal(str(proposal_id), cutoff, db)
                if lineage.universe_member and lineage.universe_member.get("scanner_family"):
                    scanner_family = lineage.universe_member["scanner_family"]
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
        hours = holding_hours(r.get("opened_at"), r.get("closed_at"))
        if hours is not None:
            f["total_hold_hours"] += hours
            f["hold_time_available_count"] += 1

    # Count shadow candidates per family, deduped by outcome_candidate_key.
    # Epoch guard: only count shadows within a single policy epoch to avoid
    # pooling non-exchangeable cohorts (CF-I04).
    shadow_epoch_id, shadow_epoch_status = None, "unavailable"
    shadow_epoch_error = "counterfactual_extension_unavailable"
    shadow_rows = []
    if _read_shadow_cohort is not None:
        shadow_epoch_id, shadow_epoch_status, shadow_epoch_error, shadow_rows = _read_shadow_cohort()
    if shadow_epoch_status in {"single", "all_null"}:
        for sr in shadow_rows:
            family = sr.get("scanner_family") or "unknown"
            if family not in families:
                families[family] = _empty_family(family)
            families[family]["shadow_count"] += 1

    # Compute final metrics
    result = []
    for family, f in sorted(families.items()):
        tc = f["trade_count"]
        result.append({
            "family": family,
            "trade_count": tc,
            "win_rate": round(f["wins"] / tc * 100.0, 1) if tc > 0 else None,
            "expectancy_pct": round(f["total_pnl_pct"] / tc, 2) if tc > 0 else None,
            "avg_hold_hours": (
                round(f["total_hold_hours"] / f["hold_time_available_count"], 1)
                if f["hold_time_available_count"] > 0 else None
            ),
            "hold_time_available_count": f["hold_time_available_count"],
            "hold_time_unavailable_count": tc - f["hold_time_available_count"],
            "shadow_count": f["shadow_count"] if shadow_epoch_status in {"single", "all_null"} else None,
            "alpha_vs_btc_pct": round(f["total_alpha_pct"] / f["alpha_count"], 2) if f["alpha_count"] > 0 else None,
            "outcome_status": "available" if tc else "insufficient",
            **execution_mode_identity(execution_mode),
            **outcome_window_metadata(),
        })
    return {
        "families": result,
        "shadow_epoch_id": shadow_epoch_id,
        "shadow_epoch_status": shadow_epoch_status,
        "shadow_epoch_error": shadow_epoch_error,
        "errors": [shadow_epoch_error] if shadow_epoch_error else [],
        **execution_mode_identity(execution_mode),
    }


def _empty_family(name: str) -> dict:
    return {
        "family": name,
        "trade_count": 0,
        "wins": 0,
        "total_pnl_pct": 0.0,
        "total_alpha_pct": 0.0,
        "alpha_count": 0,
        "total_hold_hours": 0.0,
        "hold_time_available_count": 0,
        "shadow_count": 0,
    }


def parse_utc_strict(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except (ValueError, TypeError):
        return None
