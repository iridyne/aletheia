"""Lifecycle-owned Fast Trigger watcher; public data only, no LLM hot path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone

from aletheia.data.symbols import InstrumentId
from aletheia.storage import repositories as repo
from aletheia.trading.entry_compat import decode_entry_plan, historical_plan_version
from aletheia.trading.entry_plan import EntryPlan
from aletheia.trading.momentum_trigger import (
    MomentumTriggerEngine,
    health_reason_requires_resync,
)
from aletheia.universe.fast_stream import (
    fast_market_data_unavailability_reason,
    fast_market_stream_for_config,
)
from aletheia.universe.fast_trigger import (
    TriggerPolicy,
    validate_fast_trigger_stream,
)
_ANCHOR_RETRACE_TRIGGER = "anchor-retrace-reclaim"

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
# Risk answers DEFER ("cannot be approved right now, retry under the Plan's own
# expiry"). Without a delay every quote re-ran a full Risk evaluation and wrote a
# decision row: 5,785 for one proposal in one hour (#3798). ponytail: one fixed
# interval for every deferral reason; per-reason intervals if a cause needs it.
_RISK_DEFER_RETRY_SEC = 15.0


async def _prewarm_market_structure(
    engine: MomentumTriggerEngine,
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
        getattr(cfg.fast_trigger, "enabled", True),
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
    if (
        not _lifecycle_ready
        and getattr(cfg.fast_trigger, "enabled", True)
    ):
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
        if repo.get_order_for_entry_plan(plan_id) is not None:
            return "order_present"
        proposal = repo.get_trade_proposal_by_entry_plan(plan_id)
        claim = None
        if proposal is not None:
            proposal_id = str(proposal["id"])
            if repo.has_order_for_proposal(proposal_id):
                return "order_present"
            if proposal["status"] in {
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


_DETERMINISTIC_SUBMISSION_FAILURES = frozenset({
    # Codes the execution boundary raises when the Plan cannot be submitted at
    # all: its running contract drifted, its provenance is incomplete, or its
    # financing cannot be resolved. Re-dispatching them at event rate cannot
    # change the outcome (#3747).
    "ai_entry_running_contract_mismatch",
    "ai_entry_generation_contract_mismatch",
    "ai_entry_plan_provenance_required",
    "ai_entry_context_invalid",
    "ai_entry_context_identity_mismatch",
    "entry_plan_submission_leverage_unavailable",
    "entry_plan_financing_incomplete",
    "entry_plan_financing_unresolved",
    "entry_plan_runtime_authority_revoked",
})


def _deterministic_submission_failure(exc: BaseException) -> str | None:
    """The bounded code when re-attempting this submission changes nothing.

    The classification is the boundary's own vocabulary; the caller uses it to
    stop dispatching one Plan for the rest of the loop instead of re-asking a
    question whose answer is already known.
    """
    code = str(exc)[:120]
    return code if code in _DETERMINISTIC_SUBMISSION_FAILURES else None


async def _terminalize_entry_plan(
    row: dict,
    *,
    status: str,
    reason: str,
    cancel_entry_plan_order,
    now: datetime,
) -> str:
    outcome = await asyncio.to_thread(
        _invalidate_pre_order_entry_plan,
        row["id"],
        expected_status=row["status"],
        terminal_status=status,
        reason=reason,
        now=now,
    )
    if outcome == "order_present":
        result = await asyncio.to_thread(cancel_entry_plan_order, row["id"], reason=reason)
        outcome = str(result.get("status") or "cancel_pending")
        if outcome in {"filled", "cancelled"}:
            terminal = "filled" if outcome == "filled" else status
            changed = await asyncio.to_thread(repo.transition_entry_plan, row["id"],
                expected_status=row["status"], status=terminal, reason=reason, now=now)
            return terminal if changed else "stale"
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
    existing: dict[str, MomentumTriggerEngine] | None = None,
) -> tuple[dict[str, EntryPlan], dict[str, MomentumTriggerEngine]]:
    from aletheia.execution.gateway import cancel_entry_plan_order

    plans: dict[str, EntryPlan] = {}
    engines: dict[str, MomentumTriggerEngine] = {}
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
            plan = decode_entry_plan(payload)
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
        if historical_plan_version(plan) is not None:
            outcome = await _terminalize_entry_plan(
                row, status="invalidated", reason="entry_plan_runtime_authority_revoked",
                cancel_entry_plan_order=cancel_entry_plan_order, now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        try:
            from aletheia.execution.entry_plan_execution import (
                require_entry_plan_runtime_authority,
            )

            await asyncio.to_thread(require_entry_plan_runtime_authority, plan)
        except (TypeError, ValueError):
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason="entry_plan_runtime_authority_revoked",
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        try:
            # `getattr` default keeps type-agnostic doubles usable; a real plan
            # exposes the property, and its failure propagates (PropertyError is
            # not AttributeError).
            getattr(plan, "submission_leverage", None)
        except ValueError as exc:
            # A plan that cannot produce a submission leverage can never be
            # submitted (#3729 §10: historical plans gain no new submission
            # authority). Watching it only makes the trigger loop re-attempt a
            # deterministic failure at event rate, so retire it with the
            # execution boundary's own code and stop watching it (#3747).
            outcome = await _terminalize_entry_plan(
                row,
                status="invalidated",
                reason=str(exc)[:120] or "entry_plan_submission_leverage_unavailable",
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
                reason=_entry_plan_expiry_reason(
                    row,
                    existing.get(plan.plan_id) if existing else None,
                ),
                cancel_entry_plan_order=cancel_entry_plan_order,
                now=now,
            )
            if _entry_plan_outcome_is_unresolved(outcome):
                plans[plan.plan_id] = plan
            continue
        if fast_market_data_unavailability_reason(
            cfg,
            plan.venue,
            plan.market_type,
        ) is not None:
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
        if getattr(plan, "capture_policy_id", None):
            from aletheia.execution.entry_plan_execution import _capture_facts
            from aletheia.trading.entry_execution_projection import resolve_entry_execution

            facts = await asyncio.to_thread(_capture_facts, plan, bid=None, ask=None, now=now)
            directive = resolve_entry_execution(plan, facts)
            if directive.command == "CANCEL_PASSIVE" or facts.cancellation_unresolved:
                await asyncio.to_thread(cancel_entry_plan_order, plan.plan_id, reason=directive.reason)
                plans[plan.plan_id] = plan
                continue
            if directive.action in {"STOP", "DONE"}:
                status = ("filled" if directive.reason == "entry_target_filled"
                          else "cancelled" if directive.action == "DONE" else "failed")
                await asyncio.to_thread(repo.transition_entry_plan, plan.plan_id,
                    expected_status=row["status"], status=status, reason=directive.reason, now=now)
                continue
        plans[plan.plan_id] = plan
        if plan.execution_mode != "TRIGGERED" or row["status"] not in {"waiting", "active"}:
            continue
        if row["status"] in {"active"}:
            if not await asyncio.to_thread(repo.entry_plan_activation_can_refresh, plan.plan_id):
                continue
            activation_row = await asyncio.to_thread(repo.get_entry_trigger_activation, row.get("trigger_activation_id"))
            if activation_row is not None:
                from aletheia.trading.entry_activation import EntryTriggerActivationV1

                activation = EntryTriggerActivationV1.model_validate_json(activation_row["activation_json"])
                monitoring = json.loads(row.get("monitoring_evidence_json") or "{}")
                if (datetime.fromisoformat(activation.expires_at) > now
                    and monitoring.get("stream_generation_id") == activation.snapshot.stream_generation_id
                    and monitoring.get("subscription_generation_id") == activation.snapshot.subscription_generation_id):
                    continue
            # Keep an already rearmed engine across reloads so its new
            # trigger sequence can finish. Triggered/faulted engines reset below.
        if existing is not None and plan.plan_id in existing:
            watching = existing[plan.plan_id]
            if watching.state != "triggered" and not health_reason_requires_resync(watching.health_reason):
                engines[plan.plan_id] = watching
                continue
            # ponytail: rebuilt on every reload while the fault persists; add a
            # resync cap plus typed monitoring degradation if logs ever show a
            # chronically anomalous feed instead of a transient frame.
            logger.warning(
                "Entry Plan %s trigger engine resynchronizing after %s",
                plan.plan_id,
                watching.health_reason or "activation_not_bound",
            )
        params = plan.trigger_params
        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
        policy = TriggerPolicy(
            max_spread_bps=float(cfg.universe.max_spread_bps),
            min_depth_10bps_usd=float(cfg.universe.min_depth_10bps_usd),
        )
        trigger_id = getattr(plan, "trigger_spec_id", None)
        try:
            if trigger_id != _ANCHOR_RETRACE_TRIGGER:
                raise ValueError("entry_plan_trigger_spec_unsupported")
            engine: MomentumTriggerEngine = MomentumTriggerEngine(
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
                trigger_spec_id=trigger_id,
            )
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
    plan_engines: dict[str, MomentumTriggerEngine],
    plans: dict[str, EntryPlan],
) -> set[InstrumentId]:
    instruments = {engine.instrument for engine in plan_engines.values()}
    instruments.update(
        InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
        for plan in plans.values()
    )
    return instruments


def _plan_required_channels(plan) -> tuple[str, ...]:
    return tuple(
        {"books5": "depth", "candle1m": "candle"}.get(channel, channel)
        for channel in plan.trigger_data_requirements
    )


def _plan_monitoring_channels(plan) -> tuple[str, ...]:
    if getattr(plan, "capture_policy_id", None):
        return tuple(dict.fromkeys(("quote", *_plan_required_channels(plan),
            *(("depth",) if plan.capture_priority in {"MAKER_PREFERRED", "FILL_FIRST"} else ()))))
    if plan.order_tactic == "POST_ONLY_LIMIT" and plan.execution_mode == "RESTING_MAKER":
        return ("quote", "trade")
    return _plan_required_channels(plan)


def _entry_plan_expiry_reason(row: dict, engine=None) -> str:
    order = repo.get_order_for_entry_plan(row["id"])
    if order is not None:
        try:
            monitoring = json.loads(row.get("monitoring_evidence_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            monitoring = {}
        if order and (
            monitoring.get("state") != "ready"
            or monitoring.get("stream_generation_id")
            != order.get("matching_stream_generation_id")
            or monitoring.get("subscription_generation_id")
            != order.get("matching_subscription_generation_id")
        ):
            return "expired_during_monitoring_gap"
        return "maker_order_unfilled_until_expiry"
    state = row.get("monitoring_state")
    if state in {None, "warming"}:
        return "monitoring_never_ready"
    if state == "queued":
        return "monitoring_queued_until_expiry"
    if state == "degraded":
        return "monitoring_stream_degraded"
    if state == "unavailable":
        try:
            evidence = json.loads(row.get("monitoring_evidence_json") or "{}")
            channel = next(
                name
                for name, facts in evidence.get("channels", {}).items()
                if facts.get("state") == "unavailable"
            )
        except (StopIteration, TypeError, ValueError, json.JSONDecodeError):
            return "monitoring_stream_unavailable"
        return f"monitoring_channel_unavailable:{channel}"
    passive = json.loads(row.get("monitoring_evidence_json") or "{}").get("passive_entry", {})
    if (row.get("execution_mode") == "RESTING_MAKER" and state == "ready" and passive.get("eligible") is False
        and passive.get("ever_eligible") is False and engine is None):
        return "passive_entry_never_became_eligible"
    if engine is not None:
        final_reason = getattr(engine, "final_requirement_reason", None)
        if final_reason:
            return f"trigger_final_requirements_unsatisfied:{final_reason}"
        return f"trigger_not_reached:{engine.state}"
    return "entry_plan_expired"


def _monitoring_ready(stream, instrument: InstrumentId, channels: tuple[str, ...]) -> bool:
    return stream.monitoring_state(instrument, channels)["state"] == "ready"


def _confirm_ready_engines(stream, plan_engines, plans) -> None:
    for plan_id, engine in plan_engines.items():
        plan = plans.get(plan_id)
        if plan is not None and _monitoring_ready(
            stream,
            engine.instrument,
            _plan_required_channels(plan),
        ):
            engine.confirm_subscription()


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


async def _submit_entry_plan(plan, event, *, activation=None, depth=None, current_market=None) -> None:
    """Reuse the earned proof while both it and the exact market facts are fresh."""
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    snapshot = activation.snapshot if activation else None
    if depth and (depth.stream_generation_id != event.stream_generation_id
                  or depth.subscription_generation_id != event.subscription_generation_id):
        depth = None
    if snapshot and (snapshot.stream_generation_id != event.stream_generation_id
                     or snapshot.subscription_generation_id != event.subscription_generation_id):
        snapshot = None
    depth_values = ({"depth_bids": depth.bids, "depth_asks": depth.asks,
        "depth_exchange_timestamp_ms": depth.exchange_timestamp_ms, "depth_received_timestamp_ms": depth.received_timestamp_ms,
        "depth_sequence": depth.sequence} if depth else {"depth_bids": snapshot.depth_bids, "depth_asks": snapshot.depth_asks,
        "depth_exchange_timestamp_ms": snapshot.depth_exchange_timestamp_ms, "depth_received_timestamp_ms": snapshot.depth_received_timestamp_ms,
        "depth_sequence": snapshot.depth_sequence} if snapshot else {})
    try:
        return await asyncio.to_thread(execute_entry_plan, plan.plan_id,
            bid=float(event.bid), ask=float(event.ask),
            observed_at=datetime.fromtimestamp(event.received_timestamp_ms / 1000, timezone.utc).isoformat(),
            source_observed_at=datetime.fromtimestamp(event.exchange_timestamp_ms / 1000, timezone.utc).isoformat(),
            stream_generation_id=str(event.stream_generation_id or ""),
            subscription_generation_id=str(event.subscription_generation_id or ""), quote_sequence=event.sequence,
            required_channels=("quote",), **depth_values,
            trigger_activation_id=activation.activation_id if activation else None,
            trigger_activation_hash=activation.activation_hash if activation else None,
            current_market=current_market)
    except ValueError as exc:
        if str(exc) not in {"post_only_would_cross_bbo", "entry_trigger_activation_expired",
                            "entry_trigger_activation_generation_mismatch", "entry_trigger_activation_required",
                            "entry_trigger_activation_monitoring_mismatch", "entry_execution_receipt_depth_required",
                            "entry_execution_receipt_depth_stale", "entry_ioc_outside_frozen_bounds"}:
            raise


async def _watch_loop(cfg) -> None:
    global _status
    backoff = 1.0
    submission_failures: dict[str, str] = {}
    risk_deferred_until: dict[str, float] = {}
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
        execution_tasks: dict[str, asyncio.Task] = {}
        # One Plan, one deterministic refusal per loop iteration: keep asking
        # rather than re-dispatching at event rate (#3747). Declared once outside
        # the loop so the dispatch closure is not a loop-variable capture.
        submission_failures.clear()
        risk_deferred_until.clear()
        latest_quotes = {}
        latest_depth = {}

        def current_market(instrument):
            return latest_quotes.get(instrument), stream.monitoring_state(instrument, ("quote",))

        def dispatch(plan_id, operation, *args, **kwargs):
            if plan_id in submission_failures:
                return
            if risk_deferred_until.get(plan_id, 0.0) > asyncio.get_running_loop().time():
                return
            task = execution_tasks.get(plan_id)
            if task is not None and not task.done():
                return
            async def run():
                try:
                    result = await operation(*args, **kwargs)
                    if isinstance(result, dict) and result.get("status") == "waiting_risk":
                        risk_deferred_until[plan_id] = asyncio.get_running_loop().time() + _RISK_DEFER_RETRY_SEC
                except Exception as exc:
                    # Bounded code, never a bare type: a swallowed `ValueError`
                    # left the cause underivable from logs (#3747).
                    logger.warning(
                        "Entry execution stopped: %s%s",
                        type(exc).__name__,
                        f":{str(exc)[:120]}" if str(exc) else "",
                    )
                    if _deterministic_submission_failure(exc):
                        submission_failures[plan_id] = str(exc)[:120]
            execution_tasks[plan_id] = asyncio.create_task(run())
        try:
            plans, plan_engines = await _load_entry_plan_watchers(cfg)
            instruments = _watched_instruments(plan_engines, plans)
            await stream.connect(instruments)
            await stream.set_instruments(instruments)
            _status = {
                "running": True,
                "reason": None,
                "watched_setups": len(plans),
                "watched_entry_plan_ids": list(plans.keys()),
                "watched_instruments": len(instruments),
                "monitoring_queued": 0,
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
                    execution_tasks = {key: task for key, task in execution_tasks.items() if not task.done()}
                    fresh_plans, fresh_plan_engines = await _load_entry_plan_watchers(cfg, plan_engines)
                    fresh_instruments = _watched_instruments(
                        fresh_plan_engines,
                        fresh_plans,
                    )
                    if {
                        item.market_type for item in fresh_instruments
                    } != {item.market_type for item in subscribed}:
                        break
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
                        fresh_plans, fresh_plan_engines = await _load_entry_plan_watchers(cfg)
                    plans, plan_engines = fresh_plans, fresh_plan_engines
                    # A plan that left the watched set (retired, filled, expired)
                    # is re-evaluated if it ever returns. Mutated in place so the
                    # dispatch closure keeps referring to one object.
                    for stale in [key for key in submission_failures if key not in plans]:
                        submission_failures.pop(stale, None)
                    _confirm_ready_engines(stream, plan_engines, plans)
                    _status["watched_setups"] = len(plans)
                    _status["watched_entry_plan_ids"] = list(plans.keys())
                    _status["watched_instruments"] = len(fresh_instruments)
                    _status["monitoring_queued"] = 0
                    next_watcher_reload = now + _WATCHER_RELOAD_INTERVAL_SEC
                if not done:
                    continue
                try:
                    event = event_task.result()
                except StopAsyncIteration:
                    break
                finally:
                    event_task = None
                if event.event_type == "quote":
                    latest_quotes[event.instrument] = event
                elif event.event_type == "depth":
                    latest_depth[event.instrument] = event
                for plan_id, plan in plans.items():
                    instrument = InstrumentId.from_parts(
                        plan.venue,
                        plan.symbol,
                        plan.market_type,
                    )
                    if instrument == event.instrument:
                        evidence = stream.monitoring_state(instrument, _plan_monitoring_channels(plan))
                        if (plan.order_tactic == "POST_ONLY_LIMIT" and event.event_type == "quote"
                            and evidence.get("state") == "ready"):
                            from aletheia.trading.entry_plan import post_only_price_is_passive

                            evidence = {**evidence, "passive_entry": {
                                "eligible": post_only_price_is_passive(
                                    plan.direction, plan.limit_price, float(event.bid), float(event.ask),
                                ),
                                "observed_at": datetime.fromtimestamp(
                                    event.received_timestamp_ms / 1000, timezone.utc,
                                ).isoformat(),
                            }}
                        await asyncio.to_thread(
                            repo.record_entry_plan_monitoring,
                            plan_id,
                            evidence,
                        )
                _confirm_ready_engines(stream, plan_engines, plans)
                if event.event_type == "quote" and _monitoring_ready(
                    stream,
                    event.instrument,
                    ("quote",),
                ):
                    for plan_id, plan in tuple(plans.items()):
                        instrument = InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type)
                        if instrument != event.instrument:
                            continue
                        row = await asyncio.to_thread(repo.get_entry_plan, plan_id)
                        if row is None or historical_plan_version(plan) is not None:
                            continue
                        invalidated = (plan.direction == "long" and float(event.bid) <= plan.invalidation_price) or (
                            plan.direction == "short" and float(event.ask) >= plan.invalidation_price
                        )
                        if invalidated:
                            if row["status"] in {
                                "waiting",
                                "active",
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
                        if plan.activation_policy == "IMMEDIATE" and row["status"] == "active":
                            dispatch(plan_id, _submit_entry_plan, plan, event, depth=latest_depth.get(instrument),
                                current_market=lambda instrument=instrument: current_market(instrument))
                        elif row["status"] in {"active"}:
                            from aletheia.trading.entry_activation import EntryTriggerActivationV1

                            activation_row = await asyncio.to_thread(
                                repo.get_entry_trigger_activation, row.get("trigger_activation_id"))
                            if activation_row is not None:
                                activation = EntryTriggerActivationV1.model_validate_json(activation_row["activation_json"])
                                dispatch(plan_id, _submit_entry_plan, plan, event, activation=activation, depth=latest_depth.get(instrument),
                                    current_market=lambda instrument=instrument: current_market(instrument))
                for plan_id, engine in tuple(plan_engines.items()):
                    if engine.instrument != event.instrument:
                        continue
                    plan = plans.get(plan_id)
                    if plan is None:
                        continue
                    ready = engine.observe(event)
                    if ready is not None:
                        from aletheia.trading.entry_activation import build_entry_trigger_activation

                        row = await asyncio.to_thread(repo.get_entry_plan, plan_id)
                        if row is None:
                            continue
                        now = datetime.now(timezone.utc)
                        try:
                            activation = build_entry_trigger_activation(plan, ready, now=now)
                            bound = await asyncio.to_thread(repo.bind_entry_trigger_activation, activation,
                                expected_activation_id=row.get("trigger_activation_id"))
                        except ValueError as exc:
                            if str(exc) not in {"entry_trigger_activation_expired", "entry_trigger_activation_market_stale"}:
                                raise
                            continue
                        if bound:
                            dispatch(plan_id, _submit_entry_plan, plan, event, activation=activation, depth=latest_depth.get(event.instrument),
                                current_market=lambda instrument=event.instrument: current_market(instrument))
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
            await asyncio.gather(*execution_tasks.values(), return_exceptions=True)
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
