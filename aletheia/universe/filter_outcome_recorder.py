"""Fixed-horizon settlement for filter pass/reject counterfactuals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time

from aletheia.storage import repositories as repo


logger = logging.getLogger("aletheia.universe.filter_outcome_recorder")


async def settle_due_filter_outcomes(
    max_rows: int = 500,
    time_budget_sec: float = 30.0,
) -> int:
    """Settle due filter outcomes using the Stage-D instrument and cost path."""
    from aletheia.config.loader import load_config
    from aletheia.universe.outcome_economics import estimate_outcome_economics
    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations, _parse_dt

    deadline = time.monotonic() + max(0.0, time_budget_sec)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cfg = load_config()
    processed = 0
    settled = 0

    while processed < max_rows and time.monotonic() < deadline:
        batch_limit = min(32, max_rows - processed)
        pending = repo.list_pending_filter_candidate_outcomes(now_iso, limit=batch_limit)
        if not pending:
            break
        observations = await _batch_fetch_market_observations(pending, now=now)
        for row in pending:
            observation = observations.get(row["id"])
            price = observation.get("exit_price") if observation else None
            if price is None or float(price) <= 0:
                repo.fail_filter_candidate_outcome(row["id"], settled_at=now_iso)
                processed += 1
                continue

            entry = float(row["entry_price_usd"])
            exit_price = float(price)
            ratio = exit_price / entry
            if ratio > 10.0 or ratio < 0.1:
                repo.fail_filter_candidate_outcome(row["id"], settled_at=now_iso)
                processed += 1
                continue

            entry_dt = _parse_dt(row.get("entry_ts"))
            due_dt = _parse_dt(row.get("due_at"))
            hold_seconds = (
                max(0.0, (due_dt - entry_dt).total_seconds())
                if entry_dt and due_dt
                else _horizon_seconds(row.get("horizon"))
            )
            economics = estimate_outcome_economics(
                entry_price=entry,
                exit_price=exit_price,
                direction=row.get("direction"),
                market_type=row.get("market_type") or "spot",
                hold_seconds=hold_seconds,
                entry_spread_bps=row.get("entry_spread_bps"),
                fallback_spread_bps=cfg.universe.max_spread_bps,
                funding_rate_at_entry=row.get("funding_rate_at_entry"),
                execution_fee_pct=cfg.trading.execution_fee_pct,
                slippage_bps=cfg.trading.slippage_bps,
                range_high=observation.get("range_high"),
                range_low=observation.get("range_low"),
                price_quality_ok=bool(observation.get("price_quality_ok")),
            )
            repo.settle_filter_candidate_outcome(
                row["id"],
                exit_price=exit_price,
                settled_at=now_iso,
                economics=economics,
                settlement_price_source=observation.get("price_source"),
                settlement_lag_sec=observation.get("settlement_lag_sec"),
            )
            if row.get("horizon") == "24h":
                repo.settle_linked_shadow_outcomes(
                    row["outcome_candidate_key"],
                    exit_price=exit_price,
                    settled_at=now_iso,
                    economics=economics,
                    settlement_price_source=observation.get("price_source"),
                    settlement_lag_sec=observation.get("settlement_lag_sec"),
                )
            processed += 1
            settled += 1

    if settled:
        logger.info(
            "filter outcome settler: settled %d (%d processed)",
            settled,
            processed,
        )
    return settled


def _horizon_seconds(horizon: str | None) -> float:
    values = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "24h": timedelta(hours=24),
    }
    return values.get(str(horizon), timedelta()).total_seconds()
