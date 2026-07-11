"""Bounded OHLCV catch-up for instruments entering the active Universe.

The Universe refresh remains latency-isolated from historical downloads. This
module plans exact instrument/timeframe gaps from SQLite and repairs only a
small number per scheduler cycle, with durable retry state and backoff.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aletheia.data.history_downloader import download_symbol_history
from aletheia.data.symbols import (
    InstrumentId,
    MARKET_FUTURES,
    MARKET_PERP,
    MARKET_SPOT,
)
from aletheia.storage import repositories as repo

logger = logging.getLogger("aletheia.active_ohlcv_backfill")

_TIMEFRAMES = ("1h", "4h")
_PARTIAL_RETRY_MINUTES = 60
_FAILURE_BACKOFF_BASE_MINUTES = 15
_FAILURE_BACKOFF_MAX_MINUTES = 6 * 60
_PAIR_TIMEOUT_SEC = 60.0
_RUN_BUDGET_SEC = 240.0


@dataclass(frozen=True)
class CoverageTarget:
    exchange: str
    symbol: str
    market_type: str
    timeframe: str
    rank: int
    bar_count: int
    consecutive_failures: int = 0
    last_attempt_at: str | None = None


@dataclass(frozen=True)
class BackfillRunResult:
    planned: int = 0
    attempted: int = 0
    ready: int = 0
    partial: int = 0
    failed: int = 0
    bars_fetched: int = 0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _active_instruments(member_limit: int = 50) -> list[tuple[InstrumentId, int]]:
    """Resolve active members to exact instruments without guessing venue/type."""
    result: list[tuple[InstrumentId, int]] = []
    seen: set[str] = set()
    for member in repo.get_active_universe_members(limit=member_limit):
        if not member.get("symbol") or not member.get("execution_eligible", True):
            continue
        asset = repo.get_universe_asset(member.get("asset_id")) or {}
        exchange = asset.get("venue")
        market_type = member.get("market_type") or asset.get("market_type")
        if market_type in {"usdt_futures", "swap"}:
            market_type = MARKET_PERP
        if not exchange or market_type not in {MARKET_SPOT, MARKET_PERP, MARKET_FUTURES}:
            continue
        try:
            instrument = InstrumentId.from_parts(exchange, member["symbol"], market_type)
        except ValueError:
            continue
        key = str(instrument)
        if key in seen:
            continue
        seen.add(key)
        result.append((instrument, int(member.get("rank") or 999)))
    return result


def plan_active_ohlcv_backfill(
    *,
    min_bars: int,
    max_pairs: int,
    now: datetime | None = None,
    member_limit: int = 50,
) -> list[CoverageTarget]:
    """Return due exact-instrument gaps, bounded and ordered by timeframe/rank."""
    if min_bars < 1 or max_pairs < 1:
        return []
    current = (now or _utcnow()).astimezone(timezone.utc)
    targets: list[CoverageTarget] = []

    for instrument, rank in _active_instruments(member_limit=member_limit):
        for timeframe in _TIMEFRAMES:
            coverage = repo.get_ohlcv_coverage(
                instrument.symbol,
                timeframe,
                exchange=instrument.exchange,
                market_type=instrument.market_type,
            )
            bar_count = int(coverage.get("bar_count") or 0)
            state = repo.get_ohlcv_backfill_state(
                instrument.exchange,
                instrument.symbol,
                instrument.market_type,
                timeframe,
            )
            if bar_count >= min_bars:
                if state and (state.get("status") != "ready" or state.get("bar_count") != bar_count):
                    repo.upsert_ohlcv_backfill_state(
                        exchange=instrument.exchange,
                        symbol=instrument.symbol,
                        market_type=instrument.market_type,
                        timeframe=timeframe,
                        status="ready",
                        bar_count=bar_count,
                        consecutive_failures=0,
                        last_attempt_at=state.get("last_attempt_at"),
                        next_attempt_at=None,
                        last_error=None,
                    )
                continue

            if state:
                retry_at = _parse_utc(state.get("next_attempt_at"))
                if retry_at and retry_at > current:
                    continue
            targets.append(CoverageTarget(
                exchange=instrument.exchange,
                symbol=instrument.symbol,
                market_type=instrument.market_type,
                timeframe=timeframe,
                rank=rank,
                bar_count=bar_count,
                consecutive_failures=int((state or {}).get("consecutive_failures") or 0),
                last_attempt_at=(state or {}).get("last_attempt_at"),
            ))

    timeframe_priority = {timeframe: index for index, timeframe in enumerate(_TIMEFRAMES)}
    targets.sort(key=lambda item: (
        timeframe_priority.get(item.timeframe, len(_TIMEFRAMES)),
        item.rank,
        item.bar_count,
        item.last_attempt_at or "",
        item.exchange,
        item.symbol,
    ))
    return targets[:max_pairs]


def _failure_retry_at(now: datetime, failures: int) -> str:
    delay = min(
        _FAILURE_BACKOFF_MAX_MINUTES,
        _FAILURE_BACKOFF_BASE_MINUTES * (2 ** max(0, failures - 1)),
    )
    return (now + timedelta(minutes=delay)).isoformat()


def _emit_persistent_failure(target: CoverageTarget, failures: int) -> None:
    if failures < 3:
        return
    try:
        from aletheia.infra.events import emit_infra_event

        emit_infra_event(
            severity="warning",
            source="ohlcv_active_backfill",
            code=(
                "ohlcv_backfill_failed:"
                f"{target.exchange}:{target.symbol}:{target.market_type}:{target.timeframe}"
            ),
            message=(
                f"OHLCV catch-up failed {failures} times for "
                f"{target.exchange}:{target.symbol}:{target.market_type}:{target.timeframe}"
            ),
            action="Check public market-data reachability; automatic retry is backed off",
            dedupe_sec=900,
        )
    except Exception:
        logger.debug("Could not emit OHLCV backfill failure event", exc_info=True)


async def run_active_ohlcv_backfill(
    *,
    min_bars: int,
    max_pairs: int,
    months: int,
    now: datetime | None = None,
    pair_timeout_sec: float = _PAIR_TIMEOUT_SEC,
    run_budget_sec: float = _RUN_BUDGET_SEC,
) -> BackfillRunResult:
    """Repair a bounded set of active coverage gaps and persist retry outcomes."""
    current = (now or _utcnow()).astimezone(timezone.utc)
    planned = plan_active_ohlcv_backfill(
        min_bars=min_bars,
        max_pairs=max_pairs,
        now=current,
    )
    ready = partial = failed = fetched = attempted = 0
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1.0, run_budget_sec)

    for target in planned:
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        attempted += 1
        repo.upsert_ohlcv_backfill_state(
            exchange=target.exchange,
            symbol=target.symbol,
            market_type=target.market_type,
            timeframe=target.timeframe,
            status="running",
            bar_count=target.bar_count,
            consecutive_failures=target.consecutive_failures,
            last_attempt_at=current.isoformat(),
            next_attempt_at=None,
            last_error=None,
        )
        error_code: str | None = None
        bars_fetched = 0
        try:
            download = await asyncio.wait_for(
                download_symbol_history(
                    target.symbol,
                    target.timeframe,
                    months=months,
                    exchange=target.exchange,
                    market_type=target.market_type,
                ),
                timeout=max(0.1, min(pair_timeout_sec, remaining)),
            )
            bars_fetched = int(download.bars_fetched or 0)
            fetched += bars_fetched
            if download.error:
                error_code = "provider_fetch_failed"
        except asyncio.TimeoutError:
            logger.warning(
                "Active OHLCV catch-up timed out for %s:%s:%s:%s",
                target.exchange,
                target.symbol,
                target.market_type,
                target.timeframe,
            )
            error_code = "download_timeout"
        except Exception as exc:
            logger.warning(
                "Active OHLCV catch-up raised %s for %s:%s:%s:%s",
                type(exc).__name__,
                target.exchange,
                target.symbol,
                target.market_type,
                target.timeframe,
            )
            error_code = "download_exception"

        coverage = repo.get_ohlcv_coverage(
            target.symbol,
            target.timeframe,
            exchange=target.exchange,
            market_type=target.market_type,
        )
        bar_count = int(coverage.get("bar_count") or 0)
        common = {
            "exchange": target.exchange,
            "symbol": target.symbol,
            "market_type": target.market_type,
            "timeframe": target.timeframe,
            "bar_count": bar_count,
            "last_attempt_at": current.isoformat(),
        }

        if bar_count >= min_bars:
            ready += 1
            repo.upsert_ohlcv_backfill_state(
                **common,
                status="ready",
                consecutive_failures=0,
                next_attempt_at=None,
                last_error=None,
            )
        elif error_code is None and (bar_count > target.bar_count or bars_fetched > 0):
            partial += 1
            repo.upsert_ohlcv_backfill_state(
                **common,
                status="partial",
                consecutive_failures=0,
                next_attempt_at=(current + timedelta(minutes=_PARTIAL_RETRY_MINUTES)).isoformat(),
                last_error=None,
            )
        else:
            failed += 1
            failures = target.consecutive_failures + 1
            error_code = error_code or "no_data"
            repo.upsert_ohlcv_backfill_state(
                **common,
                status="failed",
                consecutive_failures=failures,
                next_attempt_at=_failure_retry_at(current, failures),
                last_error=error_code,
            )
            _emit_persistent_failure(target, failures)

    return BackfillRunResult(
        planned=len(planned),
        attempted=attempted,
        ready=ready,
        partial=partial,
        failed=failed,
        bars_fetched=fetched,
    )
