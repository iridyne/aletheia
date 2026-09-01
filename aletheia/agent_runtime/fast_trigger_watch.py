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
    AuditEvent,
    ConditionalEntryAuthorization,
    UniverseSetup,
)
from aletheia.trading.entry_plan import EntryPlan
from aletheia.trading.momentum_trigger import MomentumTriggerEngineV2
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
_MOMENTUM_V1_TRIGGER = ("momentum-retrace-reclaim", "v1")
_MOMENTUM_V2_TRIGGER = ("momentum-retrace-reclaim", "v2")

logger = logging.getLogger("aletheia.fast_trigger_watch")

_watch_task: asyncio.Task | None = None
_stop_event: asyncio.Event | None = None
_lifecycle_ready = False
_status: dict[str, object] = {
    "running": False,
    "reason": "fast_trigger_disabled",
    "watched_setups": 0,
    "watched_entry_plan_ids": [],
}
_WATCHER_RELOAD_INTERVAL_SEC = 2.0


async def _prewarm_market_structure(
    engine: TriggerEngine | MomentumTriggerEngineV2,
    instrument: InstrumentId,
) -> None:
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
def _entry_plan_trigger_identity(plan) -> tuple[str, str]:
    return (
        str(getattr(plan, "trigger_spec_id", None) or _MOMENTUM_V1_TRIGGER[0]),
        str(getattr(plan, "trigger_spec_version", None) or _MOMENTUM_V1_TRIGGER[1]),
    )


def _is_momentum_v2_trigger_plan(plan) -> bool:
    return _entry_plan_trigger_identity(plan) == _MOMENTUM_V2_TRIGGER
async def _shadow_plan_terminal_state(plan_id: str) -> str | None:
    """Return a durable v2 terminal fact; None means the audit store was unavailable."""

    try:
        if await asyncio.to_thread(
            repo.has_audit_event,
            event_type="momentum_v2_trigger_observed",
            entity_id=plan_id,
        ):
            return "triggered"
        if await asyncio.to_thread(
            repo.has_audit_event,
            event_type="momentum_v2_state_transition",
            entity_id=plan_id,
        ):
            events = await asyncio.to_thread(
                repo.list_audit_events,
                limit=100,
                event_type="momentum_v2_state_transition",
            )
            if any(
                row.get("entity_id") == plan_id
                and '"state":"invalidated"' in str(row.get("after_state") or "")
                for row in events
            ):
                return "invalidated"
        return ""
    except Exception:
        logger.warning("Momentum v2 Shadow audit state unavailable for %s", plan_id, exc_info=True)
        return None


