"""Forward settlement for observation-only Position X aggregate snapshots."""

from __future__ import annotations

from datetime import datetime, timezone

from aletheia.storage import repositories as repo


async def settle_due_perp_structure_outcomes(limit: int = 100) -> int:
    """Settle due structure rows on their exact venue/instrument OHLCV path."""
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    rows = repo.list_due_perp_structure_shadow_outcomes(now_iso, limit=limit)
    if not rows:
        return 0
    observations = await _batch_fetch_market_observations(rows, now=now)
    settled = 0
    for row in rows:
        observation = observations.get(row["id"])
        if is_deferred(observation):
            # The venue was never reached, so this row was never measured. It stays
            # due and is asked about again next cycle; failing it here would write
            # off a measurable observation permanently.
            continue
        exit_price = observation.get("exit_price") if observation else None
        entry_price = float(row["entry_price_usd"])
        if exit_price is None or exit_price <= 0 or entry_price <= 0:
            repo.fail_perp_structure_shadow_outcome(row["id"], now_iso)
            continue
        ratio = float(exit_price) / entry_price
        if ratio > 10.0 or ratio < 0.1:
            # A real verdict on a real observation: record what was seen and
            # rejected, so this row is not mistaken for one that was never
            # measured (see repo.fail_perp_structure_shadow_outcome).
            repo.fail_perp_structure_shadow_outcome(
                row["id"],
                now_iso,
                exit_price=float(exit_price),
                settlement_price_source=observation.get("price_source"),
            )
            continue
        raw_return = (float(exit_price) - entry_price) / entry_price * 100.0
        directional_return = raw_return if row["direction"] == "long" else -raw_return
        repo.settle_perp_structure_shadow_outcome(
            row["id"],
            exit_price=float(exit_price),
            directional_return_pct=directional_return,
            settled_at=now_iso,
            price_source=observation.get("price_source"),
            settlement_lag_sec=observation.get("settlement_lag_sec"),
        )
        settled += 1
    return settled


def shadow_window_status(*, now: datetime | None = None) -> dict:
    """Report whether a non-influential 14-day cohort is mature enough to review."""
    from aletheia.storage.sqlite import get_db

    current = now or datetime.now(timezone.utc)
    row = get_db().execute(
        "SELECT MIN(entry_ts) AS started_at, COUNT(*) AS total, "
        "SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END) AS settled "
        "FROM perp_structure_shadow_outcomes"
    ).fetchone()
    started_at = row["started_at"] if row else None
    age_days = 0.0
    if started_at:
        parsed = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (current - parsed).total_seconds() / 86400.0)
    return {
        "started_at": started_at,
        "age_days": round(age_days, 2),
        "minimum_days": 14,
        "total": int(row["total"] or 0) if row else 0,
        "settled": int(row["settled"] or 0) if row else 0,
        "mature_for_review": bool(started_at and age_days >= 14),
        "decision_influence": "disabled",
    }
