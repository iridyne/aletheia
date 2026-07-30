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

# Rows handed to one concurrent exchange wave. Bounds how long a single wave can
# run when requests hit _FETCH_TIMEOUT_SEC, and is the granularity at which the
# deadline and the fetch ceiling are re-checked.
_NETWORK_WAVE_ROWS = 32

# What one row's observation resolved to. The first two are terminal writes; the
# third writes nothing at all, because the venue was never reached and so nothing
# about the row was measured (see outcome_recorder.deferred_observation).
_SETTLED = "settled"
_FAILED = "failed"
_DEFERRED = "deferred"


async def settle_due_filter_outcomes(
    max_rows: int = 500,
    time_budget_sec: float = 30.0,
    max_fetches: int | None = None,
) -> int:
    """Settle due filter outcomes using the Stage-D instrument and cost path.

    Runs in phases so that the cheap resource and the scarce one are bounded
    separately. Phase A settles everything the already-persisted ``market_ohlcv``
    window can answer and touches no exchange at all. Phase B spends up to
    ``max_fetches`` requests on what is left, one per instrument rather than one
    per row, harvests the sibling rows those responses just persisted, and repeats
    on whatever the harvest did not answer until the budget runs out. Before the
    split, one network round trip per row made the row cap and the exchange load
    the same number, so the only way to raise throughput was to raise load.

    Args:
        max_rows: rows to consider this cycle. Bounds local work only; a row this
            cap excludes stays ``pending`` for the next cycle.
        time_budget_sec: wall-clock ceiling for all phases combined.
        max_fetches: ceiling on exchange requests. ``None`` means "as many as the
            row and time budgets allow", which is the pre-existing behaviour.
            Rows past the ceiling stay ``pending`` — they are never marked
            ``failed``, because budget exhaustion is not a measurement failure.
            Nor is an unreachable venue: a row the exchange refused to answer
            about also stays ``pending``, since nothing about it was measured.
    """
    from aletheia.config.loader import load_config
    from aletheia.universe.outcome_economics import estimate_outcome_economics
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        _parse_dt,
        fetch_group_key,
        is_deferred,
        local_market_observations,
    )

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
    from aletheia.learning.behavior_contracts import current_behavior_contract_hash
    from aletheia.learning.experiments import current_policy_epoch
    from aletheia.learning.verdicts import METRIC_VERSION

    prologue_started = time.monotonic()
    prologue_deadline = min(
        deadline,
        prologue_started + max(0.0, time_budget_sec) * _PROLOGUE_BUDGET_FRACTION,
    )
    policy_epoch = current_policy_epoch("universe_family_calibration")
    current_behavior_contract_hash = (
        current_behavior_contract_hash(
            path_id="universe_family_calibration",
            policy_epoch=policy_epoch,
            target_horizon="4h",
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        )
        if policy_epoch
        else None
    )
    current_behavior_keys = repo.list_open_learning_outcome_candidate_keys(
        ("universe_family_calibration",),
        behavior_contract_hash=current_behavior_contract_hash,
    ) if current_behavior_contract_hash else set()
    priority_pending, priority_truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
        now_iso,
        current_behavior_keys,
        limit=max_rows,
        deadline=prologue_deadline,
    )
    all_priority_keys = repo.list_open_learning_outcome_candidate_keys(
        ("universe_family_calibration",),
    )
    older_behavior_keys = all_priority_keys - current_behavior_keys
    remaining_priority_rows = max(0, max_rows - len(priority_pending))
    if remaining_priority_rows and older_behavior_keys:
        older_rows, older_truncated = repo.list_pending_filter_candidate_outcomes_for_keys(
            now_iso,
            older_behavior_keys,
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
    # One candidate slice for the whole cycle, priority rows first and then plain
    # due_at order. Selected once rather than re-queried per batch: the old
    # per-batch SELECT always returned the same oldest rows, so "what this cycle
    # is working on" was unobservable, and the two phases below need to agree on
    # one population. Dedup is required because a priority key's row is also an
    # ordinary overdue row and would otherwise be settled (and counted) twice.
    seen_ids: set[str] = set()
    candidates: list[dict] = []
    for row in priority_pending:
        if row["id"] in seen_ids:
            continue
        seen_ids.add(row["id"])
        candidates.append(row)
    if len(candidates) < max_rows:
        for row in repo.list_pending_filter_candidate_outcomes(
            now_iso,
            limit=max_rows - len(candidates),
        ):
            if row["id"] in seen_ids:
                continue
            seen_ids.add(row["id"])
            candidates.append(row)
    nothing_due = not candidates

    def apply(row: dict, observation: dict | None) -> str:
        """Resolve one row. Returns _SETTLED, _FAILED, or _DEFERRED.

        Extracted verbatim from the old single loop so the local and exchange
        phases cannot drift apart in economics, guards, or shadow linkage.

        It used to return a bool, which forced the two ways of not settling into
        one answer and made the terminal write unconditional. A deferral must
        write nothing: the venue was never reached, the row's bars are still
        there, and ``failed`` is a state no job revisits.
        """
        if is_deferred(observation):
            return _DEFERRED
        price = observation.get("exit_price") if observation else None
        if price is None or float(price) <= 0:
            repo.fail_filter_candidate_outcome(row["id"], settled_at=now_iso)
            return _FAILED

        entry = float(row["entry_price_usd"])
        exit_price = float(price)
        ratio = exit_price / entry
        if ratio > 10.0 or ratio < 0.1:
            # A real verdict on a real observation: record what was seen and
            # rejected, so this row is not mistaken for one that was never
            # measured at all (see repo.fail_filter_candidate_outcome).
            repo.fail_filter_candidate_outcome(
                row["id"],
                settled_at=now_iso,
                exit_price=exit_price,
                settlement_price_source=observation.get("price_source"),
            )
            return _FAILED

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
        return _SETTLED

    # ---- phase A: everything the persisted window already answers ------------
    # No exchange resource is held here at all, so this is bounded only by rows
    # and time. Writes stay one autocommit statement per row rather than a batched
    # transaction: batching measured ~28x cheaper per row, but a raise on row 20
    # of a 32-row transaction would roll back the 19 rows that had already
    # settled, and losing settled evidence to buy latency is the wrong trade.
    local_obs, network_rows, local_reasons = local_market_observations(
        candidates,
        now=now,
        default_venue=cfg.market_data.ccxt_exchange,
    )
    local_processed = 0
    for row in candidates:
        if time.monotonic() >= deadline:
            break
        observation = local_obs.get(row["id"])
        if observation is None:
            continue
        processed += 1
        local_processed += 1
        if apply(row, observation) == _SETTLED:
            settled += 1

    # ---- phase B: the exchange, one request per instrument, not per row -------
    # The ceiling is on exchange requests, because requests are the resource that
    # is actually shared — ccxt throttles per client and the venue throttles per
    # IP, and a dozen other scheduler jobs draw on the same budget. So the budget
    # is spent on GROUPS: rows that would ask the same endpoint for the same series
    # are collapsed, and only the oldest member of each group is fetched.
    #
    # That is where the throughput comes from without any extra load. get_ohlcv
    # persists every bar it returns, and _FETCH_PLAN asks for far more bars than
    # one window needs, so the representative's response also covers every later
    # member of its group whose window falls inside the span it brought back. Those
    # members are then harvested from disk below at no request cost. Measured on a
    # clone of the live backlog: a 6,000-row cycle's 4,432 misses collapse into 363
    # groups, ~12 rows per request against 1.0 for a per-row fetch, with every
    # sibling in the sampled slice falling inside its representative's span.
    #
    # A group is fetched by ONE representative rather than by a whole wave on the
    # same instrument: concurrent siblings would each miss the not-yet-persisted
    # window and duplicate the request, which is the opposite of the intent.
    #
    # Grouping runs in ROUNDS, and that is the part that must not be simplified
    # away. Collapsing a group defers its siblings on the bet that the
    # representative's span covers them; the harvest below cashes that bet in.
    # When it does not pay — the sibling's window fell outside the span — the
    # sibling must be fetched on its own, because grouping is an amortisation of a
    # SCARCE request budget, never a reason to leave an affordable row unmeasured.
    # A single-pass collapse silently starved those rows: three due rows on one
    # instrument settled one per cycle even with an unlimited budget. So each round
    # regroups whatever is still unanswered and spends what is left of the
    # allowance on it. Rounds terminate because only a group's siblings are ever
    # carried into the next round: its representative is dropped either way, so
    # each round removes at least one row per group even when the representative
    # got no write at all because the venue was unreachable.
    default_venue = cfg.market_data.ccxt_exchange
    unanswered = list(network_rows)
    fetch_allowance = (
        len(unanswered) if max_fetches is None else max(0, int(max_fetches))
    )
    fetched_groups = 0
    harvested = 0
    rounds = 0
    deferred = 0
    venue_unreachable = False
    while unanswered and fetched_groups < fetch_allowance:
        if time.monotonic() >= deadline:
            break
        rounds += 1
        groups: dict[str, list[dict]] = {}
        ungroupable: list[dict] = []
        for row in unanswered:
            key = fetch_group_key(row, now=now, default_venue=default_venue)
            if key is None:
                # Unplannable. Leave it to the per-row path, which reports it.
                ungroupable.append(row)
                continue
            groups.setdefault(key, []).append(row)

        # Dict order is insertion order, so groups stay in the due order of their
        # oldest member: the budget goes to the most overdue evidence first.
        representatives = [members[0] for members in groups.values()] + ungroupable
        budgeted = representatives[: fetch_allowance - fetched_groups]
        if not budgeted:
            break
        attempted: set[str] = set()
        for start in range(0, len(budgeted), _NETWORK_WAVE_ROWS):
            if time.monotonic() >= deadline:
                break
            wave = budgeted[start:start + _NETWORK_WAVE_ROWS]
            observations = await _batch_fetch_market_observations(wave, now=now)
            fetched_groups += len(wave)
            wave_deferred = 0
            for row in wave:
                attempted.add(row["id"])
                verdict = apply(row, observations.get(row["id"]))
                if verdict == _DEFERRED:
                    # Not processed: nothing was measured and nothing was written.
                    deferred += 1
                    wave_deferred += 1
                    continue
                processed += 1
                if verdict == _SETTLED:
                    settled += 1
            if wave_deferred == len(wave):
                # A whole wave reached nothing, so the venue is down rather than
                # this wave being unlucky. Every later wave and round would regroup
                # more rows and spend more of the allowance against an endpoint
                # that is not answering — and when the cause is a rate-limit ban,
                # that spending is what extends the ban. Stop for this cycle; the
                # rows are all still measurable when it answers again.
                venue_unreachable = True
                break

        # ---- harvest: siblings the requests above just warmed ----------------
        # No request is issued here; this only reads what the fetches persisted.
        siblings = [
            row
            for members in groups.values()
            for row in members[1:]
            if row["id"] not in attempted
        ]
        still_unanswered: list[dict] = []
        if siblings and time.monotonic() < deadline:
            harvest_obs, _missing, _harvest_reasons = local_market_observations(
                siblings, now=now, default_venue=default_venue,
            )
            for row in siblings:
                observation = harvest_obs.get(row["id"])
                if observation is None:
                    still_unanswered.append(row)
                    continue
                processed += 1
                harvested += 1
                if apply(row, observation) == _SETTLED:
                    settled += 1
        else:
            still_unanswered = siblings
        # Rows the deadline cut out of `budgeted` keep their 'pending' status and
        # are simply left to the next cycle, so they are not carried forward here.
        unanswered = still_unanswered
        if venue_unreachable:
            break

    if settled or processed:
        logger.info(
            "filter outcome settler: settled %d of %d processed "
            "(local %d, fetched %d in %d rounds, harvested %d, deferred %d) "
            "from %d candidates, prologue %.1fs, local window %s",
            settled,
            processed,
            local_processed,
            fetched_groups,
            rounds,
            harvested,
            deferred,
            len(candidates),
            prologue_sec,
            " ".join(f"{k}={v}" for k, v in sorted(local_reasons.items())) or "none",
        )
    elif deferred:
        # A zero-throughput cycle caused by an outage and one caused by a stalled
        # queue look identical from the outside and need opposite responses — chase
        # the venue, or chase the settler. Say which one this was, and say plainly
        # that the evidence survived.
        logger.warning(
            "filter outcome settler: settled 0 of %d due rows — the venue was "
            "unreachable for all %d rows asked about (%d requests, %d rounds); those "
            "rows stay pending and are retried next cycle, none were written off",
            len(candidates),
            deferred,
            fetched_groups,
            rounds,
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
