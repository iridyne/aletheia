"""Append-only five-minute path observations for eligible Positions.

This module is observational. It never changes Position, ExitPlan, Risk, execution,
or Learning state. Missing identity or market data is persisted as unavailable
rather than guessed, so later analysis can distinguish absence from zero.
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from aletheia.data.providers import Candle, MarketDataProvider
from aletheia.data.symbols import InstrumentId, to_canonical
from aletheia.storage import repositories as repo
from aletheia.storage.repositories import insert_position_path_observation

_BUCKET_MS = 5 * 60_000
_MAX_CONCURRENT_OHLCV_REQUESTS = 4
_OHLCV_REQUEST_TIMEOUT_SECONDS = 15.0
_OBSERVATION_CYCLE_TIMEOUT_SECONDS = 60.0
_PROVIDER_CLOSE_TIMEOUT_SECONDS = 5.0
_MAX_DURABLE_OBSERVATION_ATTEMPTS = repo.POSITION_PATH_MAX_RETRY_ATTEMPTS
_MAX_RECOVERY_PASSES_PER_CYCLE = 2


def _physical_market_type(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized == "spot":
        return "spot"
    if normalized in {"perp", "swap", "usdt_futures", "future", "futures"}:
        return "perp"
    return None


def _finite(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _normalize_direction(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"long", "buy"}:
        return "long"
    if normalized in {"short", "sell"}:
        return "short"
    return None


def _first_full_bucket_open_ms(position: dict[str, Any]) -> int | None:
    value = position.get("opened_at")
    try:
        if isinstance(value, datetime):
            opened_at = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return None
            opened_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if opened_at.tzinfo is None:
            return None
        opened_at_ms = math.ceil(opened_at.astimezone(timezone.utc).timestamp() * 1000)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return ((opened_at_ms + _BUCKET_MS - 1) // _BUCKET_MS) * _BUCKET_MS


def _latest_completed_bucket_open_ms(now_ms: int) -> int:
    return (now_ms // _BUCKET_MS - 1) * _BUCKET_MS


def _observer_clock(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Position path observer clock must be timezone-aware")
    return now.astimezone(timezone.utc)


def expected_position_path_bucket_bounds(
    position: dict[str, Any], *, latest_completed_bucket_open_ms: int | None = None
) -> tuple[int | None, int | None]:
    """Return the inclusive expected bucket range for one eligible Position.

    The first bucket starts after the Position has been open for a complete
    five-minute interval. A closed Position ends at the last bucket whose close
    is no later than ``closed_at``; an open Position ends at the latest bucket
    completed by the observer clock. The optional clock cap prevents recovery
    from enqueueing a future bucket for a Position closed in the future.
    """

    first_bucket_open_ms = _first_full_bucket_open_ms(position)
    if first_bucket_open_ms is None:
        return None, None

    status = str(position.get("status") or "").strip().lower()
    if status == "open":
        last_bucket_open_ms = latest_completed_bucket_open_ms
    elif status == "closed":
        value = position.get("closed_at")
        try:
            if isinstance(value, datetime):
                closed_at = value
            else:
                raw = str(value or "").strip()
                if not raw:
                    return None, None
                closed_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if closed_at.tzinfo is None:
                return None, None
            closed_at_ms = int(closed_at.astimezone(timezone.utc).timestamp() * 1000)
        except (OSError, OverflowError, TypeError, ValueError):
            return None, None
        last_bucket_open_ms = (closed_at_ms // _BUCKET_MS - 1) * _BUCKET_MS
    else:
        return None, None

    if last_bucket_open_ms is not None and latest_completed_bucket_open_ms is not None:
        last_bucket_open_ms = min(last_bucket_open_ms, latest_completed_bucket_open_ms)
    return first_bucket_open_ms, last_bucket_open_ms


def _position_survives_target_bucket(
    position: dict[str, Any], *, bucket_open_ms: int, bucket_end_ms: int
) -> bool:
    del bucket_end_ms  # kept in the boundary signature for callers' clarity
    first_bucket_open_ms, last_bucket_open_ms = expected_position_path_bucket_bounds(
        position
    )
    if first_bucket_open_ms is None or bucket_open_ms < first_bucket_open_ms:
        return False
    if str(position.get("status") or "").strip().lower() == "open":
        return True
    return last_bucket_open_ms is not None and bucket_open_ms <= last_bucket_open_ms


def _resolve_instrument(position: dict[str, Any]) -> tuple[InstrumentId | None, str]:
    protection = repo.get_position_protection_state(str(position["id"]))
    if protection is not None:
        try:
            instrument = InstrumentId.from_parts(protection["venue"], protection["symbol"], protection["market_type"])
        except (KeyError, TypeError, ValueError):
            return None, "protection_instrument_identity_invalid"
        if instrument.symbol != to_canonical(
            str(position.get("symbol") or "")
        ) or instrument.market_type != _physical_market_type(position.get("market_type")):
            return None, "protection_instrument_identity_mismatch"
        return instrument, "position_protection_state"

    opportunity_id = str(position.get("opportunity_id") or "").strip()
    opportunity = repo.get_opportunity(opportunity_id) if opportunity_id else None
    member_id = str((opportunity or {}).get("universe_member_id") or "").strip()
    member = repo.get_universe_member(member_id) if member_id else None
    asset_id = str((member or {}).get("asset_id") or "").strip()
    asset = repo.get_universe_asset(asset_id) if asset_id else None
    if not member or not asset:
        return None, "instrument_identity_unavailable"

    physical_market = _physical_market_type(position.get("market_type"))
    member_market = _physical_market_type(member.get("market_type"))
    asset_market = _physical_market_type(asset.get("market_type"))
    symbol = to_canonical(str(position.get("symbol") or ""))
    if (
        not physical_market
        or (member_market is not None and physical_market != member_market)
        or physical_market != asset_market
        or symbol != to_canonical(str(member.get("symbol") or ""))
        or symbol != to_canonical(str(asset.get("symbol") or ""))
    ):
        return None, "universe_instrument_identity_mismatch"
    try:
        return (
            InstrumentId.from_parts(asset["venue"], symbol, physical_market),
            "universe_registry",
        )
    except (KeyError, TypeError, ValueError):
        return None, "universe_instrument_identity_invalid"


def _base_observation(position: dict[str, Any], *, bucket_open_ms: int, observed_at: str) -> dict[str, object]:
    from aletheia.agent_runtime.runtime_session import current_provenance

    provenance = current_provenance()
    return {
        "position_id": position["id"],
        "bucket_open_ms": bucket_open_ms,
        "observed_at": observed_at,
        "venue": None,
        "symbol": to_canonical(str(position.get("symbol") or "")),
        "market_type": _physical_market_type(position.get("market_type")),
        "direction": _normalize_direction(position.get("direction")),
        "entry_price": _finite(position.get("entry_price")),
        "quantity": _finite(position.get("quantity")),
        "bar_open": None,
        "bar_high": None,
        "bar_low": None,
        "bar_close": None,
        "bar_volume": None,
        "bar_provider": None,
        "mark_price": _finite(position.get("current_price")),
        "unrealized_pnl": _finite(position.get("unrealized_pnl")),
        "entry_fee": _finite(position.get("entry_fee")),
        "funding_cost": _finite(position.get("funding_cost")),
        "funding_source": str(position.get("funding_source") or "unavailable"),
        "stop_price": _finite(position.get("stop_price")),
        "take_profit_price": _finite(position.get("take_profit_price")),
        "exit_plan_version": max(0, int(position.get("exit_plan_version") or 0)),
        "evidence_status": "unavailable",
        "evidence_reason": "not_observed",
        "producer_commit": provenance.get("producer_commit"),
        "runtime_session_id": provenance.get("runtime_session_id"),
        "capture_mode": ("post_close_final_snapshot" if position.get("status") == "closed" else "open_snapshot"),
    }


def _store_unavailable(position: dict[str, Any], *, bucket_open_ms: int, observed_at: str, reason: str) -> bool:
    observation = _base_observation(position, bucket_open_ms=bucket_open_ms, observed_at=observed_at)
    observation["evidence_reason"] = reason
    return insert_position_path_observation(observation)


def _historical_position_snapshot(
    position: dict[str, Any], *, bucket_open_ms: int
) -> dict[str, Any]:
    """Use durable historical protection facts; never attach current mutable state."""

    snapshot = dict(position)
    snapshot["funding_cost"] = None
    snapshot["funding_source"] = "unavailable"
    snapshot["stop_price"] = None
    snapshot["take_profit_price"] = None
    snapshot["exit_plan_version"] = 0
    try:
        plan, _bisected = repo.get_position_exit_plan_for_bar(
            str(position["id"]),
            bar_open_ms=bucket_open_ms,
            bar_close_ms=bucket_open_ms + _BUCKET_MS,
        )
    except Exception:
        plan = None
    if plan is not None:
        snapshot["stop_price"] = _finite(plan.get("stop_price"))
        snapshot["take_profit_price"] = _finite(plan.get("take_profit_price"))
        snapshot["exit_plan_version"] = max(0, int(plan.get("version") or 0))
    return snapshot


def _store_latest_position_result(
    position_id: str,
    *,
    instrument: InstrumentId | None,
    bar: Candle | None,
    reason: str,
    bucket_open_ms: int,
    bucket_end_ms: int,
    observed_at: str,
    historical: bool = False,
) -> tuple[bool, str] | None:
    position = repo.get_position(position_id)
    if position is None or not _position_survives_target_bucket(
        position,
        bucket_open_ms=bucket_open_ms,
        bucket_end_ms=bucket_end_ms,
    ):
        return None

    if instrument is None or bar is None:
        return (
            _store_unavailable(
                position,
                bucket_open_ms=bucket_open_ms,
                observed_at=observed_at,
                reason=reason,
            ),
            "unavailable",
        )

    if historical:
        position = _historical_position_snapshot(
            position, bucket_open_ms=bucket_open_ms
        )
    observation = _base_observation(
        position,
        bucket_open_ms=bucket_open_ms,
        observed_at=observed_at,
    )
    close = _finite(bar.close)
    entry = _finite(position.get("entry_price"))
    quantity = _finite(position.get("quantity"))
    direction = _normalize_direction(position.get("direction"))
    if close is None or entry is None or quantity is None or direction is None:
        return (
            _store_unavailable(
                position,
                bucket_open_ms=bucket_open_ms,
                observed_at=observed_at,
                reason="position_or_bar_economics_invalid",
            ),
            "unavailable",
        )

    observation.update(
        {
            "venue": instrument.exchange,
            "bar_open": _finite(bar.open),
            "bar_high": _finite(bar.high),
            "bar_low": _finite(bar.low),
            "bar_close": close,
            "bar_volume": _finite(bar.volume),
            "bar_provider": str(bar.provider or "unknown"),
            "evidence_status": "available",
            "evidence_reason": "complete_exact_5m_bar",
        }
    )
    if position.get("status") != "closed":
        observation["mark_price"] = close
        observation["unrealized_pnl"] = (
            (close - entry) * quantity if direction == "long" else (entry - close) * quantity
        )
    return insert_position_path_observation(observation), "available"


def _record_inserted_summary(summary: dict[str, Any], *, inserted: bool, status: str) -> None:
    if inserted:
        summary["inserted"] += 1
        summary[status] += 1


def _first_missing_bucket(
    first_bucket_open_ms: int,
    last_bucket_open_ms: int,
    *,
    observed: set[int],
) -> int | None:
    for bucket_open_ms in range(
        first_bucket_open_ms, last_bucket_open_ms + 1, _BUCKET_MS
    ):
        if bucket_open_ms not in observed:
            return bucket_open_ms
    return None


async def observe_open_position_paths(
    *, now: datetime | None = None, _recovery_pass: int = 0
) -> dict[str, Any]:
    """Recover a bounded oldest-first prefix of each eligible Position path."""

    from aletheia.data.ccxt_provider import get_market_data_provider

    current = _observer_clock(now)
    cycle_started = time.monotonic()
    now_ms = int(current.timestamp() * 1000)
    latest_completed_bucket_open_ms = _latest_completed_bucket_open_ms(now_ms)
    observed_at = current.isoformat()
    positions = repo.list_positions_for_path_observation_recovery()
    position_ids = [str(position["id"]) for position in positions]
    bucket_state = repo.get_position_path_bucket_state(position_ids)
    summary: dict[str, Any] = {
        "positions": len(positions),
        "inserted": 0,
        "available": 0,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
    }

    # A process crash can leave an exhausted retry row behind. Resolve those
    # rows before discovering the next gap, so a Position never has an older
    # permanently pending bucket blocking its expected path. Terminal recovery
    # consumes this Position's one-per-round budget as well.
    terminal_position_ids: set[str] = set()
    for row in repo.list_exhausted_deferred_position_path_buckets():
        position_id = str(row["position_id"])
        terminal_position_ids.add(position_id)
        position = repo.get_position(position_id)
        bucket_open_ms = int(row["bucket_open_ms"])
        try:
            if position is not None and _position_survives_target_bucket(
                position,
                bucket_open_ms=bucket_open_ms,
                bucket_end_ms=bucket_open_ms + _BUCKET_MS,
            ):
                inserted = _store_unavailable(
                    position,
                    bucket_open_ms=bucket_open_ms,
                    observed_at=observed_at,
                    reason=f"max_retries_exceeded:{row['reason']}",
                )
                _record_inserted_summary(
                    summary, inserted=inserted, status="unavailable"
                )
            repo.resolve_deferred_position_path_observation(
                position_id, bucket_open_ms=bucket_open_ms
            )
        except Exception as exc:
            summary["errors"].append(
                {"position_id": position_id, "error_type": type(exc).__name__}
            )

    # Register at most the oldest missing bucket for each Position. The row is
    # durable before any provider I/O, which is the recovery contract for a
    # skipped/misfired observer tick.
    bucket_state = repo.get_position_path_bucket_state(position_ids)
    for position in positions:
        position_id = str(position["id"])
        if position_id in terminal_position_ids:
            continue
        state = bucket_state[position_id]
        first_bucket_open_ms, last_bucket_open_ms = expected_position_path_bucket_bounds(
            position,
            latest_completed_bucket_open_ms=latest_completed_bucket_open_ms,
        )
        if first_bucket_open_ms is None:
            # Preserve the existing explicit diagnostic for an open Position
            # whose entry timestamp cannot establish an expected range.
            if (
                str(position.get("status") or "").strip().lower() == "open"
                and latest_completed_bucket_open_ms >= 0
                and latest_completed_bucket_open_ms not in state["observed"]
                and latest_completed_bucket_open_ms not in state["deferred"]
            ):
                inserted = _store_unavailable(
                    position,
                    bucket_open_ms=latest_completed_bucket_open_ms,
                    observed_at=observed_at,
                    reason="position_opened_at_invalid",
                )
                _record_inserted_summary(
                    summary, inserted=inserted, status="unavailable"
                )
                if inserted:
                    state["observed"].add(latest_completed_bucket_open_ms)
            continue
        if last_bucket_open_ms is None or last_bucket_open_ms < first_bucket_open_ms:
            continue
        missing_bucket_open_ms = _first_missing_bucket(
            first_bucket_open_ms,
            last_bucket_open_ms,
            observed=state["observed"],
        )
        if (
            missing_bucket_open_ms is not None
            and missing_bucket_open_ms not in state["deferred"]
        ):
            repo.defer_position_path_observation_bucket(
                position_id,
                bucket_open_ms=missing_bucket_open_ms,
                attempted_at=observed_at,
                reason="scheduled",
            )
            state["deferred"].add(missing_bucket_open_ms)

    deferred_rows = repo.list_oldest_deferred_position_path_bucket()
    bucket_open_ms = int(deferred_rows[0]["bucket_open_ms"]) if deferred_rows else None
    bucket_end_ms = bucket_open_ms + _BUCKET_MS if bucket_open_ms is not None else None
    deferred_positions = [
        position
        for row in deferred_rows
        if (position := repo.get_position(str(row["position_id"]))) is not None
    ]
    observed_position_ids = {
        str(position["id"])
        for position in deferred_positions
        if bucket_open_ms in bucket_state.get(str(position["id"]), {}).get("observed", set())
    }
    providers: dict[str, MarketDataProvider] = {}
    tasks: list[asyncio.Task[tuple[InstrumentId, Candle | None, str, str | None]]] = []
    task_instruments: dict[
        asyncio.Task[tuple[InstrumentId, Candle | None, str, str | None]],
        InstrumentId,
    ] = {}
    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_OHLCV_REQUESTS)

    def store_result(
        position_id: str,
        *,
        instrument: InstrumentId | None,
        bar: Candle | None,
        reason: str,
    ) -> None:
        if bucket_open_ms is None or bucket_end_ms is None:
            return
        try:
            result = _store_latest_position_result(
                position_id,
                instrument=instrument,
                bar=bar,
                reason=reason,
                bucket_open_ms=bucket_open_ms,
                bucket_end_ms=bucket_end_ms,
                observed_at=observed_at,
                historical=bucket_open_ms < latest_completed_bucket_open_ms,
            )
            if result is None:
                repo.resolve_deferred_position_path_observation(
                    position_id, bucket_open_ms=bucket_open_ms
                )
                return
            inserted, status = result
            repo.resolve_deferred_position_path_observation(
                position_id, bucket_open_ms=bucket_open_ms
            )
            _record_inserted_summary(summary, inserted=inserted, status=status)
        except Exception as exc:
            summary["errors"].append(
                {"position_id": position_id, "error_type": type(exc).__name__}
            )
            try:
                defer_retry(position_id, reason="observation_store_failed")
            except Exception as retry_exc:
                summary["errors"].append(
                    {
                        "position_id": position_id,
                        "error_type": type(retry_exc).__name__,
                    }
                )

    def defer_retry(position_id: str, *, reason: str) -> None:
        if bucket_open_ms is None:
            return
        attempt_count = repo.defer_position_path_observation_bucket(
            position_id,
            bucket_open_ms=bucket_open_ms,
            attempted_at=observed_at,
            reason=reason,
        )
        if attempt_count < _MAX_DURABLE_OBSERVATION_ATTEMPTS:
            summary["deferred"] += 1
            return

        position = repo.get_position(position_id)
        if position is not None and _position_survives_target_bucket(
            position,
            bucket_open_ms=bucket_open_ms,
            bucket_end_ms=bucket_open_ms + _BUCKET_MS,
        ):
            inserted = _store_unavailable(
                position,
                bucket_open_ms=bucket_open_ms,
                observed_at=observed_at,
                reason=f"max_retries_exceeded:{reason}",
            )
            _record_inserted_summary(
                summary, inserted=inserted, status="unavailable"
            )
        repo.resolve_deferred_position_path_observation(
            position_id, bucket_open_ms=bucket_open_ms
        )

    async def fetch_bar(
        instrument: InstrumentId,
    ) -> tuple[InstrumentId, Candle | None, str, str | None]:
        async with semaphore:
            try:
                provider = providers.get(instrument.exchange)
                if provider is None:
                    provider = get_market_data_provider(
                        exchange=instrument.exchange,
                        native_ccxt=True,
                        fallback_exchange=instrument.exchange,
                    )
                    providers[instrument.exchange] = provider
                # native_ccxt providers expose the side-effect-free path. The
                # fallback keeps lightweight structural test doubles usable.
                fetch_ohlcv = getattr(provider, "get_ohlcv_observation", None)
                if fetch_ohlcv is None:
                    fetch_ohlcv = provider.get_ohlcv
                result = await asyncio.wait_for(
                    fetch_ohlcv(
                        instrument,
                        timeframe="5m",
                        limit=2,
                        since=bucket_open_ms,
                    ),
                    timeout=_OHLCV_REQUEST_TIMEOUT_SECONDS,
                )
                bar = next(
                    (
                        candle
                        for candle in result
                        if candle.timestamp == bucket_open_ms and candle.is_closed
                    ),
                    None,
                )
                if bar is not None and (
                    str(bar.exchange or "").strip().lower()
                    != instrument.exchange.strip().lower()
                    or not str(bar.provider or "").strip()
                ):
                    bar = None
                    reason = "exact_5m_bar_identity_unavailable"
                else:
                    reason = (
                        "complete_exact_5m_bar"
                        if bar is not None
                        else "exact_5m_bar_unavailable"
                    )
                return instrument, bar, reason, None
            except Exception as exc:
                return (
                    instrument,
                    None,
                    "market_data_fetch_failed",
                    type(exc).__name__,
                )

    try:
        instrument_groups: dict[InstrumentId, list[str]] = {}
        for position in deferred_positions:
            position_id = str(position["id"])
            if position_id in observed_position_ids:
                repo.resolve_deferred_position_path_observation(
                    position_id, bucket_open_ms=bucket_open_ms
                )
                continue
            first_bucket_open_ms = _first_full_bucket_open_ms(position)
            if first_bucket_open_ms is None:
                latest = repo.get_position(position_id)
                if latest is not None:
                    inserted = _store_unavailable(
                        latest,
                        bucket_open_ms=bucket_open_ms,
                        observed_at=observed_at,
                        reason="position_opened_at_invalid",
                    )
                    repo.resolve_deferred_position_path_observation(
                        position_id, bucket_open_ms=bucket_open_ms
                    )
                    _record_inserted_summary(
                        summary, inserted=inserted, status="unavailable"
                    )
                continue
            if bucket_open_ms < first_bucket_open_ms:
                repo.resolve_deferred_position_path_observation(
                    position_id, bucket_open_ms=bucket_open_ms
                )
                continue

            instrument, identity_reason = _resolve_instrument(position)
            if instrument is None:
                store_result(
                    position_id,
                    instrument=None,
                    bar=None,
                    reason=identity_reason,
                )
                continue
            instrument_groups.setdefault(instrument, []).append(position_id)

        task_instruments = {
            asyncio.create_task(fetch_bar(instrument)): instrument
            for instrument in instrument_groups
        }
        tasks = list(task_instruments)
        try:
            for completed in asyncio.as_completed(
                tasks, timeout=_OBSERVATION_CYCLE_TIMEOUT_SECONDS
            ):
                instrument, bar, reason, error_type = await completed
                if error_type is not None:
                    summary["errors"].append(
                        {"instrument": str(instrument), "error_type": error_type}
                    )
                    for position_id in instrument_groups[instrument]:
                        defer_retry(position_id, reason=reason)
                    continue
                if bar is None:
                    for position_id in instrument_groups[instrument]:
                        defer_retry(position_id, reason=reason)
                    continue
                for position_id in instrument_groups[instrument]:
                    store_result(
                        position_id,
                        instrument=instrument,
                        bar=bar,
                        reason=reason,
                    )
        except TimeoutError:
            for task, instrument in task_instruments.items():
                if task.done():
                    continue
                summary["errors"].append(
                    {
                        "instrument": str(instrument),
                        "error_type": "ObservationCycleTimeout",
                    }
                )
                for position_id in instrument_groups[instrument]:
                    defer_retry(position_id, reason="observation_cycle_timeout")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if providers:
            await asyncio.gather(
                *(
                    asyncio.wait_for(
                        provider.close(), timeout=_PROVIDER_CLOSE_TIMEOUT_SECONDS
                    )
                    for provider in providers.values()
                ),
                return_exceptions=True,
            )
    # A single missed tick must not create a permanent one-bucket lag: the
    # observer may make one bounded follow-up pass when the first pass was
    # healthy and completed within half of the job's cycle budget. Failed or
    # unavailable evidence stays on the durable queue for the next cycle.
    if (
        _recovery_pass + 1 < _MAX_RECOVERY_PASSES_PER_CYCLE
        and summary["available"] > 0
        and summary["unavailable"] == 0
        and summary["deferred"] == 0
        and not summary["errors"]
        and time.monotonic() - cycle_started
        < _OBSERVATION_CYCLE_TIMEOUT_SECONDS / 2
    ):
        follow_up = await observe_open_position_paths(
            now=current, _recovery_pass=_recovery_pass + 1
        )
        for key in ("inserted", "available", "unavailable", "deferred"):
            summary[key] += follow_up[key]
        summary["errors"].extend(follow_up["errors"])
    return summary


def build_position_path_observer_status(
    *, now: datetime | None = None
) -> dict[str, Any]:
    """Build a read-only durable recovery snapshot for control status."""

    current = _observer_clock(now)
    now_ms = int(current.timestamp() * 1000)
    latest_completed_bucket_open_ms = _latest_completed_bucket_open_ms(now_ms)
    positions = repo.list_positions_for_path_observation_recovery()
    position_ids = [str(position["id"]) for position in positions]
    bucket_state = repo.get_position_path_bucket_state(position_ids)
    oldest_missing: tuple[int, str] | None = None
    skipped = 0
    cutoff_ms = now_ms - 24 * 60 * 60 * 1000

    for position in positions:
        position_id = str(position["id"])
        first_bucket_open_ms, last_bucket_open_ms = expected_position_path_bucket_bounds(
            position,
            latest_completed_bucket_open_ms=latest_completed_bucket_open_ms,
        )
        if first_bucket_open_ms is None or last_bucket_open_ms is None:
            continue
        state = bucket_state[position_id]
        for bucket_open_ms in range(
            first_bucket_open_ms, last_bucket_open_ms + 1, _BUCKET_MS
        ):
            if bucket_open_ms not in state["observed"]:
                if oldest_missing is None or bucket_open_ms < oldest_missing[0]:
                    oldest_missing = (bucket_open_ms, position_id)
                if (
                    bucket_open_ms not in state["deferred"]
                    and bucket_open_ms + _BUCKET_MS >= cutoff_ms
                ):
                    skipped += 1

    oldest_missing_bucket = None
    if oldest_missing is not None:
        bucket_open_ms, position_id = oldest_missing
        oldest_missing_bucket = {
            "position_id": position_id,
            "bucket_open_ms": bucket_open_ms,
            "bucket_open_at": datetime.fromtimestamp(
                bucket_open_ms / 1000, tz=timezone.utc
            ).isoformat(),
        }
        lag_seconds = max(0.0, (now_ms - bucket_open_ms - _BUCKET_MS) / 1000.0)
    else:
        lag_seconds = 0.0

    last_24h = repo.count_position_path_observations_since(
        (current - timedelta(hours=24)).isoformat()
    )
    last_24h["skipped"] = skipped
    return {
        "eligible_position_count": len(positions),
        "latest_completed_bucket_open_ms": latest_completed_bucket_open_ms,
        "oldest_missing_bucket": oldest_missing_bucket,
        "deferred_bucket_count": repo.count_deferred_position_path_observation_buckets(),
        "observer_lag": {
            "seconds": round(lag_seconds, 3),
            "buckets": (
                int(math.ceil(lag_seconds * 1000 / _BUCKET_MS))
                if lag_seconds > 0
                else 0
            ),
        },
        "last_24h": last_24h,
    }


def run_open_position_path_observer() -> dict[str, Any]:
    """Scheduler worker boundary for the asynchronous observer."""

    return asyncio.run(observe_open_position_paths())
