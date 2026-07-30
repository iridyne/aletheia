"""Fixed-horizon settlement for filter pass/reject counterfactuals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time

from aletheia.storage import repositories as repo


logger = logging.getLogger("aletheia.universe.filter_outcome_recorder")

# Share of the cycle budget the priority-ordering prologue may consume. Selecting
# WHICH due rows to settle first is an optimisation; settling rows is the job. A
# slow prologue must degrade the order, never the throughput.
_PROLOGUE_BUDGET_FRACTION = 0.30


async def settle_due_filter_outcomes(
    max_rows: int = 500,
    time_budget_sec: float = 30.0,
) -> int:
    """Settle due filter outcomes using the Stage-D instrument and cost path."""
    from aletheia.config.loader import load_config
    from aletheia.universe.outcome_economics import estimate_outcome_economics
    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations, _parse_dt

    if max_rows <= 0:
        return 0

    deadline = time.monotonic() + max(0.0, time_budget_sec)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    cfg = load_config()
    processed = 0
    settled = 0
    # The current UFC observation window must not sit behind the historical
    # canonical backlog: its exact outcomes are the evidence required by LCO.
    # This only changes settlement order inside the already-bounded canonical
    # quota; it does not change candidate selection, economics, or authority.
    #
    # The prologue is time-boxed because it once wasn't: expanding ~34k
    # open-experiment keys grew past the ENTIRE cycle budget, so the loop below
    # never ran a single iteration. The function returned 0 without raising, and
    # the old log line only fired when settled > 0 — so canonical settlement
    # stopped for hours while the forward mirror kept draining and every probe
    # stayed quiet.
    from aletheia.agent_runtime.build_info import get_boot_commit

    prologue_started = time.monotonic()
    prologue_deadline = min(
        deadline,
        prologue_started + max(0.0, time_budget_sec) * _PROLOGUE_BUDGET_FRACTION,
    )
    current_code_commit = get_boot_commit()
    current_build_keys = repo.list_open_learning_outcome_candidate_keys(
        ("universe_family_calibration",),
        code_commit=current_code_commit,
    ) if current_code_commit else set()
    priority_pending, priority_truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
        now_iso,
        current_build_keys,
        limit=max_rows,
        deadline=prologue_deadline,
    )
    all_priority_keys = repo.list_open_learning_outcome_candidate_keys(
        ("universe_family_calibration",),
    )
    older_build_keys = all_priority_keys - current_build_keys
    remaining_priority_rows = max(0, max_rows - len(priority_pending))
    if remaining_priority_rows and older_build_keys:
        older_rows, older_truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
            now_iso,
            older_build_keys,
            limit=remaining_priority_rows,
            deadline=prologue_deadline,
        )
        priority_pending.extend(older_rows)
        priority_truncated = priority_truncated or older_truncated
    prologue_sec = time.monotonic() - prologue_started
    if priority_truncated:
        logger.warning(
            "filter outcome settler: priority key expansion hit its %.1fs slice "
            "(%.1fs used, %d open-experiment keys, %d rows selected) — settling in "
            "due_at order for the rest of this cycle",
            max(0.0, prologue_deadline - prologue_started),
            prologue_sec,
            len(all_priority_keys),
            len(priority_pending),
        )
    priority_offset = 0
    nothing_due = False

    while processed < max_rows and time.monotonic() < deadline:
        batch_limit = min(32, max_rows - processed)
        pending = priority_pending[priority_offset:priority_offset + batch_limit]
        priority_offset += len(pending)
        if not pending:
            pending = repo.list_pending_filter_candidate_outcomes(
                now_iso,
                limit=batch_limit,
            )
        if not pending:
            nothing_due = True
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

    if settled or processed:
        logger.info(
            "filter outcome settler: settled %d (%d processed, prologue %.1fs)",
            settled,
            processed,
            prologue_sec,
        )
    elif nothing_due:
        logger.debug("filter outcome settler: nothing due")
    else:
        # A zero-throughput cycle that still had work to do must be audible. The
        # absence of this line is what let a multi-hour full stop look identical
        # to a healthy caught-up queue.
        logger.warning(
            "filter outcome settler: settled 0 with work outstanding — prologue "
            "%.1fs of a %.0fs budget, %d priority rows selected, budget_exhausted=%s; "
            "the canonical queue is not draining",
            prologue_sec,
            time_budget_sec,
            len(priority_pending),
            time.monotonic() >= deadline,
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
