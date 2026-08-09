"""Universe Forward Outcome Recorder.

Tracks how universe members perform AFTER being selected, answering:
"did the scanner families actually pick winners?"

Two entry points called from UniverseService.refresh():
  1. register_pending(members) — create outcome rows for each member × horizon
  2. settle_due() — find overdue pending rows, fetch exit price, compute return

Self-evolution Tier 1:
  After settling, outcomes with 4h net_return below the loss threshold
  automatically generate guard_candidate knowledge items for human review.
  Closes the feedback loop: scanner signal → actual result → candidate rule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from aletheia.storage.models import UniverseForwardOutcome
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.universe.outcome_recorder")

# Tier 1: loss threshold for auto guard_candidate generation
_GUARD_HORIZON = "4h"
_LOSS_THRESHOLD_NET = -3.0
_DEDUP_WINDOW_DAYS = 7

_HORIZONS: dict[str, timedelta] = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
}

# The limit is deliberately far wider than the window itself needs. get_ohlcv()
# persists every bar it returns to market_ohlcv unconditionally — only the
# in-process cache is gated on `since` (ccxt_provider.get_ohlcv) — so one request
# warms the local window of every sibling row on the same instrument whose window
# falls inside the span it covers. The queue drains oldest-due first, so the bars
# a request warms are exactly the ones the following rows ask for.
#
# This is free: a wide limit is ONE round trip and ONE ccxt rate-limit token, the
# same as a narrow one. Measured over the live 208k-row backlog, widening from
# 20/65/51/99 to a uniform 300 cut the exchange requests needed to settle the
# whole miss population from 270 to 132 per 1000 rows.
#
# 300 rather than the ~1000 that measured slightly better (95/1000): it sits
# inside every venue cap in use (OKX history-candles 300, Binance 1000+), and a
# venue that clamps anyway still returns far more than the 96 bars the widest
# window here needs, so a clamp costs amortisation and never correctness.
_FETCH_PLAN: dict[str, tuple[str, int]] = {
    "15m": ("1m", 300),
    "1h": ("1m", 300),
    "4h": ("5m", 300),
    "24h": ("15m", 300),
}
_MAX_CONCURRENT_FETCHES = 8
_FETCH_TIMEOUT_SEC = 20.0

# Extra bars read either side of the derived window, absorbing an unaligned
# entry_ts boundary.
_LOCAL_BAR_MARGIN = 2
# Distinct labels so the local-store hit rate is measurable straight from the
# evidence ledger. The semantics are identical — both are the close of a bar that
# ended at or before due_at, past the same staleness gate — so both carry
# price_quality_ok=True.
_NETWORK_PRICE_SOURCE = "horizon_ohlcv"
_LOCAL_PRICE_SOURCE = "horizon_ohlcv_local"

# Ceiling on the exchange work one canonical cycle may spend. Bounds the resource
# that is actually shared — ccxt throttles per client, the venue throttles per IP,
# and a dozen other scheduler jobs draw on the same budget — so that raising row
# throughput no longer means raising external load.
#
# It is a hard cap on requests: settle_due_filter_outcomes spends the budget on
# instrument GROUPS, one request each, so 400 means <=400 requests per 10-minute
# cycle — 0.67/sec, a few percent of one ccxt client's throttle.
#
# Sized against the live queue rather than guessed. Measured on a clone of the
# 249k-row backlog, a 6,000-row cycle settles ~1,570 rows from the local window
# alone; the oldest rows fare worst because their 1m/5m bars have aged past the
# 2-day and 7-day retention windows and no backfill job maintains those
# timeframes. The remaining ~4,430 collapse into 363 instrument groups — the same
# few hundred symbols recur across every horizon and due time — so the entire
# slice costs at most 363 requests, and one request is worth ~12 rows.
#
# 400 therefore sits just above a full cycle's real cost: a normal cycle is never
# truncated by it, and a pathological one still cannot exceed 0.67 requests/sec.
# At ~6,000 rows per cycle against a measured arrival rate of ~1,054, the standing
# backlog drains in well under a day.
_CANONICAL_FETCH_CEILING = 400

# At most one drain of these queues runs at a time; see settle_due().
_settle_in_flight = False

# Marks an observation attempt that never reached the venue. See
# deferred_observation() for why "no price" needed splitting into two answers.
_DEFER_KEY = "deferred"


def deferred_observation(reason: str) -> dict:
    """Report that this row was never measured, and why. Carries no price.

    The observation layer used to answer with a price or with ``None``, and all
    three settle loops read ``None`` as "this row is unmeasurable" and wrote
    ``status='failed'`` — a terminal state no job revisits. But ``None`` covered
    two different facts:

    * the venue answered, and its answer holds no usable price for the window —
      the row really is unmeasurable, and failing it is correct;
    * the venue could not be reached at all — nothing was measured, and the bars
      the row would have been measured from are untouched.

    Conflating them destroys evidence. A 20-minute Binance IP ban
    (``418 -1003 ... IP banned until ...``) wrote off 335 canonical and forward
    rows in two settlement cycles, every one with ``exit_price_usd IS NULL`` and
    ``settlement_price_source IS NULL`` — no observation was ever taken — while
    their 1m bars sat in ``market_ohlcv`` the whole time. One failed fetch
    permanently condemned a row that was fully measurable.

    So this is the third answer: measured nothing, decide nothing, come back next
    cycle. It needs no new status value and no retry counter, because it lasts
    exactly as long as the outage that caused it — the row stays ``pending``,
    which is already the state meaning "still owed an observation".

    The verdict rides inside the existing ``dict[row_id, dict]`` observation map
    rather than a new return type, so every caller and test that reads
    ``observation.get("exit_price")`` keeps working: the price is ``None``, which
    is what a deferral means. Callers that must not write a terminal state check
    :func:`is_deferred` first.
    """
    return {"exit_price": None, _DEFER_KEY: True, "defer_reason": reason}


def is_deferred(observation: dict | None) -> bool:
    """True when the venue was never reached, so no terminal write is allowed."""
    return bool(observation) and bool(observation.get(_DEFER_KEY))


def build_pending(members: list[dict], entry_ts: str) -> list[UniverseForwardOutcome]:
    """Create pending outcome rows for each member × horizon.

    Args:
        members: list of member dicts (must have id, snapshot_id, symbol,
                 scanner_family, entry_price_usd).
        entry_ts: ISO timestamp of the universe snapshot creation.

    Returns:
        Precomputed outcome rows ready for bulk persistence.
    """
    ts = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    outcomes: list[UniverseForwardOutcome] = []
    skipped_no_price = 0
    for m in members:
        entry_price = m.get("entry_price_usd")
        if not entry_price or entry_price <= 0:
            skipped_no_price += 1
            continue

        for horizon_key, delta in _HORIZONS.items():
            due_at = (ts + delta).isoformat()
            outcomes.append(UniverseForwardOutcome(
                outcome_candidate_key=(
                    f"{m['learning_candidate_key']}:{horizon_key}"
                    if m.get("learning_candidate_key") else None
                ),
                member_id=m["id"],
                snapshot_id=m["snapshot_id"],
                symbol=m["symbol"],
                venue=m.get("venue"),
                market_type=m.get("market_type", "spot"),
                scanner_family=m.get("scanner_family"),
                direction=m.get("direction"),
                horizon=horizon_key,
                entry_price_usd=entry_price,
                entry_spread_bps=m.get("spread_bps"),
                funding_rate_at_entry=m.get("funding_rate"),
                entry_ts=entry_ts,
                due_at=due_at,
            ))

    if skipped_no_price:
        logger.info(
            "Forward-outcome registration skipped %d/%d members with no entry price "
            "(missing 1h OHLCV at feature collection)", skipped_no_price, len(members),
        )

    return outcomes


def register_pending(members: list[dict], entry_ts: str) -> int:
    """Build outside the writer transaction when possible, then bulk insert."""

    outcomes = build_pending(members, entry_ts)
    if outcomes:
        repo.insert_forward_outcomes_batch(outcomes)
        logger.debug("Registered %d forward outcome rows", len(outcomes))
    return len(outcomes)


async def settle_due(max_rows: int = 8000, time_budget_sec: float = 240.0) -> int:
    """Settle all overdue pending outcomes, at most one drain at a time.

    Two scheduler jobs call this — ``outcome_settler`` and ``universe_refresh``,
    which fire on the same 10-minute interval only 30s apart. ``max_instances=1``
    bounds each job against itself but not against the other, so both used to
    drain concurrently, and because each selects the same oldest-due rows they
    duplicated one another's exchange requests. That silently doubles external
    load and halves the useful work, so the second caller yields instead.

    A plain flag rather than an ``asyncio.Lock``: both callers run on the one
    scheduler event loop, there is no await between the test and the set, and
    yielding is the wanted behaviour anyway — queueing a second full drain behind
    the first would just run it against an already-drained queue.
    """
    global _settle_in_flight

    if _settle_in_flight:
        logger.info(
            "outcome settler: another drain is already in flight — yielding this call"
        )
        return 0
    _settle_in_flight = True
    try:
        return await _settle_due_once(
            max_rows=max_rows, time_budget_sec=time_budget_sec
        )
    finally:
        _settle_in_flight = False


async def _settle_due_once(*, max_rows: int, time_budget_sec: float) -> int:
    """Settle all overdue pending outcomes by fetching current prices.

    Processes rows in batches until the queue is empty or the wall-clock budget
    is exhausted (default 240s, safely under the scheduler's 300s job timeout).
    This prevents large backlogs from accumulating when many rows pile up between
    runs — previously a fixed limit=500 per call took hours to drain a deep queue.

    Args:
        max_rows: Maximum rows to process per call (safety cap across all batches).
        time_budget_sec: Wall-clock seconds to spend before stopping early.

    Returns:
        Number of outcomes settled (status='settled', not 'failed').
    """
    import time as _time

    deadline = _time.monotonic() + time_budget_sec
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    total_settled = 0
    total_processed = 0
    all_settled_rows: list[dict] = []
    # Bound one network wave below the scheduler's wall-clock budget even when
    # requests hit their timeout. The outer loop drains more waves while time remains.
    batch_size = 32
    from aletheia.config.loader import load_config
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    cfg = load_config()

    # Canonical filter outcomes are the authority for Learning contribution and
    # filter feedback.  They used to run only after the (often much larger)
    # Universe mirror queue, so a busy/slow forward queue could consume the whole
    # wall-clock budget and leave already-due canonical rows pending forever, so a
    # bounded share is reserved before draining mirrors.  This is measurement
    # scheduling only; it does not alter candidate selection, and it does not enter
    # the policy-epoch hash (see learning/experiments._policy_epoch_definition,
    # which folds decision policy only and deliberately excludes scheduling knobs).
    #
    # The share was a quarter, split on the assumption that the two queues were
    # comparable in size.  Measured, they are not: the canonical queue had 209,090
    # overdue rows while the forward mirror had ~25, so three quarters of the
    # budget sat on the queue that had nothing to do.  Inverted to match the
    # measurement.  The forward mirror still gets a quarter of the rows plus
    # whatever wall clock canonical leaves, which is ample for a two-digit queue.
    #
    # Tiny explicit calls are commonly used to settle one forward row in tests
    # and maintenance probes; preserve that contract. Scheduled production calls
    # use a large cap and therefore receive the reserved canonical quota.
    filter_row_budget = min(6000, max_rows * 3 // 4) if max_rows >= 4 else 0
    filter_time_budget = min(180.0, max(0.0, time_budget_sec * 0.75))
    filter_settled = 0
    if filter_row_budget and filter_time_budget > 0:
        try:
            from aletheia.universe.filter_outcome_recorder import (
                settle_due_filter_outcomes,
            )

            filter_settled = await settle_due_filter_outcomes(
                max_rows=filter_row_budget,
                time_budget_sec=filter_time_budget,
                max_fetches=_CANONICAL_FETCH_CEILING,
            )
        except Exception as exc:
            logger.warning("filter outcome settlement failed (non-fatal): %s", exc)

    forward_row_budget = max(0, max_rows - filter_row_budget)
    # Rows this cycle asked about and could not reach. They keep status='pending',
    # which is also what list_pending_forward_outcomes selects on, so without this
    # exclusion the next batch re-selects the same oldest-due head and asks the
    # same unreachable venue about the same rows until the wall clock runs out —
    # an unbounded retry inside one cycle, aimed at an endpoint that is very
    # possibly refusing us for volume in the first place.
    deferred_ids: set[str] = set()
    while total_processed < forward_row_budget:
        if _time.monotonic() >= deadline:
            logger.info(
                "outcome_settler: time budget (%.0fs) reached — %d processed, %d settled; "
                "remaining rows will be handled in the next cycle",
                time_budget_sec, total_processed, total_settled,
            )
            break

        # The limit is widened by the deferrals so excluding them still leaves a
        # full batch of rows this cycle has not tried yet.
        pending = [
            row
            for row in repo.list_pending_forward_outcomes(
                before_ts=now_iso, limit=batch_size + len(deferred_ids)
            )
            if row["id"] not in deferred_ids
        ]
        if not pending:
            break  # queue drained

        observations = await _batch_fetch_market_observations(pending, now=now)

        batch_settled_rows: list[dict] = []
        batch_deferred = 0
        for row in pending:
            symbol = row["symbol"]
            observation = observations.get(row["id"])
            if is_deferred(observation):
                # Never reached the venue, so nothing about this row was measured
                # and there is no verdict to write. It stays pending and comes
                # back next cycle, when the venue may answer again.
                deferred_ids.add(row["id"])
                batch_deferred += 1
                total_processed += 1
                continue
            price = observation.get("exit_price") if observation else None
            if price is None or price <= 0:
                repo.fail_forward_outcome(row["id"], settled_at=now_iso)
                logger.warning(
                    "forward_outcome FAILED: symbol=%s horizon=%s — price unavailable",
                    symbol, row.get("horizon"),
                )
                total_processed += 1
                continue

            entry = row["entry_price_usd"]

            # Sanity: reject price deviating >10x from entry in either direction.
            # Catches cross-symbol contamination (e.g. BTC price ~50000 applied to
            # PEPE at entry 0.000003 → ratio 1.6B), which ccxt can return without
            # raising when a symbol isn't found on the fallback exchange.
            ratio = price / entry
            if ratio > 10.0 or ratio < 0.1:
                # A real verdict on a real observation: record what was seen and
                # rejected, so this row is not mistaken for one that was never
                # measured at all (see repo.fail_forward_outcome).
                repo.fail_forward_outcome(
                    row["id"],
                    settled_at=now_iso,
                    exit_price=price,
                    settlement_price_source=observation.get("price_source"),
                )
                logger.warning(
                    "forward_outcome FAILED: symbol=%s horizon=%s — exit price rejected",
                    symbol, row.get("horizon"),
                )
                logger.warning(
                    "Rejected anomalous exit price for %s: entry=%.8g exit=%.8g ratio=%.2f",
                    symbol, entry, price, ratio,
                )
                total_processed += 1
                continue

            entry_dt = _parse_dt(row.get("entry_ts"))
            due_dt = _parse_dt(row.get("due_at"))
            hold_seconds = (
                max(0.0, (due_dt - entry_dt).total_seconds())
                if entry_dt and due_dt
                else _HORIZONS.get(row.get("horizon"), timedelta()).total_seconds()
            )
            economics = estimate_outcome_economics(
                entry_price=entry,
                exit_price=price,
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
            forward_return = economics["gross_return_pct"]

            repo.settle_forward_outcome(
                outcome_id=row["id"],
                exit_price=price,
                forward_return=forward_return,
                settled_at=now_iso,
                economics=economics,
                settlement_price_source=observation.get("price_source"),
                settlement_lag_sec=observation.get("settlement_lag_sec"),
            )
            total_settled += 1
            total_processed += 1
            batch_settled_rows.append({
                **row,
                **economics,
                "forward_return_pct": forward_return,
                "settlement_price_source": observation.get("price_source"),
                "settlement_lag_sec": observation.get("settlement_lag_sec"),
            })

        if batch_settled_rows:
            logger.info(
                "outcome_settler: settled %d / %d in this batch",
                len(batch_settled_rows), len(pending),
            )
        all_settled_rows.extend(batch_settled_rows)

        if batch_deferred == len(pending):
            # A whole batch reached nothing: the venue is down, not this batch.
            # Draining further batches against it would only add load to an
            # endpoint that is already refusing, and every one of those rows is
            # measurable again the moment it answers.
            logger.warning(
                "outcome_settler: the venue answered none of %d rows — stopping the "
                "forward drain for this cycle, %d rows left pending",
                len(pending), len(deferred_ids),
            )
            break

    if total_settled:
        logger.info(
            "outcome_settler: settled %d forward outcomes total (%d processed)",
            total_settled, total_processed,
        )

    # --- PnL Attribution (XAlpha inspired) ---
    # For each settled trade, compute factor + agent + regime attribution
    # and update the factor effectiveness tracker.
    if all_settled_rows:
        try:
            from aletheia.portfolio.pnl_attribution import attribute_trade, record_attribution

            for settled in all_settled_rows:
                attr = attribute_trade(settled)
                record_attribution(attr)
        except Exception as exc:
            logger.debug("PnL attribution skipped: %s", exc)

    # Emit cost-aware cohort metrics for observability (§4.2).
    if total_settled:
        try:
            stats = repo.calibration_stats()
            for row in stats:
                if row["n"] >= 10:
                    wr = row["win_count"] / row["n"] * 100
                    logger.info(
                        "METRIC family=%s direction=%s market_type=%s "
                        "horizon=%s n=%d net_win_rate=%.1f%%",
                        row["scanner_family"],
                        row["direction"],
                        row["market_type"],
                        row["horizon"],
                        row["n"],
                        wr,
                    )
        except Exception:
            pass
    # Log per-family filter pressure: families whose filter-rejected candidates
    # show positive net returns (only meaningful once filter_candidate_outcomes
    # accumulates rows with scanner_family from the fine filter stage).
    if total_settled:
        try:
            from aletheia.universe.filter_advisor import get_family_filter_pressure_summary
            pressure = get_family_filter_pressure_summary()
            for entry in pressure:
                logger.info(
                    "FILTER_PRESSURE family=%s direction=%s market_type=%s "
                    "horizon=%s avg_net_missed=%.3f%% miss_rate=%.1f%% n=%d",
                    entry["scanner_family"],
                    entry["direction"],
                    entry["market_type"],
                    entry["horizon"],
                    entry["avg_net_missed"],
                    entry["miss_rate"] * 100,
                    entry["n"],
                )
        except Exception:
            pass

    # Tier 1: auto-generate guard candidates for significant losses
    if all_settled_rows:
        _maybe_generate_guard_candidates(all_settled_rows)

    total_settled += filter_settled

    # Position X rows are a separate, observation-only cohort. They reuse the
    # exact-instrument price path but never enter scanner or Operator ranking.
    try:
        from aletheia.data.perp_structure_shadow import (
            settle_due_perp_structure_outcomes,
        )
        total_settled += await settle_due_perp_structure_outcomes(limit=100)
    except Exception as exc:
        logger.warning("perp structure shadow settlement failed (non-fatal): %s", exc)

    return total_settled


async def _batch_fetch_market_observations(
    rows: list[dict],
    *,
    now: datetime,
) -> dict[str, dict]:
    """Fetch each outcome's target-horizon price on its exact instrument.

    OHLCV is preferred because an overdue scheduler run must still settle at
    ``due_at`` rather than at the later wall-clock time. Ticker fallback is usable
    for net learning only when the lag is within 10% of the requested horizon.

    The already-persisted ``market_ohlcv`` window is consulted first. Those are the
    very bars this settler wrote through ``ccxt_provider._persist``, so a hit
    reproduces the network answer exactly — and costs no exchange round trip.
    Because ``since`` is non-None the provider's in-process cache is bypassed on
    both read and write (``ccxt_provider.get_ohlcv``), so without this the settler
    pays one live request per row even for a window it already owns.
    """
    import asyncio

    from aletheia.config.loader import load_config
    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider()
    default_venue = load_config().market_data.ccxt_exchange
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)
    local_reasons: dict[str, int] = {}

    async def fetch_one(row: dict) -> tuple[str, dict | None]:
        # Plan construction is caught HERE rather than left to fetch_bounded,
        # because its failures are deterministic — an unparseable timestamp or a
        # symbol that is not an instrument on this venue — and a deterministic
        # failure must stay terminal. A deferral is bounded only by the outage
        # that caused it; a row that can never be planned has no such end, so
        # deferring it would park it in the queue until retention deleted it and
        # re-plan it every cycle. Keeping this catch local means fetch_bounded's
        # own catch only ever sees the transport.
        try:
            plan = _build_plan(row, default_venue=default_venue, now=now)
        except Exception as exc:
            logger.debug(
                "outcome observation unplannable for %s: %s", row.get("id"), exc
            )
            return row["id"], None
        if plan is None:
            return row["id"], None

        # Local store first, deliberately OUTSIDE the semaphore: a hit must not
        # consume one of the _MAX_CONCURRENT_FETCHES network permits, which is
        # where the throughput comes from. Only a miss (absent / stale / short)
        # falls through to the exchange below.
        local, reason = _local_observation(plan)
        local_reasons[reason] = local_reasons.get(reason, 0) + 1
        if local is not None:
            return plan.row_id, local

        async with semaphore:
            # Which probes RAISED, as opposed to answering with nothing usable.
            # This is the only place that distinction still exists: past this
            # function the observation map holds prices, and the caller cannot
            # tell an empty venue from an unreachable one.
            unreachable: list[str] = []
            try:
                candles = await provider.get_ohlcv(
                    plan.instrument,
                    timeframe=plan.timeframe,
                    limit=plan.limit,
                    since=plan.entry_ms,
                )
                window = _window_observation(
                    [
                        (int(c.timestamp), float(c.high), float(c.low), float(c.close))
                        for c in candles
                    ],
                    entry_ms=plan.entry_ms,
                    due_ms=plan.due_ms,
                    bar_ms=plan.bar_ms,
                )
                if window is not None:
                    return plan.row_id, {
                        "exit_price": window["exit_price"],
                        "range_high": window["range_high"],
                        "range_low": window["range_low"],
                        "price_source": _NETWORK_PRICE_SOURCE,
                        "price_quality_ok": True,
                        "settlement_lag_sec": plan.lag_sec,
                    }
            except Exception as exc:
                unreachable.append(f"ohlcv:{type(exc).__name__}")
                logger.debug(
                    "forward outcome OHLCV unavailable for %s/%s: %s",
                    plan.instrument,
                    plan.horizon,
                    exc,
                )

            try:
                ticker = await provider.get_ticker(plan.instrument)
                price = float(ticker.last_price) if ticker.last_price else None
            except Exception as exc:
                unreachable.append(f"ticker:{type(exc).__name__}")
                price = None
            if not price or price <= 0:
                # Terminal ONLY when both probes answered. If either raised, this
                # row's window was never looked at, so there is nothing to
                # conclude about it — see deferred_observation().
                if unreachable:
                    return plan.row_id, deferred_observation(",".join(unreachable))
                return plan.row_id, None
            hold_sec = max(1.0, (plan.due_dt - plan.entry_dt).total_seconds())
            near_due = plan.lag_sec <= hold_sec * 0.10
            return plan.row_id, {
                "exit_price": price,
                "range_high": None,
                "range_low": None,
                "price_source": "near_due_ticker" if near_due else "late_ticker",
                "price_quality_ok": near_due,
                "settlement_lag_sec": plan.lag_sec,
            }

    async def fetch_bounded(row: dict) -> tuple[str, dict | None]:
        try:
            return await asyncio.wait_for(fetch_one(row), timeout=_FETCH_TIMEOUT_SEC)
        except Exception as exc:
            # fetch_one absorbs its own deterministic failures above, so whatever
            # reaches here is the transport — a _FETCH_TIMEOUT_SEC expiry above
            # all, which is the ordinary shape of a venue that stopped answering.
            logger.debug("forward outcome fetch failed for %s: %s", row.get("id"), exc)
            return row["id"], deferred_observation(f"fetch:{type(exc).__name__}")

    try:
        fetched = await asyncio.gather(*(fetch_bounded(row) for row in rows))
        if local_reasons:
            logger.info(
                "outcome observations: %d rows, local OHLCV %s",
                len(rows),
                " ".join(f"{k}={v}" for k, v in sorted(local_reasons.items())),
            )
        observations = {
            outcome_id: value for outcome_id, value in fetched if value is not None
        }
        # One line per batch, not one per row. The ban that destroyed 335 rows
        # logged only per-row "price unavailable" warnings, which read as a
        # data-quality drift across many symbols rather than as one venue going
        # away — so the actual cause took a manual ccxt probe to find.
        deferrals = {
            outcome_id: value
            for outcome_id, value in observations.items()
            if is_deferred(value)
        }
        if deferrals:
            reasons: dict[str, int] = {}
            for value in deferrals.values():
                reason = str(value.get("defer_reason") or "unknown")
                reasons[reason] = reasons.get(reason, 0) + 1
            logger.warning(
                "outcome observations: %d of %d rows could not be reached (%s) — "
                "left pending rather than written off",
                len(deferrals),
                len(rows),
                " ".join(f"{k}={v}" for k, v in sorted(reasons.items())),
            )
        return observations
    finally:
        await provider.close()


def local_market_observations(
    rows: list[dict],
    *,
    now: datetime,
    default_venue: str | None = None,
) -> tuple[dict[str, dict], list[dict], dict[str, int]]:
    """Observe every row that the already-persisted window can answer. No network.

    Returns ``(observations_by_row_id, rows_still_needing_the_exchange, reasons)``.

    This exists so a settle loop can drain its local-hit majority without holding
    any exchange resource at all: measured on the live queue it observes ~4,100
    rows/sec, and ~71% of the backlog answers from disk. Splitting it out of
    ``_batch_fetch_market_observations`` lets the caller bound the exchange work by
    a fetch budget while leaving the local work bounded only by rows and time.

    A row that misses is returned for the caller to decide about; it is NEVER
    reported as an observation failure. That distinction matters because both
    settle loops treat "no observation" as terminal and mark the row ``failed``,
    so a row deferred for lack of budget must never reach that path.
    """
    if default_venue is None:
        from aletheia.config.loader import load_config

        default_venue = load_config().market_data.ccxt_exchange

    observations: dict[str, dict] = {}
    misses: list[dict] = []
    reasons: dict[str, int] = {}
    for row in rows:
        try:
            plan = _build_plan(row, default_venue=default_venue, now=now)
        except Exception as exc:
            # An unparseable instrument is the exchange path's problem to report,
            # exactly as before this was split out.
            logger.debug("local observation plan failed for %s: %s", row.get("id"), exc)
            reasons["unplannable"] = reasons.get("unplannable", 0) + 1
            misses.append(row)
            continue
        if plan is None:
            reasons["unplannable"] = reasons.get("unplannable", 0) + 1
            misses.append(row)
            continue
        observation, reason = _local_observation(plan)
        reasons[reason] = reasons.get(reason, 0) + 1
        if observation is not None:
            observations[plan.row_id] = observation
        else:
            misses.append(row)
    return observations, misses, reasons


def fetch_group_key(
    row: dict,
    *,
    now: datetime,
    default_venue: str,
) -> str | None:
    """Identify the single exchange request that could serve this row.

    Rows sharing a key ask the same endpoint for the same series, differing only
    in which slice of it they need. Because ``get_ohlcv`` persists every bar it
    returns, one request for the oldest member of a group also answers every
    later member whose window falls inside the span it covered — so a caller that
    spends one request per GROUP rather than one per ROW settles several rows per
    request instead of one, at no extra external load.

    Returns ``None`` when the row cannot be planned, so the caller can leave it to
    the per-row path to report rather than silently folding it into a group.
    """
    try:
        plan = _build_plan(row, default_venue=default_venue, now=now)
    except Exception:
        return None
    if plan is None:
        return None
    return f"{plan.instrument}|{plan.timeframe}"


@dataclass(frozen=True)
class _ObservationPlan:
    """Everything one queue row needs to be observed, derived before any I/O.

    Duck-typed across the three tables this helper serves
    (``universe_forward_outcomes``, ``filter_candidate_outcomes``,
    ``perp_structure_shadow_outcomes``): requires id/symbol/entry_ts/due_at and
    reads venue/market_type/horizon when present.
    """

    row_id: str
    instrument: object  # InstrumentId; imported lazily, see _build_plan
    horizon: str
    timeframe: str
    limit: int  # network fetch limit, from _FETCH_PLAN
    bar_ms: int
    entry_dt: datetime
    due_dt: datetime
    entry_ms: int
    due_ms: int
    lag_sec: float


def _build_plan(
    row: dict, *, default_venue: str, now: datetime
) -> _ObservationPlan | None:
    """Derive the exact instrument and no-lookahead window for one row.

    Returns None for an unparseable entry/due pair. Raises for a symbol that is not
    a valid instrument — the caller's fetch_bounded() turns that into a per-row
    miss, exactly as it did before this was split out of fetch_one().
    """
    from aletheia.data.ccxt_provider import timeframe_ms
    from aletheia.data.symbols import InstrumentId

    entry_dt = _parse_dt(row.get("entry_ts"))
    due_dt = _parse_dt(row.get("due_at"))
    if entry_dt is None or due_dt is None:
        return None
    raw_market_type = str(row.get("market_type") or "spot").lower()
    market_type = (
        "perp"
        if raw_market_type in {"perp", "swap", "usdt_futures"}
        else raw_market_type
    )
    instrument = InstrumentId.from_parts(
        row.get("venue") or default_venue,
        row["symbol"],
        market_type,
    )
    horizon = row.get("horizon") or "1h"
    timeframe, limit = _FETCH_PLAN.get(horizon, ("1h", 27))
    return _ObservationPlan(
        row_id=row["id"],
        instrument=instrument,
        horizon=horizon,
        timeframe=timeframe,
        limit=limit,
        bar_ms=timeframe_ms(timeframe),
        entry_dt=entry_dt,
        due_dt=due_dt,
        entry_ms=int(entry_dt.timestamp() * 1000),
        due_ms=int(due_dt.timestamp() * 1000),
        lag_sec=max(0.0, (now - due_dt).total_seconds()),
    )


def _window_observation(
    bars: list[tuple[int, float, float, float]],
    *,
    entry_ms: int,
    due_ms: int,
    bar_ms: int,
) -> dict | None:
    """Aggregate the eligible fixed-horizon window; None when unusable.

    ``bars`` is ``(open_ms, high, low, close)``. Shared by the local-store and the
    network branch so the two paths cannot drift apart.

    Eligibility is exactly ``entry_ms <= open_ms and open_ms + bar_ms <= due_ms``:
    the bar that OPENS at ``due_at`` is excluded, so no future information can leak
    into a fixed-horizon result.

    Returns None when no bar is eligible, or when the last eligible bar closes more
    than ``max(60_000, bar_ms * 2)`` before ``due_at`` — a delisted or halted symbol
    must never settle off a stale bar.

    ``bar_ms <= 0`` (unsupported timeframe: ``timeframe_ms()`` returns 0) yields
    None rather than degenerating the predicate into an at-``due_at`` lookahead.
    """
    if bar_ms <= 0:
        return None
    eligible = sorted(
        (b for b in bars if entry_ms <= b[0] and b[0] + bar_ms <= due_ms),
        key=lambda b: b[0],
    )
    if not eligible:
        return None
    last = eligible[-1]
    if due_ms - (last[0] + bar_ms) > max(60_000, bar_ms * 2):
        return None
    return {
        "exit_price": float(last[3]),
        "range_high": max(float(b[1]) for b in eligible),
        "range_low": min(float(b[2]) for b in eligible),
        "bar_count": len(eligible),
    }


def _expected_bar_count(*, entry_ms: int, due_ms: int, bar_ms: int) -> int:
    """Count of exchange-aligned bars fully inside ``[entry_ms, due_ms]``.

    Aligned opens are multiples of ``bar_ms``. ``entry_ts`` is a snapshot wall-clock
    time and is NOT aligned, so the first eligible open is the smallest multiple at
    or after ``entry_ms`` and the last is the largest multiple at or before
    ``due_ms - bar_ms`` (the bar opening at ``due_at`` is excluded).
    """
    if bar_ms <= 0 or due_ms <= entry_ms:
        return 0
    first = -(-entry_ms // bar_ms) * bar_ms  # ceil to bar boundary
    last = (due_ms - bar_ms) // bar_ms * bar_ms  # floor to bar boundary
    if last < first:
        return 0
    return (last - first) // bar_ms + 1


def _local_window_bars(
    plan: _ObservationPlan,
) -> list[tuple[int, float, float, float]]:
    """Read the already-persisted window for one exact instrument. Never raises.

    One index-optimal seek on ``idx_market_ohlcv_instrument_tf_ts``
    (exchange, symbol, market_type, timeframe, ts DESC). ``before_ts=due_ms`` means
    the query cannot even return the bar that opens at ``due_at``, so no-lookahead
    holds at the SQL layer as well as inside _window_observation().

    Identity comes from the InstrumentId, not the raw row: ``from_parts`` has
    already lower-cased the exchange and canonicalised the symbol, which is exactly
    the form ``ccxt_provider._persist`` writes. Both scope fields being non-empty
    also short-circuits ``_resolve_ohlcv_scope``, so this can never degrade into a
    fuzzy cross-venue match. Deliberately no fallback-exchange lookup: _persist
    tags rows with the exchange that actually served the request, and settling a
    binance row from bitget bars is the contamination the caller's 10x ratio guard
    exists to catch.

    Called synchronously rather than via ``asyncio.to_thread``: ``get_db()`` is
    thread-local (storage/sqlite.py), so a worker thread would open a DIFFERENT
    database than the one holding these bars. An indexed seek costs well under a
    millisecond, which is cheaper than the correctness question.
    """
    if plan.bar_ms <= 0 or plan.due_ms <= plan.entry_ms:
        return []
    span = max(1, (plan.due_ms - plan.entry_ms) // plan.bar_ms + 1)
    # The parse is inside the try as well: a malformed stored bar must degrade to
    # "no local window" (and so to the exchange), never to an unsettled row.
    try:
        rows = repo.get_ohlcv_candles(
            plan.instrument.symbol,
            plan.timeframe,
            limit=min(2000, span + _LOCAL_BAR_MARGIN),
            exchange=plan.instrument.exchange,
            market_type=plan.instrument.market_type,
            before_ts=plan.due_ms,
        )
        out: list[tuple[int, float, float, float]] = []
        for r in rows:
            ts = r.get("ts")
            if ts is None or int(ts) < plan.entry_ms:
                continue
            out.append((int(ts), float(r["high"]), float(r["low"]), float(r["close"])))
        return out
    except Exception as exc:
        logger.debug(
            "local OHLCV read failed for %s/%s: %s",
            plan.instrument,
            plan.timeframe,
            exc,
        )
        return []


def _local_observation(plan: _ObservationPlan) -> tuple[dict | None, str]:
    """Try to satisfy one observation from the persisted market_ohlcv window.

    Returns ``(observation, reason)`` where reason is one of hit / absent / stale /
    short, so the local hit rate and its miss modes are measurable per batch
    without waiting for the evidence ledger to fill.

    The window must be BAR-COMPLETE. A tail gap is already caught by the staleness
    gate, but a MID-window gap would leave exit_price correct while range_high /
    range_low come out silently narrower than reality — minting a real
    net_return_pct beside a wrong mfe/mae, which is worse than not settling.
    Settling with ranges=None is no better: downstream could not tell "no path data
    available" from "path data withheld because it was incomplete". So a short
    window falls through to the exchange, which is authoritative for the window;
    the local store is a cache bounded by per-timeframe retention and by the
    rolling-window prune in upsert_ohlcv_candles, and a cache must prove itself.
    """
    bars = _local_window_bars(plan)
    if not bars:
        return None, "absent"
    window = _window_observation(
        bars, entry_ms=plan.entry_ms, due_ms=plan.due_ms, bar_ms=plan.bar_ms
    )
    if window is None:
        return None, "stale"
    expected = _expected_bar_count(
        entry_ms=plan.entry_ms, due_ms=plan.due_ms, bar_ms=plan.bar_ms
    )
    if expected <= 0 or window["bar_count"] < expected:
        return None, "short"
    return {
        "exit_price": window["exit_price"],
        "range_high": window["range_high"],
        "range_low": window["range_low"],
        "price_source": _LOCAL_PRICE_SOURCE,
        "price_quality_ok": True,
        "settlement_lag_sec": plan.lag_sec,
    }, "hit"


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _maybe_generate_guard_candidates(settled_rows: list[dict]) -> None:
    """Self-evolution Tier 1: generate guard_candidate items for notable losses.

    Only processes complete 4h net outcomes below -3%. Deduplicates within 7 days
    per symbol+family to avoid flooding the knowledge base.

    Never raises — failures are logged and ignored so settle_due() is unaffected.
    """
    from aletheia.knowledge.manager import create_guard_candidate

    candidates = [
        r for r in settled_rows
        if r.get("horizon") == _GUARD_HORIZON
        and r.get("net_return_pct") is not None
        and r.get("scanner_family")
    ]
    if not candidates:
        return

    generated = 0
    for row in candidates:
        net = row["net_return_pct"]
        direction = row.get("direction") or "long"
        raw_market_type = str(row.get("market_type") or "spot").lower()
        market_type = (
            "perp"
            if raw_market_type in {"perp", "futures", "swap", "usdt_futures"}
            else "spot"
        )
        symbol = row["symbol"]
        family = row["scanner_family"]

        # Direction-aware loss check
        is_loss = net < _LOSS_THRESHOLD_NET
        if not is_loss:
            continue

        # Dedup: skip if a candidate for this symbol+family exists within 7 days
        try:
            if repo.guard_candidate_exists_recent(
                symbol,
                family,
                _DEDUP_WINDOW_DAYS,
                direction=direction,
                market_type=market_type,
            ):
                logger.debug(
                    "evolution:tier1 skipped dedup symbol=%s family=%s", symbol, family
                )
                continue
        except Exception as e:
            logger.warning("evolution:tier1 dedup check failed: %s", e)
            continue

        title = f"Auto guard candidate: {symbol} {family} {direction} {market_type} 4h net loss"
        content = _build_guard_candidate_content(row, net, direction)

        # Stable guard_id per (family, direction, symbol) loss pattern so the
        # promoter (run_promotion_check) can match + promote/move it, and so the
        # frontmatter carries status="candidate" (else load_trading_guards(
        # status="candidate") filters it out). Without these the candidate was
        # invisible to the promotion chain — L-Bug-D/B3.
        guard_id = (
            f"auto_{family}_{direction}_{market_type}_{symbol}"
            .replace("/", "").replace(":", "").replace(" ", "_").lower()
        )
        try:
            create_guard_candidate(
                title=title,
                content=content,
                metadata={
                    "guard_id": guard_id,
                    "status": "candidate",
                    "scope": "paper_entry",
                    "applies_to": {
                        "symbol": [symbol],
                        "scanner_family": [family],
                        "direction": [direction],
                        "market_type": [market_type],
                    },
                    # predicates must be non-empty for the matcher to fire
                    # (_predicates_match returns False on []), so keep the family predicate.
                    "predicates": [{"field": "scanner_family", "op": "eq", "value": family}],
                    "outcome_id": row["id"],
                    "symbol": symbol,
                    "scanner_family": family,
                    "horizon": _GUARD_HORIZON,
                    "forward_return_pct": row.get("forward_return_pct"),
                    "net_return_pct": net,
                    "direction": direction,
                    "market_type": market_type,
                    "entry_ts": row.get("entry_ts", ""),
                    "auto_generated": True,
                    # Learned/auto guard: shadow-only, observe-only. Cannot auto-promote
                    # to a block; enforcement suppressed until an experiment authorizes.
                    "shadow_only": True,
                    "block_candidate": False,
                    "tier": 1,
                },
                tags=["auto_generated", "tier1_evolution", "shadow-only"],
            )
            generated += 1
            logger.info(
                "evolution:tier1 guard_candidate created symbol=%s family=%s return=%.2f%%",
                symbol, family, net,
            )
        except Exception as e:
            logger.warning(
                "evolution:tier1 failed to create guard_candidate symbol=%s: %s", symbol, e
            )

    if generated:
        logger.info("evolution:tier1 generated %d guard candidate(s)", generated)


def _build_guard_candidate_content(row: dict, net: float, direction: str) -> str:
    symbol = row["symbol"]
    family = row["scanner_family"]
    entry_price = row.get("entry_price_usd", "?")
    entry_ts = row.get("entry_ts", "?")
    outcome_id = row.get("id", "?")
    snapshot_id = row.get("snapshot_id", "?")
    market_type = row.get("market_type", "spot")

    gross = row.get("directional_return_pct")
    direction_label = "short" if direction == "short" else "long"

    return f"""## 观察

scanner_family=`{family}` 选出 `{symbol}`，方向 {direction_label}，
4h 成本后估算净收益 **{net:+.2f}%**（触发阈值：{_LOSS_THRESHOLD_NET:+.1f}%）。

## 信号上下文

- symbol: {symbol}
- scanner_family: {family}
- direction: {direction}
- market_type: {market_type}
- entry_price_usd: {entry_price}
- entry_ts: {entry_ts}
- horizon: 4h
- directional_return_pct: {gross if gross is not None else "unknown"}%
- net_return_pct: {net:+.2f}%
- outcome_id: {outcome_id}
- snapshot_id: {snapshot_id}

## 候选规则（shadow-only，观察态）

当 `{family}` scanner 选出 {direction} 信号，且 4h 成本后净收益为 {net:+.2f}%，
说明本次入场的时机或信号强度判断存在问题。

建议：
- 回溯该时点的 RSI、Vol z-score、ATR 等指标值，确认是否存在共性
- 若多次类似形态均出现亏损，可考虑在 scanners.py 中提高该族的门槛

**此条目为自动生成，medium_confidence，不参与 LLM 检索，且为 shadow-only：只用于观察/学习，
不会阻断或扣分任何交易。它无法自动升级为阻断——paper-block 权限需要 owner 显式操作，
或 `ranker_learned_guard` 实验给出正向 verdict（AT-01 + AT-02）后才可能获得。**
"""
