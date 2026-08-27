"""Lifecycle-owned Fast Trigger watcher; public data only, no LLM hot path."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from aletheia.data.symbols import InstrumentId
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    ConditionalEntryAuthorization,
    UniverseSetup,
)
from aletheia.universe.fast_stream import (
    fast_market_data_venue_for_config,
    fast_market_stream_for_config,
)
from aletheia.universe.fast_trigger import (
    TriggerEngine,
    TriggerPolicy,
    materialize_trigger,
    validate_fast_trigger_stream,
)

logger = logging.getLogger("aletheia.fast_trigger_watch")

_watch_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_lifecycle_ready = False
_status: dict[str, object] = {
    "running": False,
    "reason": "fast_trigger_disabled",
    "watched_setups": 0,
}


async def _prewarm_market_structure(
    engine: TriggerEngine, instrument: InstrumentId
) -> None:
    """Best-effort slow-path warmup; missing history stays shadow-unavailable."""

    from aletheia.data.ccxt_provider import get_market_data_provider

    provider = get_market_data_provider(
        instrument.exchange,
        native_ccxt=True,
        fallback_exchange=instrument.exchange,
    )
    try:
        candles = await provider.get_ohlcv(instrument, timeframe="1m", limit=120)
        engine.preload_closed_candles([
            {
                "ts": candle.timestamp,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in candles
            if candle.is_closed and str(candle.exchange or "").lower() == instrument.exchange
        ])
    except Exception:
        logger.debug(
            "Fast Trigger market-structure warmup unavailable for %s",
            instrument,
            exc_info=True,
        )
    finally:
        await provider.close()


def current_fast_trigger_task() -> asyncio.Task | None:
    return _watch_task


def fast_trigger_watch_status() -> dict[str, object]:
    return dict(_status)


def register_fast_trigger_lifecycle() -> None:
    global _lifecycle_ready
    _lifecycle_ready = True


def start_fast_trigger_watch(
    loop: asyncio.AbstractEventLoop,
    cfg,
) -> asyncio.Task | None:
    global _watch_task, _stop_event, _status
    if _watch_task is not None and not _watch_task.done():
        return _watch_task
    reason = _configured_block_reason(cfg)
    if reason:
        _status = {"running": False, "reason": reason, "watched_setups": 0}
        return None
    _stop_event = asyncio.Event()
    _watch_task = loop.create_task(_watch_loop(cfg), name="fast_trigger_watch")
    _status = {"running": True, "reason": None, "watched_setups": 0}
    return _watch_task


def _configured_block_reason(cfg) -> str | None:
    reason = validate_fast_trigger_stream(cfg)
    if reason is None and (cfg.runtime.paused or cfg.runtime.kill_switch_active):
        reason = (
            "fast_trigger_runtime_paused"
            if cfg.runtime.paused
            else "fast_trigger_kill_switch_active"
        )
    return reason


def _watch_config_identity(cfg) -> tuple[object, ...]:
    return (
        cfg.fast_trigger.mode,
        cfg.fast_trigger.max_setups,
        cfg.fast_trigger.authorization_ttl_minutes,
        cfg.fast_trigger.trigger_behavior_version,
        cfg.exchange.paper_execution_mode,
        cfg.runtime.paused,
        cfg.runtime.kill_switch_active,
    )


def stop_fast_trigger_watch(*, release_lifecycle: bool = False) -> None:
    global _stop_event, _lifecycle_ready
    if _stop_event is not None:
        _stop_event.set()
    if release_lifecycle:
        _lifecycle_ready = False


async def reconfigure_fast_trigger_watch(cfg, loop: asyncio.AbstractEventLoop) -> dict[str, object]:
    """Apply a saved mode change to the lifecycle-owned watcher."""

    global _watch_task, _stop_event
    if not _lifecycle_ready and cfg.fast_trigger.mode != "off":
        return {
            "running": False,
            "reason": "fast_trigger_lifecycle_unavailable",
            "watched_setups": 0,
            "configured_mode": cfg.fast_trigger.mode,
            "task_started": False,
        }
    previous = _watch_task
    stop_fast_trigger_watch()
    if previous is not None and not previous.done():
        previous.cancel()
        await asyncio.gather(previous, return_exceptions=True)
    _watch_task = None
    _stop_event = None
    task = start_fast_trigger_watch(loop, cfg)
    status = fast_trigger_watch_status()
    status["configured_mode"] = cfg.fast_trigger.mode
    status["task_started"] = task is not None
    return status


async def _load_watchers(
    cfg,
    existing: dict[str, TriggerEngine] | None = None,
) -> tuple[dict[str, UniverseSetup], dict[str, TriggerEngine]]:
    await asyncio.to_thread(repo.expire_universe_setups)
    rows = await asyncio.to_thread(
        repo.list_watchable_universe_setups, int(cfg.fast_trigger.max_setups)
    )
    setups: dict[str, UniverseSetup] = {}
    engines: dict[str, TriggerEngine] = {}
    for row in rows:
        setup = UniverseSetup.model_validate(row)
        authorization_row = await asyncio.to_thread(
            repo.get_conditional_entry_authorization, setup.id
        )
        if authorization_row is None:
            continue
        authorization = ConditionalEntryAuthorization.model_validate(authorization_row)
        if datetime.fromisoformat(
            authorization.valid_until.replace("Z", "+00:00")
        ).astimezone(timezone.utc) <= datetime.now(timezone.utc):
            await asyncio.to_thread(
                repo.transition_universe_setup,
                setup.id,
                expected_states=(setup.state,),
                state="expired",
            )
            continue
        if authorization.trigger_spec_hash != setup.trigger_spec_hash:
            await asyncio.to_thread(
                repo.transition_universe_setup,
                setup.id,
                expected_states=(setup.state,),
                state="invalidated",
            )
            continue
        if (
            authorization.authorized_mode != cfg.fast_trigger.mode
            or authorization.direction != setup.direction
            or authorization.entry_zone != setup.entry_zone
            or authorization.invalidation_price != setup.invalidation_price
            or setup.venue.lower()
            != fast_market_data_venue_for_config(cfg)
        ):
            await asyncio.to_thread(
                repo.transition_universe_setup,
                setup.id,
                expected_states=(setup.state,),
                state="invalidated",
            )
            continue
        instrument = InstrumentId.from_parts(
            setup.venue, setup.symbol, setup.market_type
        )
        if existing is not None and setup.id in existing:
            setups[setup.id] = setup
            engines[setup.id] = existing[setup.id]
            continue
        engine = TriggerEngine(
            instrument=instrument,
            direction=setup.direction,
            breakout_level=setup.breakout_level,
            pullback_band=tuple(json.loads(setup.pullback_band)),
            entry_zone=tuple(json.loads(setup.entry_zone)),
            policy=TriggerPolicy(
                max_spread_bps=authorization.max_spread_bps,
                min_depth_10bps_usd=authorization.min_depth_10bps_usd,
            ),
        )
        await _prewarm_market_structure(engine, instrument)
        engine.state = setup.state
        setups[setup.id] = setup
        engines[setup.id] = engine
    return setups, engines


async def _watch_loop(cfg) -> None:
    global _status
    backoff = 1.0
    stop = _stop_event
    if stop is None:
        return
    while not stop.is_set():
        from aletheia.config.loader import load_config

        cfg = await asyncio.to_thread(load_config, persist_migrations=False)
        reason = _configured_block_reason(cfg)
        if reason:
            _status = {"running": False, "reason": reason, "watched_setups": 0}
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            except TimeoutError:
                pass
            continue
        stream = fast_market_stream_for_config(cfg)
        event_task: asyncio.Task | None = None
        try:
            await stream.connect()
            setups, engines = await _load_watchers(cfg)
            instruments = {engine.instrument for engine in engines.values()}
            await stream.set_instruments(instruments)
            _status = {
                "running": True,
                "reason": None,
                "watched_setups": len(setups),
            }
            iterator = stream.events().__aiter__()
            subscribed = instruments
            next_config_check = asyncio.get_running_loop().time() + 2.0
            while not stop.is_set():
                if event_task is None:
                    event_task = asyncio.create_task(anext(iterator))
                done, _ = await asyncio.wait({event_task}, timeout=2.0)
                if asyncio.get_running_loop().time() >= next_config_check:
                    current_cfg = await asyncio.to_thread(
                        load_config, persist_migrations=False
                    )
                    if _watch_config_identity(current_cfg) != _watch_config_identity(cfg):
                        cfg = current_cfg
                        break
                    next_config_check = asyncio.get_running_loop().time() + 2.0
                if not done:
                    fresh_setups, fresh_engines = await _load_watchers(cfg, engines)
                    fresh_instruments = {
                        engine.instrument for engine in fresh_engines.values()
                    }
                    if fresh_instruments != subscribed:
                        await stream.set_instruments(fresh_instruments)
                        subscribed = fresh_instruments
                        # A subscription generation change invalidates every
                        # rolling quote/trade/depth window; re-warm from empty.
                        fresh_setups, fresh_engines = await _load_watchers(cfg)
                    setups, engines = fresh_setups, fresh_engines
                    if stream.health().subscriptions_confirmed:
                        for engine in engines.values():
                            engine.confirm_subscription()
                    _status["watched_setups"] = len(setups)
                    continue
                try:
                    event = event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    event_task = None
                if stream.health().subscriptions_confirmed:
                    for engine in engines.values():
                        engine.confirm_subscription()
                for setup_id, engine in tuple(engines.items()):
                    if engine.instrument != event.instrument:
                        continue
                    before = engine.state
                    ready = engine.observe(event)
                    if before == "armed" and engine.state == "pullback_seen":
                        await asyncio.to_thread(
                            repo.transition_universe_setup,
                            setup_id,
                            expected_states=("armed",),
                            state="pullback_seen",
                        )
                    if ready is not None:
                        current_cfg = await asyncio.to_thread(
                            load_config, persist_migrations=False
                        )
                        result = await asyncio.to_thread(
                            materialize_trigger,
                            setup_id,
                            ready,
                            cfg=current_cfg,
                        )
                        logger.info(
                            "fast trigger setup=%s result=%s",
                            setup_id,
                            result.get("status"),
                        )
                        engines.pop(setup_id, None)
                        setups.pop(setup_id, None)
                backoff = 1.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _status = {
                "running": True,
                "reason": f"fast_trigger_stream_error:{type(exc).__name__}",
                "watched_setups": 0,
            }
            logger.warning("Fast Trigger stream restarting: %s", type(exc).__name__)
        finally:
            if event_task is not None and not event_task.done():
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
            await stream.close()
        if not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)
    _status = {"running": False, "reason": "fast_trigger_stream_closed", "watched_setups": 0}
