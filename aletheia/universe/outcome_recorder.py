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

_FETCH_PLAN: dict[str, tuple[str, int]] = {
    "15m": ("1m", 20),
    "1h": ("1m", 65),
    "4h": ("5m", 51),
    "24h": ("15m", 99),
}
_MAX_CONCURRENT_FETCHES = 8
_FETCH_TIMEOUT_SEC = 20.0


def register_pending(members: list[dict], entry_ts: str) -> int:
    """Create pending outcome rows for each member × horizon.

    Args:
        members: list of member dicts (must have id, snapshot_id, symbol,
                 scanner_family, entry_price_usd).
        entry_ts: ISO timestamp of the universe snapshot creation.

    Returns:
        Number of outcome rows created.
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

    if outcomes:
        repo.insert_forward_outcomes_batch(outcomes)
        logger.debug("Registered %d forward outcome rows", len(outcomes))

    if skipped_no_price:
        logger.info(
            "Forward-outcome registration skipped %d/%d members with no entry price "
            "(missing 1h OHLCV at feature collection)", skipped_no_price, len(members),
        )

    return len(outcomes)


async def settle_due(max_rows: int = 2000, time_budget_sec: float = 100.0) -> int:
    """Settle all overdue pending outcomes by fetching current prices.

    Processes rows in batches until the queue is empty or the wall-clock budget
    is exhausted (default 100s, safely under the scheduler's 120s job timeout).
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

    while total_processed < max_rows:
        if _time.monotonic() >= deadline:
            logger.info(
                "outcome_settler: time budget (%.0fs) reached — %d processed, %d settled; "
                "remaining rows will be handled in the next cycle",
                time_budget_sec, total_processed, total_settled,
            )
            break

        pending = repo.list_pending_forward_outcomes(before_ts=now_iso, limit=batch_size)
        if not pending:
            break  # queue drained

        observations = await _batch_fetch_market_observations(pending, now=now)

        batch_settled_rows: list[dict] = []
        for row in pending:
            symbol = row["symbol"]
            observation = observations.get(row["id"])
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
                repo.fail_forward_outcome(row["id"], settled_at=now_iso)
                logger.warning(
                    "forward_outcome FAILED: symbol=%s horizon=%s — price unavailable",
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

    if total_settled:
        logger.info(
            "outcome_settler: settled %d forward outcomes total (%d processed)",
            total_settled, total_processed,
        )

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
    # Tier 1: auto-generate guard candidates for significant losses
    if all_settled_rows:
        _maybe_generate_guard_candidates(all_settled_rows)

    # Use the same remaining scheduler budget to settle filter pass/reject
    # counterfactuals. They share this module's exact-instrument OHLCV fetch path
    # and the Stage-D economics formula; no second shadow-return definition.
    remaining_sec = max(0.0, deadline - _time.monotonic())
    remaining_rows = max(0, max_rows - total_processed)
    if remaining_rows and remaining_sec > 1.0:
        try:
            from aletheia.universe.filter_outcome_recorder import (
                settle_due_filter_outcomes,
            )

            total_settled += await settle_due_filter_outcomes(
                max_rows=remaining_rows,
                time_budget_sec=remaining_sec,
            )
        except Exception as exc:
            logger.warning("filter outcome settlement failed (non-fatal): %s", exc)

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
    """
    import asyncio

    from aletheia.config.loader import load_config
    from aletheia.data.ccxt_provider import get_market_data_provider, timeframe_ms
    from aletheia.data.symbols import InstrumentId

    provider = get_market_data_provider()
    default_venue = load_config().market_data.ccxt_exchange
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

    async def fetch_one(row: dict) -> tuple[str, dict | None]:
        async with semaphore:
            entry_dt = _parse_dt(row.get("entry_ts"))
            due_dt = _parse_dt(row.get("due_at"))
            if entry_dt is None or due_dt is None:
                return row["id"], None
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
            entry_ms = int(entry_dt.timestamp() * 1000)
            due_ms = int(due_dt.timestamp() * 1000)
            lag_sec = max(0.0, (now - due_dt).total_seconds())
            try:
                candles = await provider.get_ohlcv(
                    instrument,
                    timeframe=timeframe,
                    limit=limit,
                    since=entry_ms,
                )
                bar_ms = timeframe_ms(timeframe)
                eligible = [
                    c for c in candles
                    if entry_ms <= c.timestamp and c.timestamp + bar_ms <= due_ms
                ]
                if eligible:
                    last = eligible[-1]
                    max_gap_ms = max(60_000, bar_ms * 2)
                    if due_ms - (last.timestamp + bar_ms) <= max_gap_ms:
                        return row["id"], {
                            "exit_price": float(last.close),
                            "range_high": max(float(c.high) for c in eligible),
                            "range_low": min(float(c.low) for c in eligible),
                            "price_source": "horizon_ohlcv",
                            "price_quality_ok": True,
                            "settlement_lag_sec": lag_sec,
                        }
            except Exception as exc:
                logger.debug(
                    "forward outcome OHLCV unavailable for %s/%s: %s",
                    instrument,
                    horizon,
                    exc,
                )

            try:
                ticker = await provider.get_ticker(instrument)
                price = float(ticker.last_price) if ticker.last_price else None
            except Exception:
                price = None
            if not price or price <= 0:
                return row["id"], None
            hold_sec = max(1.0, (due_dt - entry_dt).total_seconds())
            near_due = lag_sec <= hold_sec * 0.10
            return row["id"], {
                "exit_price": price,
                "range_high": None,
                "range_low": None,
                "price_source": "near_due_ticker" if near_due else "late_ticker",
                "price_quality_ok": near_due,
                "settlement_lag_sec": lag_sec,
            }

    async def fetch_bounded(row: dict) -> tuple[str, dict | None]:
        try:
            return await asyncio.wait_for(fetch_one(row), timeout=_FETCH_TIMEOUT_SEC)
        except Exception as exc:
            logger.debug("forward outcome fetch failed for %s: %s", row.get("id"), exc)
            return row["id"], None

    try:
        fetched = await asyncio.gather(*(fetch_bounded(row) for row in rows))
        return {outcome_id: value for outcome_id, value in fetched if value is not None}
    finally:
        await provider.close()


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
                    "tier": 1,
                },
                tags=["auto_generated", "tier1_evolution"],
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

## 候选规则（待人工确认）

当 `{family}` scanner 选出 {direction} 信号，且 4h 成本后净收益为 {net:+.2f}%，
说明本次入场的时机或信号强度判断存在问题。

建议：
- 回溯该时点的 RSI、Vol z-score、ATR 等指标值，确认是否存在共性
- 若多次类似形态均出现亏损，可考虑在 scanners.py 中提高该族的门槛
- 确认无误后升级为 trading_guard，将阻止后续同类信号进入执行队列

**此条目为自动生成，medium_confidence，不会参与 LLM 检索。须人工审核后升级。**
"""