def _json_state(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _persist_shadow_audit(event: AuditEvent) -> None:
    repo.insert_audit_event_once(event)
    try:
        from aletheia.files.jsonl import write_audit

        if not write_audit(event.model_dump(), mirror_db=False, dedup_id=event.id):
            logger.warning("Shadow audit JSONL write unavailable: %s", event.id)
    except Exception:
        logger.warning("Shadow audit JSONL write failed: %s", event.id, exc_info=True)


async def _observe_entry_plan_engine(plan, engine, event):
    """Observe one plan and retain v2 state/trigger facts without execution."""

    before_path: tuple[str, ...] = tuple(getattr(engine, "state_path", ()))
    ready = engine.observe(event)
    if not _is_momentum_v2_trigger_plan(plan):
        return ready

    params = plan.trigger_params
    geometry = {
        "geometry_version": params.get("geometry_version"),
        "geometry_fingerprint": params.get("geometry_fingerprint"),
    }
    state_path: tuple[str, ...] = tuple(engine.state_path)
    for index in range(len(before_path), len(state_path)):
        previous = state_path[index - 1] if index > 0 else "armed"
        current = state_path[index]
        event_id = f"momentum-v2-state:{plan.plan_id}:{index}:{event.sequence}:{current}"
        audit = AuditEvent(
            id=event_id,
            timestamp=datetime.fromtimestamp(
                event.received_timestamp_ms / 1000,
                tz=timezone.utc,
            ).isoformat(),
            actor="system",
            source="fast_trigger_shadow",
            event_type="momentum_v2_state_transition",
            entity_type="entry_plan",
            entity_id=plan.plan_id,
            before_state=_json_state(
                {"state": previous, "state_path": list(state_path[:index])}
            ),
            after_state=_json_state(
                {
                    **geometry,
                    "state": current,
                    "state_path": list(state_path[: index + 1]),
                    "event_type": event.event_type,
                    "event_sequence": event.sequence,
                    "event_received_at_ms": event.received_timestamp_ms,
                    "event_exchange_at_ms": event.exchange_timestamp_ms,
                }
            ),
            message=f"Shadow Momentum TriggerSpec v2 state transition {previous}->{current}",
        )
        await asyncio.to_thread(_persist_shadow_audit, audit)

    if ready is None:
        return None
    trigger = dict(ready.entry_trigger)
    event_id = (
        f"momentum-v2-trigger:{plan.plan_id}:{ready.quote_sequence}:"
        f"{ready.trade_sequence}:{ready.depth_sequence}:"
        f"{trigger.get('geometry_fingerprint', '')}"
    )
    audit = AuditEvent(
        id=event_id,
        timestamp=ready.observed_at,
        actor="system",
        source="fast_trigger_shadow",
        event_type="momentum_v2_trigger_observed",
        entity_type="entry_plan",
        entity_id=plan.plan_id,
        before_state=_json_state({"state": "reclaim"}),
        after_state=_json_state(
            {
                **geometry,
                "state": "triggered",
                "entry_price": ready.entry_price,
                "bid": ready.bid,
                "ask": ready.ask,
                "spread_bps": ready.spread_bps,
                "depth_10bps_usd": ready.depth_10bps_usd,
                "taker_buy_ratio": ready.taker_buy_ratio,
                "quote_sequence": ready.quote_sequence,
                "trade_sequence": ready.trade_sequence,
                "depth_sequence": ready.depth_sequence,
                "entry_trigger": trigger,
            }
        ),
        message="Shadow Momentum TriggerSpec v2 produced a trigger; no execution authority granted.",
    )
    await asyncio.to_thread(_persist_shadow_audit, audit)
    return ready


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
        _status = {"running": False, "reason": reason, "watched_setups": 0, "watched_entry_plan_ids": []}
        return None
    _stop_event = asyncio.Event()
    _watch_task = loop.create_task(_watch_loop(cfg), name="fast_trigger_watch")
    _status = {"running": True, "reason": None, "watched_setups": 0, "watched_entry_plan_ids": []}
    return _watch_task


def _configured_block_reason(cfg) -> str | None:
    reason = validate_fast_trigger_stream(cfg)
    if reason is None and (cfg.runtime.paused or cfg.runtime.kill_switch_active):
        reason = "fast_trigger_runtime_paused" if cfg.runtime.paused else "fast_trigger_kill_switch_active"
    return reason


def _watch_config_identity(cfg) -> tuple[object, ...]:
    return (
        cfg.fast_trigger.mode,
        cfg.fast_trigger.max_setups,
        cfg.fast_trigger.authorization_ttl_minutes,
        cfg.fast_trigger.trigger_behavior_version,
        cfg.agent.permission_mode,
        cfg.exchange.paper_execution_mode,
        str(cfg.market_data.ccxt_exchange or "").strip().lower(),
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
            "watched_entry_plan_ids": [],
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
    reason = _configured_block_reason(cfg)
    cleanup = await _cancel_open_entry_plans(reason) if reason else None
    task = start_fast_trigger_watch(loop, cfg)
    status = fast_trigger_watch_status()
    if cleanup is not None:
        status["cancellation_cleanup"] = cleanup
        _status["cancellation_cleanup"] = cleanup
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


async def _handle_entry_plan_integrity_failure(
    row: dict,
    *,
    reason: str,
    cancel_entry_plan_order,
) -> None:
    """Keep unresolved execution authority under Gateway supervision."""

    await _terminalize_entry_plan(
        row,
        status="failed",
        reason=reason,
        cancel_entry_plan_order=cancel_entry_plan_order,
        now=datetime.now(timezone.utc),
    )


def _invalidate_pre_order_entry_plan(
    plan_id: str,
    *,
    expected_status: str,
    reason: str,
    now: datetime,
    terminal_status: str = "invalidated",
) -> str:
    """Invalidate before Order creation and release stale pre-Order authority."""

    with repo.transaction(immediate=True):
        current = repo.get_entry_plan(plan_id)
        if current is None or current["status"] != expected_status:
            return "stale"
        proposal = repo.get_trade_proposal_by_entry_plan(plan_id)
        claim = None
        if proposal is not None:
            proposal_id = str(proposal["id"])
            if repo.has_order_for_proposal(proposal_id):
                return "order_present"
            if proposal["execution_mode"] != "paper_internal" and proposal["status"] in {
                "executing",
                "submission_unknown",
                "materialization_missing",
                "executed",
            }:
                # An expired external owner may already have crossed the exchange
                # boundary; only Gateway reconciliation may resolve that exposure.
                return "external_execution"
            claim = repo.get_proposal_execution_claim(proposal_id)
            if claim is not None:
                try:
                    lease_expires_at = datetime.fromisoformat(
                        str(claim["lease_expires_at"]).replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                except (TypeError, ValueError):
                    return "active_claim"
                if lease_expires_at > now:
                    return "active_claim"
        if not repo.transition_entry_plan(
            plan_id,
            expected_status=expected_status,
            status=terminal_status,
            reason=reason,
            now=now,
        ):
            return "stale"
        if proposal is not None:
            proposal_id = str(proposal["id"])
            repo.release_entry_risk_reservation(proposal_id)
            if claim is not None:
                repo.release_proposal_execution_claim(
                    proposal_id,
                    str(claim["owner_token"]),
                )
    return terminal_status


async def _terminalize_entry_plan(
    row: dict,
    *,
    status: str,
    reason: str,
    cancel_entry_plan_order,
    now: datetime,
) -> str:
    if row["status"] in {"ordered", "partially_filled"}:
        result = await asyncio.to_thread(
            cancel_entry_plan_order,
            row["id"],
            reason=reason,
        )
        return str(result.get("status") or "cancel_pending")
    outcome = await asyncio.to_thread(
        _invalidate_pre_order_entry_plan,
        row["id"],
        expected_status=row["status"],
        terminal_status=status,
        reason=reason,
        now=now,
    )
    if outcome == "order_present":
        result = await asyncio.to_thread(
            cancel_entry_plan_order,
            row["id"],
            reason=reason,
        )
        return str(result.get("status") or "cancel_pending")
    return outcome


def _entry_plan_outcome_is_unresolved(outcome: str) -> bool:
    return outcome not in {
        "invalidated",
        "expired",
        "failed",
        "cancelled",
        "filled",
    }


async def _load_entry_plan_watchers(
    cfg,
    existing: dict[str, TriggerEngine | MomentumTriggerEngineV2] | None = None,
) -> tuple[dict[str, EntryPlan], dict[str, TriggerEngine | MomentumTriggerEngineV2]]:
    from aletheia.execution.gateway import cancel_entry_plan_order

    plans: dict[str, EntryPlan] = {}
    engines: dict[str, TriggerEngine | MomentumTriggerEngineV2] = {}
    monitoring_limit = int(cfg.fast_trigger.max_setups)
    now = datetime.now(timezone.utc)
    for row in await asyncio.to_thread(repo.list_watchable_entry_plans, None):
        payload = str(row["plan_json"])
        if hashlib.sha256(payload.encode()).hexdigest() != row["plan_hash"]:
            await _handle_entry_plan_integrity_failure(
                row,
                reason="entry_plan_hash_mismatch",
                cancel_entry_plan_order=cancel_entry_plan_order,
            )
            continue
        try:
            plan = EntryPlan.model_validate_json(payload)
        except Exception:
            logger.error("Entry Plan %s failed stored-contract validation", row["id"])
            await _handle_entry_plan_integrity_failure(
                row,
                reason="entry_plan_contract_invalid",
                cancel_entry_plan_order=cancel_entry_plan_order,
            )
            continue
        generation = await asyncio.to_thread(
            repo.get_candidate_planning_generation,
            plan.planning_generation_id,
        )
        if generation is None or not bool(generation["is_current"]):
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason="incompatible_planning_generation",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
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
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason=reason,
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        if datetime.fromisoformat(plan.expires_at.replace("Z", "+00:00")) <= now:
            outcome = await _terminalize_entry_plan(
                row,
                status="expired",
                reason="entry_plan_expired",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        if plan.venue.lower() != fast_market_data_venue_for_config(cfg):
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason="entry_plan_execution_venue_unavailable",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        if (
            plan.order_tactic == "POST_ONLY_LIMIT"
            and row["status"] in {"ordered", "partially_filled"}
            and plan.max_wait_sec > 0
            and row.get("order_id")
        ):
            order = await asyncio.to_thread(repo.get_order, row["order_id"])
            if order is not None:
                placed_at = datetime.fromisoformat(str(order["created_at"]).replace("Z", "+00:00")).astimezone(
                    timezone.utc
                )
                if (now - placed_at).total_seconds() >= plan.max_wait_sec:
                    from aletheia.execution.gateway import cancel_entry_plan_order

                    await asyncio.to_thread(
                        cancel_entry_plan_order,
                        plan.plan_id,
                        reason="entry_plan_maker_timeout",
                    )
                    continue
        if len(plans) >= monitoring_limit:
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason="entry_plan_monitoring_capacity_unavailable",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                # Never abandon unresolved execution authority at the cap.
                plans[plan.plan_id] = plan
            continue
        plans[plan.plan_id] = plan
        if _is_momentum_v2_trigger_plan(plan):
            shadow_state = await _shadow_plan_terminal_state(plan.plan_id)
            if shadow_state is None:
                logger.warning("Skipping Momentum v2 plan %s until Shadow audit is readable", plan.plan_id)
                continue
            if shadow_state == "triggered":
                continue
            if shadow_state == "invalidated":
                outcome = await _terminalize_entry_plan(
                    row,
                    status="invalidated",
                    reason="entry_plan_price_invalidated",
                    cancel_entry_plan_order=cancel_entry_plan_order,
                    now=now,
                )
                if _entry_plan_outcome_is_unresolved(outcome):
                    plans[plan.plan_id] = plan
                else:
                    plans.pop(plan.plan_id, None)
                continue
        if plan.execution_mode != "TRIGGERED" or row["status"] != "waiting":
            continue
        if existing is not None and plan.plan_id in existing:
            engines[plan.plan_id] = existing[plan.plan_id]
            continue
        params = plan.trigger_params
        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
        policy = TriggerPolicy(
            max_spread_bps=float(cfg.universe.max_spread_bps),
            min_depth_10bps_usd=float(cfg.universe.min_depth_10bps_usd),
        )
        trigger_identity = _entry_plan_trigger_identity(plan)
        try:
            if trigger_identity == _MOMENTUM_V2_TRIGGER:
                engine: TriggerEngine | MomentumTriggerEngineV2 = MomentumTriggerEngineV2(
                    instrument=instrument,
                    direction=plan.direction,
                    breakout_level=float(params["breakout_level"]),
                    pullback_band=(float(params["pullback_low"]), float(params["pullback_high"])),
                    sweep_band=(float(params["sweep_low"]), float(params["sweep_high"])),
                    entry_zone=(float(params["entry_low"]), float(params["entry_high"])),
                    invalidation_price=float(params["invalidation_price"]),
                    atr_abs=float(params["atr_abs"]),
                    geometry_version=str(params["geometry_version"]),
                    geometry_fingerprint=str(params["geometry_fingerprint"]),
                    policy=policy,
                )
            elif trigger_identity == _MOMENTUM_V1_TRIGGER:
                engine = TriggerEngine(
                    instrument=instrument,
                    direction=plan.direction,
                    breakout_level=float(params["breakout_level"]),
                    pullback_band=(float(params["pullback_low"]), float(params["pullback_high"])),
                    entry_zone=(float(params["entry_low"]), float(params["entry_high"])),
                    policy=policy,
                )
            else:
                raise ValueError("entry_plan_trigger_spec_unsupported")
            await _prewarm_market_structure(engine, instrument)
        except (KeyError, TypeError, ValueError, OverflowError):
            logger.error(
                "Entry Plan %s has an unsupported or invalid TriggerSpec",
                plan.plan_id,
                exc_info=True,
            )
            outcome = await _terminalize_entry_plan(
                row,
                status="failed",
                reason="entry_plan_trigger_contract_invalid",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            else:
                plans.pop(plan.plan_id, None)
            continue
        engines[plan.plan_id] = engine
    return plans, engines


def _watched_instruments(
    setup_engines: dict[str, TriggerEngine],
    plan_engines: dict[str, TriggerEngine | MomentumTriggerEngineV2],
    plans: dict[str, EntryPlan],
) -> set[InstrumentId]:
    instruments = {engine.instrument for engine in (*setup_engines.values(), *plan_engines.values())}
    instruments.update(
        InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
        for plan in plans.values()
        if plan.order_tactic == "POST_ONLY_LIMIT"
    )
    return instruments


async def _cancel_open_entry_plans(reason: str) -> dict[str, int]:
    from aletheia.execution.gateway import cancel_entry_plan_order

    cleanup = {"checked": 0, "terminalized": 0, "unresolved": 0, "remaining_unattempted": 0}
    through = await asyncio.to_thread(repo.watchable_entry_plan_high_watermark)
    if through is None:
        return cleanup

    now = datetime.now(timezone.utc)
    after: tuple[str, str] | None = None
    while True:
        rows = await asyncio.to_thread(
            repo.list_watchable_entry_plans_page,
            after=after,
            through=through,
            limit=100,
        )
        if not rows:
            return cleanup
        for row in rows:
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason=reason,
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            cleanup["checked"] += 1
            key = "unresolved" if _entry_plan_outcome_is_unresolved(outcome) else "terminalized"
            cleanup[key] += 1
        after = (str(rows[-1]["created_at"]), str(rows[-1]["id"]))


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
            cleanup = await _cancel_open_entry_plans(reason)
            _status = {
                "running": False,
                "reason": reason,
                "watched_setups": 0,
                "watched_entry_plan_ids": [],
                "cancellation_cleanup": cleanup,
            }
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
            instruments = _watched_instruments(engines, plan_engines, plans)
            await stream.set_instruments(instruments)
            _status = {
                "running": True,
                "reason": None,
                "watched_setups": len(setups) + len(plans),
                "watched_entry_plan_ids": list(plans.keys())[: int(cfg.fast_trigger.max_setups)],
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
                    fresh_instruments = _watched_instruments(
                        fresh_engines,
                        fresh_plan_engines,
                        fresh_plans,
                    )
                    if fresh_instruments != subscribed:
                        if event_task is not None:
                            if event_task.done():
                                try:
                                    event_task.result()
                                except StopAsyncIteration:
                                    pass
                            else:
                                event_task.cancel()
                                await asyncio.gather(event_task, return_exceptions=True)
                            event_task = None
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
                    _status["watched_entry_plan_ids"] = list(plans.keys())[: int(cfg.fast_trigger.max_setups)]
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
                if event.event_type == "quote" and stream.health().subscriptions_confirmed:
                    for plan_id, plan in tuple(plans.items()):
                        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
                        if instrument != event.instrument:
                            continue
                        row = await asyncio.to_thread(repo.get_entry_plan, plan_id)
                        if row is None:
                            continue
                        if _is_momentum_v2_trigger_plan(plan):
                            continue
                        invalidated = (plan.direction == "long" and float(event.bid) <= plan.invalidation_price) or (
                            plan.direction == "short" and float(event.ask) >= plan.invalidation_price
                        )
                        if invalidated:
                            if row["status"] in {
                                "waiting",
                                "triggered",
                                "risk_reserved",
                                "ordered",
                                "partially_filled",
                            }:
                                from aletheia.execution.gateway import cancel_entry_plan_order

                                await _terminalize_entry_plan(
                                    row,
                                    status="invalidated",
                                    reason="entry_plan_price_invalidated",
                                    cancel_entry_plan_order=cancel_entry_plan_order,
                                    now=datetime.now(timezone.utc),
                                )
                            continue
                        if plan.execution_mode == "RESTING_MAKER" and row["status"] == "waiting":
                            from aletheia.execution.entry_plan_execution import execute_entry_plan

                            try:
                                await asyncio.to_thread(
                                    execute_entry_plan,
                                    plan_id,
                                    bid=float(event.bid),
                                    ask=float(event.ask),
                                    observed_at=datetime.fromtimestamp(
                                        event.received_timestamp_ms / 1000, tz=timezone.utc
                                    ).isoformat(),
                                    source_observed_at=datetime.fromtimestamp(
                                        event.exchange_timestamp_ms / 1000, tz=timezone.utc
                                    ).isoformat(),
                                )
                            except ValueError as exc:
                                if str(exc) != "post_only_would_cross_bbo":
                                    raise
                        elif row["status"] in {"triggered", "risk_reserved"}:
                            from aletheia.execution.entry_plan_execution import execute_entry_plan

                            await asyncio.to_thread(
                                execute_entry_plan,
                                plan_id,
                                bid=float(event.bid),
                                ask=float(event.ask),
                                observed_at=datetime.fromtimestamp(
                                    event.received_timestamp_ms / 1000,
                                    tz=timezone.utc,
                                ).isoformat(),
                                source_observed_at=datetime.fromtimestamp(
                                    event.exchange_timestamp_ms / 1000,
                                    tz=timezone.utc,
                                ).isoformat(),
                            )
                for plan_id, engine in tuple(plan_engines.items()):
                    if engine.instrument != event.instrument:
                        continue
                    plan = plans.get(plan_id)
                    if plan is None:
                        continue
                    ready = await _observe_entry_plan_engine(plan, engine, event)
                    if _is_momentum_v2_trigger_plan(plan) and engine.state == "invalidated":
                        from aletheia.execution.gateway import cancel_entry_plan_order

                        row = await asyncio.to_thread(repo.get_entry_plan, plan_id)
                        if row is not None and row["status"] == "waiting":
                            await _terminalize_entry_plan(
                                row,
                                status="invalidated",
                                reason="entry_plan_price_invalidated",
                                cancel_entry_plan_order=cancel_entry_plan_order,
                                now=datetime.now(timezone.utc),
                            )
                        continue
                    if _is_momentum_v2_trigger_plan(plan) and ready is not None:
                        logger.info(
                            "fast trigger treatment plan=%s result=trigger_observed",
                            plan_id,
                        )
                    if ready is not None:
                        from aletheia.execution.entry_plan_execution import execute_entry_plan

                        await asyncio.to_thread(
                            execute_entry_plan,
                            plan_id,
                            bid=ready.bid,
                            ask=ready.ask,
                            observed_at=datetime.fromtimestamp(
                                ready.quote_received_timestamp_ms / 1000,
                                tz=timezone.utc,
                            ).isoformat(),
                            source_observed_at=datetime.fromtimestamp(
                                ready.quote_exchange_timestamp_ms / 1000,
                                tz=timezone.utc,
                            ).isoformat(),
                            trigger_evidence=ready.entry_trigger,
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
                "watched_entry_plan_ids": [],
            }
            logger.warning("Fast Trigger stream restarting: %s", type(exc).__name__, exc_info=True)
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
    _status = {
        "running": False,
        "reason": "fast_trigger_stream_closed",
        "watched_setups": 0,
        "watched_entry_plan_ids": [],
    }
