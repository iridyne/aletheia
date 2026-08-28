"""Lifecycle-owned Fast Trigger watcher; public data only, no LLM hot path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from aletheia.data.symbols import InstrumentId
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    ConditionalEntryAuthorization,
    UniverseSetup,
)
from aletheia.trading.entry_plan import EntryPlan
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
_WATCHER_RELOAD_INTERVAL_SEC = 2.0


async def _prewarm_market_structure(engine: TriggerEngine, instrument: InstrumentId) -> None:
    """Warm from bounded exact-instrument local history; never fetch remotely."""

    try:
        candles = await asyncio.to_thread(
            repo.get_ohlcv_candles,
            instrument.symbol,
            "1m",
            120,
            instrument.exchange,
            instrument.market_type,
        )
        current_minute_ms = int(datetime.now(timezone.utc).timestamp() // 60 * 60_000)
        engine.preload_closed_candles(
            [
                {
                    "ts": candle["ts"],
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle["volume"],
                }
                for candle in candles
                if int(candle["ts"]) < current_minute_ms
            ]
        )
    except Exception:
        logger.debug(
            "Fast Trigger market-structure warmup unavailable for %s",
            instrument,
            exc_info=True,
        )


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
    if reason is None and cfg.agent.permission_mode not in {"Paper", "Full"}:
        reason = "entry_plan_permission_revoked"
    if reason is None and (cfg.runtime.paused or cfg.runtime.kill_switch_active):
        reason = "fast_trigger_runtime_paused" if cfg.runtime.paused else "fast_trigger_kill_switch_active"
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
    rows = await asyncio.to_thread(repo.list_watchable_universe_setups, int(cfg.fast_trigger.max_setups))
    setups: dict[str, UniverseSetup] = {}
    engines: dict[str, TriggerEngine] = {}
    for row in rows:
        setup = UniverseSetup.model_validate(row)
        if await asyncio.to_thread(
            repo.has_current_entry_plan_for_instrument,
            setup.venue,
            setup.symbol,
            setup.market_type,
            setup.direction,
        ):
            continue
        authorization_row = await asyncio.to_thread(repo.get_conditional_entry_authorization, setup.id)
        if authorization_row is None:
            continue
        authorization = ConditionalEntryAuthorization.model_validate(authorization_row)
        if datetime.fromisoformat(authorization.valid_until.replace("Z", "+00:00")).astimezone(
            timezone.utc
        ) <= datetime.now(timezone.utc):
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
            or setup.venue.lower() != fast_market_data_venue_for_config(cfg)
        ):
            await asyncio.to_thread(
                repo.transition_universe_setup,
                setup.id,
                expected_states=(setup.state,),
                state="invalidated",
            )
            continue
        instrument = InstrumentId.from_parts(setup.venue, setup.symbol, setup.market_type)
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


async def _load_entry_plan_watchers(
    cfg,
    existing: dict[str, TriggerEngine] | None = None,
) -> tuple[dict[str, EntryPlan], dict[str, TriggerEngine]]:
    plans: dict[str, EntryPlan] = {}
    engines: dict[str, TriggerEngine] = {}
    now = datetime.now(timezone.utc)
    for row in await asyncio.to_thread(repo.list_watchable_entry_plans, int(cfg.fast_trigger.max_setups)):
        payload = str(row["plan_json"])
        if hashlib.sha256(payload.encode()).hexdigest() != row["plan_hash"]:
            await asyncio.to_thread(
                repo.transition_entry_plan,
                row["id"],
                expected_status=row["status"],
                status="failed",
                reason="entry_plan_hash_mismatch",
            )
            continue
        try:
            plan = EntryPlan.model_validate_json(payload)
        except Exception:
            logger.error("Entry Plan %s failed stored-contract validation", row["id"])
            continue
        generation = await asyncio.to_thread(
            repo.get_candidate_planning_generation,
            plan.planning_generation_id,
        )
        if generation is None or not bool(generation["is_current"]):
            if row["status"] in {"ordered", "partially_filled"}:
                from aletheia.execution.gateway import cancel_entry_plan_order

                await asyncio.to_thread(
                    cancel_entry_plan_order,
                    plan.plan_id,
                    reason="incompatible_planning_generation",
                )
            else:
                await asyncio.to_thread(
                    repo.transition_entry_plan,
                    plan.plan_id,
                    expected_status=row["status"],
                    status="invalidated",
                    reason="incompatible_planning_generation",
                )
            continue
        try:
            from aletheia.risk.policy import resolve_risk_policy

            current_policy = resolve_risk_policy(cfg, plan.execution_account_mode)
            policy_changed = (
                current_policy.policy.policy_id != plan.risk_policy_id
                or current_policy.policy.revision != plan.risk_policy_revision
                or current_policy.policy_hash != plan.risk_policy_hash
            )
        except Exception:
            policy_changed = True
        account_changed = str(cfg.exchange.paper_execution_mode) != plan.execution_account_mode
        if policy_changed or account_changed:
            reason = (
                "entry_plan_risk_authority_revoked"
                if policy_changed
                else "entry_plan_execution_account_changed"
            )
            if row["status"] in {"ordered", "partially_filled"}:
                from aletheia.execution.gateway import cancel_entry_plan_order

                await asyncio.to_thread(cancel_entry_plan_order, plan.plan_id, reason=reason)
            else:
                await asyncio.to_thread(
                    repo.transition_entry_plan,
                    plan.plan_id,
                    expected_status=row["status"],
                    status="invalidated",
                    reason=reason,
                )
            continue
        if datetime.fromisoformat(plan.expires_at.replace("Z", "+00:00")) <= now:
            if row["status"] not in {"ordered", "partially_filled"}:
                await asyncio.to_thread(
                    repo.transition_entry_plan,
                    plan.plan_id,
                    expected_status=row["status"],
                    status="expired",
                    reason="entry_plan_expired",
                )
            else:
                from aletheia.execution.gateway import cancel_entry_plan_order

                await asyncio.to_thread(
                    cancel_entry_plan_order,
                    plan.plan_id,
                    reason="entry_plan_expired",
                )
            continue
        if (
            plan.order_tactic == "POST_ONLY_LIMIT"
            and row["status"] in {"ordered", "partially_filled"}
            and plan.max_wait_sec > 0
            and row.get("order_id")
        ):
            order = await asyncio.to_thread(repo.get_order, row["order_id"])
            if order is not None:
                placed_at = datetime.fromisoformat(
                    str(order["created_at"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                if (now - placed_at).total_seconds() >= plan.max_wait_sec:
                    from aletheia.execution.gateway import cancel_entry_plan_order

                    await asyncio.to_thread(
                        cancel_entry_plan_order,
                        plan.plan_id,
                        reason="entry_plan_maker_timeout",
                    )
                    continue
        if plan.venue.lower() != fast_market_data_venue_for_config(cfg):
            continue
        plans[plan.plan_id] = plan
        if plan.execution_mode != "TRIGGERED" or row["status"] != "waiting":
            continue
        if existing is not None and plan.plan_id in existing:
            engines[plan.plan_id] = existing[plan.plan_id]
            continue
        params = plan.trigger_params
        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
        engine = TriggerEngine(
            instrument=instrument,
            direction=plan.direction,
            breakout_level=float(params["breakout_level"]),
            pullback_band=(float(params["pullback_low"]), float(params["pullback_high"])),
            entry_zone=(float(params["entry_low"]), float(params["entry_high"])),
            policy=TriggerPolicy(
                max_spread_bps=float(cfg.universe.max_spread_bps),
                min_depth_10bps_usd=float(cfg.universe.min_depth_10bps_usd),
            ),
        )
        await _prewarm_market_structure(engine, instrument)
        engines[plan.plan_id] = engine
    return plans, engines


async def _cancel_open_entry_plans(reason: str) -> None:
    from aletheia.execution.gateway import cancel_entry_plan_order

    for row in await asyncio.to_thread(repo.list_watchable_entry_plans, 100):
        if row["status"] in {"ordered", "partially_filled"}:
            await asyncio.to_thread(cancel_entry_plan_order, row["id"], reason=reason)
        else:
            await asyncio.to_thread(
                repo.transition_entry_plan,
                row["id"],
                expected_status=row["status"],
                status="invalidated",
                reason=reason,
            )


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
            await _cancel_open_entry_plans(reason)
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
            plans, plan_engines = await _load_entry_plan_watchers(cfg)
            instruments = {engine.instrument for engine in (*engines.values(), *plan_engines.values())}
            instruments.update(
                InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
                for plan in plans.values()
                if plan.execution_mode == "RESTING_MAKER"
            )
            await stream.set_instruments(instruments)
            _status = {
                "running": True,
                "reason": None,
                "watched_setups": len(setups) + len(plans),
            }
            iterator = stream.events().__aiter__()
            subscribed = instruments
            loop = asyncio.get_running_loop()
            next_config_check = loop.time() + 2.0
            next_watcher_reload = loop.time() + _WATCHER_RELOAD_INTERVAL_SEC
            while not stop.is_set():
                if event_task is None:
                    event_task = asyncio.create_task(anext(iterator))
                now = loop.time()
                timeout = max(0.0, min(next_config_check, next_watcher_reload) - now)
                done, _ = await asyncio.wait({event_task}, timeout=timeout)
                now = loop.time()
                if now >= next_config_check:
                    current_cfg = await asyncio.to_thread(load_config, persist_migrations=False)
                    if _watch_config_identity(current_cfg) != _watch_config_identity(cfg):
                        cfg = current_cfg
                        break
                    next_config_check = now + 2.0
                if now >= next_watcher_reload:
                    fresh_setups, fresh_engines = await _load_watchers(cfg, engines)
                    fresh_plans, fresh_plan_engines = await _load_entry_plan_watchers(cfg, plan_engines)
                    fresh_instruments = {
                        engine.instrument for engine in (*fresh_engines.values(), *fresh_plan_engines.values())
                    }
                    fresh_instruments.update(
                        InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
                        for plan in fresh_plans.values()
                        if plan.execution_mode == "RESTING_MAKER"
                    )
                    if fresh_instruments != subscribed:
                        await stream.set_instruments(fresh_instruments)
                        subscribed = fresh_instruments
                        # A subscription generation change invalidates every
                        # rolling quote/trade/depth window; re-warm from empty.
                        fresh_setups, fresh_engines = await _load_watchers(cfg)
                        fresh_plans, fresh_plan_engines = await _load_entry_plan_watchers(cfg)
                    setups, engines = fresh_setups, fresh_engines
                    plans, plan_engines = fresh_plans, fresh_plan_engines
                    if stream.health().subscriptions_confirmed:
                        for engine in (*engines.values(), *plan_engines.values()):
                            engine.confirm_subscription()
                    _status["watched_setups"] = len(setups) + len(plans)
                    next_watcher_reload = now + _WATCHER_RELOAD_INTERVAL_SEC
                if not done:
                    continue
                try:
                    event = event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    event_task = None
                if stream.health().subscriptions_confirmed:
                    for engine in (*engines.values(), *plan_engines.values()):
                        engine.confirm_subscription()
                if event.event_type == "quote":
                    for plan_id, plan in tuple(plans.items()):
                        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
                        if instrument != event.instrument:
                            continue
                        invalidated = (plan.direction == "long" and float(event.bid) <= plan.invalidation_price) or (
                            plan.direction == "short" and float(event.ask) >= plan.invalidation_price
                        )
                        row = await asyncio.to_thread(repo.get_entry_plan, plan_id)
                        if row is None:
                            continue
                        if invalidated:
                            if row["status"] in {"ordered", "partially_filled"}:
                                from aletheia.execution.gateway import cancel_entry_plan_order

                                await asyncio.to_thread(
                                    cancel_entry_plan_order,
                                    plan_id,
                                    reason="entry_plan_price_invalidated",
                                )
                            elif row["status"] in {
                                "waiting",
                                "triggered",
                                "risk_reserved",
                            }:
                                await asyncio.to_thread(
                                    repo.transition_entry_plan,
                                    plan_id,
                                    expected_status=row["status"],
                                    status="invalidated",
                                    reason="entry_plan_price_invalidated",
                                )
                            continue
                        if plan.execution_mode == "RESTING_MAKER" and row["status"] == "waiting":
                            from aletheia.execution.entry_plan_execution import execute_entry_plan

                            await asyncio.to_thread(
                                execute_entry_plan,
                                plan_id,
                                bid=float(event.bid),
                                ask=float(event.ask),
                                observed_at=datetime.fromtimestamp(
                                    event.received_timestamp_ms / 1000, tz=timezone.utc
                                ).isoformat(),
                            )
                        elif row["status"] in {"triggered", "risk_reserved"}:
                            from aletheia.execution.entry_plan_execution import (
                                execute_entry_plan,
                            )

                            await asyncio.to_thread(
                                execute_entry_plan,
                                plan_id,
                                bid=float(event.bid),
                                ask=float(event.ask),
                                observed_at=datetime.fromtimestamp(
                                    event.received_timestamp_ms / 1000,
                                    tz=timezone.utc,
                                ).isoformat(),
                            )
                for plan_id, engine in tuple(plan_engines.items()):
                    if engine.instrument != event.instrument:
                        continue
                    ready = engine.observe(event)
                    if ready is not None:
                        from aletheia.execution.entry_plan_execution import execute_entry_plan

                        await asyncio.to_thread(
                            execute_entry_plan,
                            plan_id,
                            bid=ready.bid,
                            ask=ready.ask,
                            observed_at=ready.observed_at,
                        )
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
                        current_cfg = await asyncio.to_thread(load_config, persist_migrations=False)
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
