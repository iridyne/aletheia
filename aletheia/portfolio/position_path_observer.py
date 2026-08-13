"""Append-only five-minute path observations for eligible Positions.

This module is observational. It never changes Position, ExitPlan, Risk, execution,
or Learning state. Missing identity or market data is persisted as unavailable
rather than guessed, so later analysis can distinguish absence from zero.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
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
_MAX_DURABLE_OBSERVATION_ATTEMPTS = 3


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


def _position_survives_target_bucket(position: dict[str, Any], *, bucket_open_ms: int, bucket_end_ms: int) -> bool:
    first_bucket_open_ms = _first_full_bucket_open_ms(position)
    if first_bucket_open_ms is None or bucket_open_ms < first_bucket_open_ms:
        return False

    status = str(position.get("status") or "").strip().lower()
    if status == "open":
        return True
    if status != "closed":
        return False

    value = position.get("closed_at")
    try:
        if isinstance(value, datetime):
            closed_at = value
        else:
            raw = str(value or "").strip()
            if not raw:
                return False
            closed_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if closed_at.tzinfo is None:
            return False
        bucket_end = datetime.fromtimestamp(bucket_end_ms / 1000, tz=timezone.utc)
        return closed_at.astimezone(timezone.utc) >= bucket_end
    except (OSError, OverflowError, TypeError, ValueError):
        return False


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


def _store_latest_position_result(
    position_id: str,
    *,
    instrument: InstrumentId | None,
    bar: Candle | None,
    reason: str,
    bucket_open_ms: int,
    bucket_end_ms: int,
    observed_at: str,
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


async def observe_open_position_paths(*, now: datetime | None = None) -> dict[str, Any]:
    """Persist the latest completed five-minute bar for eligible Positions."""

    from aletheia.data.ccxt_provider import get_market_data_provider

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now_ms = int(current.timestamp() * 1000)
    current_bucket_open_ms = (now_ms // _BUCKET_MS - 1) * _BUCKET_MS
    current_bucket_end_ms = current_bucket_open_ms + _BUCKET_MS
    observed_at = current.isoformat()
    current_positions = repo.list_positions_for_path_observation_bucket(
        bucket_open=datetime.fromtimestamp(current_bucket_open_ms / 1000, tz=timezone.utc).isoformat(),
        bucket_end=datetime.fromtimestamp(current_bucket_end_ms / 1000, tz=timezone.utc).isoformat(),
    )
    current_observed = repo.get_observed_position_ids_for_bucket(
        [str(position["id"]) for position in current_positions],
        bucket_open_ms=current_bucket_open_ms,
    )
    for position in current_positions:
        position_id = str(position["id"])
        first_bucket_open_ms = _first_full_bucket_open_ms(position)
        if position_id not in current_observed and (
            first_bucket_open_ms is None or current_bucket_open_ms >= first_bucket_open_ms
        ):
            repo.defer_position_path_observation_bucket(
                position_id,
                bucket_open_ms=current_bucket_open_ms,
                attempted_at=observed_at,
                reason="scheduled",
            )
    deferred_rows = repo.list_oldest_deferred_position_path_bucket()
    bucket_open_ms = int(deferred_rows[0]["bucket_open_ms"]) if deferred_rows else current_bucket_open_ms
    bucket_end_ms = bucket_open_ms + _BUCKET_MS
    positions = [
        position for row in deferred_rows if (position := repo.get_position(str(row["position_id"]))) is not None
    ]
    observed_position_ids = repo.get_observed_position_ids_for_bucket(
        [str(position["id"]) for position in positions],
        bucket_open_ms=bucket_open_ms,
    )
    summary: dict[str, Any] = {
        "positions": len(current_positions),
        "inserted": 0,
        "available": 0,
        "unavailable": 0,
        "deferred": 0,
        "errors": [],
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
        try:
            result = _store_latest_position_result(
                position_id,
                instrument=instrument,
                bar=bar,
                reason=reason,
                bucket_open_ms=bucket_open_ms,
                bucket_end_ms=bucket_end_ms,
                observed_at=observed_at,
            )
            if result is None:
                repo.resolve_deferred_position_path_observation(position_id, bucket_open_ms=bucket_open_ms)
                return
            inserted, status = result
            repo.resolve_deferred_position_path_observation(position_id, bucket_open_ms=bucket_open_ms)
            if inserted:
                summary["inserted"] += 1
                summary[status] += 1
        except Exception as exc:
            summary["errors"].append(
                {
                    "position_id": position_id,
                    "error_type": type(exc).__name__,
                }
            )

    def defer_retry(position_id: str, *, reason: str) -> None:
        repo.defer_position_path_observation_bucket(
            position_id,
            bucket_open_ms=bucket_open_ms,
            attempted_at=observed_at,
            reason=reason,
        )
        summary["deferred"] += 1

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
                result = await asyncio.wait_for(
                    provider.get_ohlcv(
                        instrument,
                        timeframe="5m",
                        limit=2,
                        since=bucket_open_ms,
                    ),
                    timeout=_OHLCV_REQUEST_TIMEOUT_SECONDS,
                )
                bar = next(
                    (candle for candle in result if candle.timestamp == bucket_open_ms and candle.is_closed),
                    None,
                )
                return (
                    instrument,
                    bar,
                    ("complete_exact_5m_bar" if bar is not None else "exact_5m_bar_unavailable"),
                    None,
                )
            except Exception as exc:
                return (
                    instrument,
                    None,
                    "market_data_fetch_failed",
                    type(exc).__name__,
                )

    try:
        instrument_groups: dict[InstrumentId, list[str]] = {}
        for position in positions:
            position_id = str(position["id"])
            if position_id in observed_position_ids:
                repo.resolve_deferred_position_path_observation(position_id, bucket_open_ms=bucket_open_ms)
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
                    repo.resolve_deferred_position_path_observation(position_id, bucket_open_ms=bucket_open_ms)
                    if inserted:
                        summary["inserted"] += 1
                        summary["unavailable"] += 1
                continue
            if bucket_open_ms < first_bucket_open_ms:
                repo.resolve_deferred_position_path_observation(position_id, bucket_open_ms=bucket_open_ms)
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

        task_instruments = {asyncio.create_task(fetch_bar(instrument)): instrument for instrument in instrument_groups}
        tasks = list(task_instruments)
        try:
            for completed in asyncio.as_completed(tasks, timeout=_OBSERVATION_CYCLE_TIMEOUT_SECONDS):
                instrument, bar, reason, error_type = await completed
                if error_type is not None:
                    summary["errors"].append(
                        {
                            "instrument": str(instrument),
                            "error_type": error_type,
                        }
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
                    asyncio.wait_for(provider.close(), timeout=_PROVIDER_CLOSE_TIMEOUT_SECONDS)
                    for provider in providers.values()
                ),
                return_exceptions=True,
            )
    return summary


def run_open_position_path_observer() -> dict[str, Any]:
    """Scheduler worker boundary for the asynchronous observer."""

    return asyncio.run(observe_open_position_paths())
