"""Durable exact-1m protective barriers for new ``paper_internal`` positions.

Only a closed candle from the activation-time frozen instrument may authorize a
trigger.  The candle records evidence; the existing routed close path obtains a
fresh mark and remains the sole source of the simulated fill.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from aletheia.data.symbols import InstrumentId, to_canonical
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    PositionExitPlanVersion,
    PositionProtectionState,
    PositionProtectionTrigger,
)

logger = logging.getLogger("aletheia.protective_barrier")
_UNSET = object()
_TRAILING_QUALIFICATION_VERSION = "paper-dynamic-profit-protection/v2"
_TRAILING_TREATMENT_ID = "atr1.5-balance1.5-completed1m/v1"
_TRAILING_TREATMENT_BPS = 9_000
_TRAILING_RECOVERY_WINDOW_BARS = 30
_TRAILING_RECOVERY_THRESHOLD_PCT = 1.0


def _trailing_qualification_assignment(position_id: str) -> dict:
    """Return the immutable 90/10 treatment/control assignment for one Position."""
    digest = hashlib.sha256(
        f"{_TRAILING_QUALIFICATION_VERSION}:{position_id}".encode("utf-8")
    ).hexdigest()
    bucket = int(digest[:8], 16) % 10_000
    return {
        "policy_version": _TRAILING_QUALIFICATION_VERSION,
        "treatment_id": _TRAILING_TREATMENT_ID,
        "assignment_unit": position_id,
        "assignment_hash": digest,
        "arm": "treatment" if bucket < _TRAILING_TREATMENT_BPS else "control",
        "treatment_allocation_bps": _TRAILING_TREATMENT_BPS,
        "control_holdout_bps": 10_000 - _TRAILING_TREATMENT_BPS,
        "recovery_window_bars": _TRAILING_RECOVERY_WINDOW_BARS,
        "recovery_threshold_pct": _TRAILING_RECOVERY_THRESHOLD_PCT,
    }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _physical_market(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "spot":
        return "spot"
    if normalized in {"perp", "usdt_futures"}:
        return "perp"
    raise ValueError(f"unsupported protection market_type: {normalized or 'missing'}")


def _plan_payload(
    *,
    stop_price: float | None,
    take_profit_price: float | None,
    exit_limit_price: float | None,
    exit_limit_source: str | None,
    trailing: dict | None = None,
) -> str:
    return json.dumps(
        {
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "exit_limit_price": exit_limit_price,
            "exit_limit_source": exit_limit_source,
            "trailing": trailing,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def activate_new_paper_position(position_id: str) -> dict:
    """Atomically activate immutable protection for one newly inserted Position."""

    position = repo.get_position(position_id)
    if position is None:
        raise ValueError("protection activation requires an open Position")
    return _activate_paper_position(
        position_id,
        effective_at=str(position.get("opened_at") or ""),
        provenance="paper_entry_protection_v2",
        assign_shadow=True,
    )


def _activate_paper_position(
    position_id: str,
    *,
    effective_at: str,
    provenance: str,
    assign_shadow: bool,
) -> dict:
    """Activate one exact-instrument cursor without applying a plan backward."""

    from aletheia.config.loader import load_config
    from aletheia.data.price_feed import protective_price_venue
    from aletheia.portfolio.exit_plan import effective_exit_plan

    position = repo.get_position(position_id)
    if position is None or position.get("status") != "open":
        raise ValueError("protection activation requires an open Position")
    if position.get("execution_mode") != "paper_internal":
        return {"activated": False, "reason": "not_paper_internal"}
    existing = repo.get_position_protection_state(position_id)
    if existing is not None:
        return {"activated": False, "reason": "already_activated"}
    if position.get("exit_plan_effective_at"):
        return {"activated": False, "reason": "immutable_plan_without_state"}

    cfg = load_config()
    resolved = effective_exit_plan(position, cfg)
    if (
        assign_shadow
        and resolved.trailing is None
        and getattr(cfg.trading, "trail_enabled", False)
        and position.get("trade_school") == "short_term"
        and position.get("source") == "native"
    ):
        qualification = _trailing_qualification_assignment(position_id)
        treatment = qualification["arm"] == "treatment"
        resolved.trailing = {
            "enabled": treatment,
            "mode": "atr" if treatment else "off",
            "distance": float(cfg.trading.trail_atr_mult),
            "fallback_pct": float(cfg.trading.trail_pct),
            "activation_profit_pct": float(cfg.trading.breakeven_trigger_pct),
            "floor_at_breakeven": True,
            "floor_mode": "net_balance",
            "observation": "completed_1m",
            "cost_model_version": "paper-net-balance-v1",
            "exit_fee_rate": float(cfg.trading.execution_fee_pct) / 100.0,
            "slippage_bps": float(cfg.trading.slippage_bps),
            "qualification": qualification,
        }
    if resolved.stop_price is None and resolved.target_price is None:
        # Paper historically permits an approved native Proposal without an SL/TP.
        # There is no barrier to evaluate, so preserve that entry contract without
        # fabricating an ExitPlan, venue identity, or cursor.
        return {"activated": False, "reason": "no_resolved_protective_level"}
    venue = protective_price_venue(
        "paper_internal",
        configured_data_venue=cfg.market_data.ccxt_exchange,
    )
    if not venue:
        raise ValueError("paper protection venue is unavailable")

    activated_ms = _timestamp_ms(effective_at)
    next_bar_open_ms = ((activated_ms + 59_999) // 60_000) * 60_000
    created_at = _now().isoformat()
    payload = _plan_payload(
        stop_price=resolved.stop_price,
        take_profit_price=resolved.target_price,
        exit_limit_price=position.get("exit_limit_price"),
        exit_limit_source=position.get("exit_limit_source"),
        trailing=resolved.trailing,
    )
    plan = PositionExitPlanVersion(
        position_id=position_id,
        version=1,
        effective_at=effective_at,
        stop_price=resolved.stop_price,
        take_profit_price=resolved.target_price,
        exit_limit_price=position.get("exit_limit_price"),
        exit_limit_source=position.get("exit_limit_source"),
        plan_json=payload,
        plan_hash=repo.hash_position_exit_plan_json(payload),
        provenance=provenance,
        created_at=created_at,
    )
    state = PositionProtectionState(
        position_id=position_id,
        venue=str(venue).lower(),
        symbol=to_canonical(position["symbol"]),
        market_type=_physical_market(position.get("market_type")),
        activated_at=effective_at,
        next_bar_open_ms=next_bar_open_ms,
        created_at=created_at,
        updated_at=created_at,
    )
    with repo.transaction(immediate=True):
        repo.append_position_exit_plan_version(plan, expected_effective_at=None)
        repo.update_position(
            position_id,
            stop_price=resolved.stop_price,
            take_profit_price=resolved.target_price,
            stop_source=resolved.provenance,
            exit_plan_version=1,
            trailing_json=(
                json.dumps(resolved.trailing, sort_keys=True)
                if resolved.trailing is not None
                else None
            ),
        )
        repo.insert_position_protection_state(state)
        if assign_shadow and resolved.trailing is None:
            try:
                from aletheia.analytics.exit_breakeven_shadow import (
                    assign_new_protected_position,
                )

                assign_new_protected_position(position_id)
            except Exception as exc:
                # The experiment is read-only evidence.  It must never make a valid
                # protected Paper entry fail; absence remains visible as no assignment.
                logger.warning(
                    "exit breakeven shadow assignment unavailable for %s: %s",
                    position_id,
                    type(exc).__name__,
                )
    return {"activated": True, "position_id": position_id, "next_bar_open_ms": next_bar_open_ms}


def adopt_existing_paper_positions(*, limit: int = 200) -> dict:
    """Forward-activate unprotected Paper Positions from the current instant.

    Adoption never backdates the immutable plan or cursor and deliberately does
    not enroll a mid-hold Position into the prospective exit-shadow cohort.
    """

    adoption_at = _now().isoformat()
    bounded_limit = max(0, min(int(limit), 500))
    summary = {
        "checked": 0,
        "adopted": 0,
        "already_protected": 0,
        "skipped": 0,
        "errors": [],
    }
    for position in repo.list_all_open_positions()[:bounded_limit]:
        summary["checked"] += 1
        if position.get("execution_mode") != "paper_internal":
            summary["skipped"] += 1
            continue
        try:
            result = _activate_paper_position(
                str(position["id"]),
                effective_at=adoption_at,
                provenance="paper_forward_adoption_v1",
                assign_shadow=False,
            )
        except Exception as exc:
            logger.exception(
                "forward protection adoption failed position=%s",
                position.get("id"),
            )
            summary["errors"].append({
                "position_id": position.get("id"),
                "code": "protective_forward_adoption_failed",
                "error_type": type(exc).__name__,
                "message": str(exc)[:200],
            })
            continue
        if result.get("activated") is True:
            summary["adopted"] += 1
        elif result.get("reason") == "already_activated":
            summary["already_protected"] += 1
        elif result.get("reason") == "immutable_plan_without_state":
            summary["errors"].append({
                "position_id": position.get("id"),
                "code": "protective_forward_adoption_integrity_conflict",
                "error_type": "PositionProtectionConflict",
                "message": "immutable ExitPlan exists without protection state",
            })
        else:
            summary["skipped"] += 1
    return summary


def persist_position_exit_plan(
    position_id: str,
    plan,
    *,
    provenance: str,
    expected_version: int | None = None,
    exit_limit_price: object = _UNSET,
    exit_limit_source: object = _UNSET,
    extra_position_updates: dict | None = None,
    effective_at: str | None = None,
):
    """Update intent, rejecting a protected caller whose base version is stale."""

    from aletheia.portfolio.exit_plan import ExitPlan, save_exit_plan

    position = repo.get_position(position_id)
    if position is None or position.get("status") != "open":
        raise ValueError("ExitPlan update requires an open Position")
    state = repo.get_position_protection_state(position_id)
    if state is None:
        updates = dict(extra_position_updates or {})
        if exit_limit_price is not _UNSET:
            updates["exit_limit_price"] = exit_limit_price
        if exit_limit_source is not _UNSET:
            updates["exit_limit_source"] = exit_limit_source
        with repo.transaction(immediate=True):
            save_exit_plan(position_id, plan)
            if updates:
                repo.update_position(position_id, **updates)
        return plan
    if state["status"] not in {"active", "gap"}:
        raise ValueError(f"ExitPlan cannot change after protection {state['status']}")

    current = repo.get_current_position_exit_plan(position_id)
    if current is None:
        raise ValueError("protected Position has no current immutable ExitPlan")
    caller_version = int(plan.version) if expected_version is None else int(expected_version)
    if caller_version != int(current["version"]):
        raise repo.PositionProtectionConflict(
            "ExitPlan caller version compare-and-set failed"
        )
    effective = (
        _now()
        if effective_at is None
        else datetime.fromisoformat(effective_at.replace("Z", "+00:00"))
    )
    if effective.tzinfo is None:
        raise ValueError("ExitPlan effective_at requires a timezone")
    previous = datetime.fromisoformat(str(current["effective_at"]).replace("Z", "+00:00"))
    if effective <= previous:
        effective = previous + timedelta(microseconds=1)
    effective_at = effective.isoformat()
    resolved_limit = position.get("exit_limit_price") if exit_limit_price is _UNSET else exit_limit_price
    resolved_source = position.get("exit_limit_source") if exit_limit_source is _UNSET else exit_limit_source
    version = int(current["version"]) + 1
    stored_plan = ExitPlan(
        stop_price=plan.stop_price,
        target_price=plan.target_price,
        expected_horizon_hours=plan.expected_horizon_hours,
        trailing=plan.trailing,
        thesis_invalidation=plan.thesis_invalidation,
        provenance=plan.provenance,
        version=version,
    )
    payload = _plan_payload(
        stop_price=stored_plan.stop_price,
        take_profit_price=stored_plan.target_price,
        exit_limit_price=resolved_limit,
        exit_limit_source=resolved_source,
        trailing=stored_plan.trailing,
    )
    immutable = PositionExitPlanVersion(
        position_id=position_id,
        version=version,
        effective_at=effective_at,
        stop_price=stored_plan.stop_price,
        take_profit_price=stored_plan.target_price,
        exit_limit_price=resolved_limit,
        exit_limit_source=resolved_source,
        plan_json=payload,
        plan_hash=repo.hash_position_exit_plan_json(payload),
        provenance=provenance,
        created_at=effective_at,
    )
    with repo.transaction(immediate=True):
        repo.append_position_exit_plan_version(
            immutable,
            expected_effective_at=position.get("exit_plan_effective_at"),
        )
        repo.update_position(position_id, **stored_plan.to_columns())
        updates = dict(extra_position_updates or {})
        if exit_limit_price is not _UNSET:
            updates["exit_limit_price"] = resolved_limit
        if exit_limit_source is not _UNSET:
            updates["exit_limit_source"] = resolved_source
        if updates:
            repo.update_position(position_id, **updates)
    return stored_plan


def _expected_close_reason(trigger: dict, plan: dict) -> str:
    if trigger["reason"] != "exit_limit":
        return str(trigger["reason"])
    return "human_limit_exit" if plan.get("exit_limit_source") == "human" else "operator_limit_exit"


def _materialize_triggered(state: dict) -> dict:
    trigger = repo.get_position_protection_trigger(state["position_id"])
    position = repo.get_position(state["position_id"])
    if trigger is None or position is None:
        raise RuntimeError("triggered protection evidence is incomplete")
    plan, _ = repo.get_position_exit_plan_for_bar(
        state["position_id"],
        bar_open_ms=int(trigger["bar_open_ms"]),
        bar_close_ms=int(trigger["bar_close_ms"]),
    )
    if plan is None:
        raise RuntimeError("triggered protection plan is unavailable")
    expected_reason = _expected_close_reason(trigger, plan)

    if position.get("status") == "open":
        from aletheia.portfolio.position_manager import close_position_routed

        result = close_position_routed(
            state["position_id"],
            expected_reason,
            decision_actor="system",
            decision_source="protective_barrier_1m",
        )
        order_id = result.get("order_id")
        fill_id = result.get("fill_id")
    else:
        order, fill = repo.get_local_paper_close_chain(state["position_id"])
        order_id = order.get("id") if order else None
        fill_id = fill.get("id") if fill else None

    order = repo.get_order(str(order_id)) if order_id else None
    if order is None or order.get("close_reason") != expected_reason or not fill_id:
        repo.block_position_protection_integrity(
            state["position_id"],
            reason="protective_close_chain_mismatch",
            blocked_at=_now().isoformat(),
        )
        return {"closed": False, "reason": "protective_close_chain_mismatch"}
    repo.link_position_protection_close_order(
        state["position_id"],
        trigger_id=trigger["trigger_id"],
        close_order_id=str(order_id),
    )
    repo.materialize_position_protection_close(
        state["position_id"],
        trigger_id=trigger["trigger_id"],
        close_order_id=str(order_id),
        close_fill_id=str(fill_id),
    )
    return {"closed": True, "position_id": state["position_id"], "reason": expected_reason}


async def _refresh_due_position_protection_bars(*, limit: int = 200) -> dict:
    """Fetch missing exact 1m bars for the frozen protected instruments.

    The normal price feed refreshes point-in-time tickers, while the general
    OHLCV cache may remain valid for ten minutes.  Protective barriers cannot
    consume either source: they need the exact completed bar at their durable
    cursor.  Passing ``since`` deliberately bypasses that cache, and using the
    same venue as both primary and fallback prevents cross-venue evidence.
    """

    from aletheia.data.ccxt_provider import get_market_data_provider

    now_ms = int(_now().timestamp() * 1000)
    due: dict[InstrumentId, int] = {}
    for state in repo.list_position_protection_work(limit=limit):
        if state.get("status") == "triggered":
            continue
        position = repo.get_position(state["position_id"])
        if position is None or position.get("status") != "open":
            continue
        cursor = int(state["next_bar_open_ms"])
        if cursor + 60_000 > now_ms or repo.get_exact_position_protection_bar(state) is not None:
            continue
        instrument = InstrumentId.from_parts(
            state["venue"], state["symbol"], state["market_type"]
        )
        due[instrument] = min(cursor, due.get(instrument, cursor))

    summary = {"requested": len(due), "available": 0, "errors": []}
    providers: dict[str, object] = {}
    try:
        for instrument, cursor in sorted(due.items(), key=lambda item: str(item[0])):
            provider = providers.get(instrument.exchange)
            if provider is None:
                provider = get_market_data_provider(
                    exchange=instrument.exchange,
                    native_ccxt=True,
                    fallback_exchange=instrument.exchange,
                )
                providers[instrument.exchange] = provider
            backlog_bars = max(1, (now_ms - cursor) // 60_000)
            fetch_limit = min(500, max(2, backlog_bars + 1))
            try:
                await provider.get_ohlcv(
                    instrument,
                    timeframe="1m",
                    limit=fetch_limit,
                    since=cursor,
                )
                probe = {
                    "venue": instrument.exchange,
                    "symbol": instrument.symbol,
                    "market_type": instrument.market_type,
                    "next_bar_open_ms": cursor,
                }
                if repo.get_exact_position_protection_bar(probe) is None:
                    raise RuntimeError("exact completed 1m bar remained unavailable")
                summary["available"] += 1
            except Exception as exc:
                summary["errors"].append(
                    {
                        "instrument": str(instrument),
                        "code": "protective_ohlcv_refresh_failed",
                        "error_type": type(exc).__name__,
                        "message": str(exc)[:200],
                    }
                )
    finally:
        for provider in providers.values():
            try:
                await provider.close()
            except Exception:
                logger.debug("protective OHLCV provider close failed", exc_info=True)
    return summary


def refresh_due_position_protection_bars(*, limit: int = 200) -> dict:
    """Synchronously refresh the bounded due set from the scheduler worker."""

    import asyncio

    return asyncio.run(_refresh_due_position_protection_bars(limit=limit))


def _evaluate_state(state: dict, *, now_ms: int, detected_at: str) -> dict:
    if state["status"] == "triggered":
        return _materialize_triggered(state)
    position = repo.get_position(state["position_id"])
    if position is None:
        repo.block_position_protection_integrity(
            state["position_id"],
            reason="protected Position is unavailable",
            blocked_at=detected_at,
        )
        return {"blocked": True, "reason": "protected_position_unavailable"}
    if position.get("status") != "open":
        superseded = repo.supersede_position_protection_after_close(
            state["position_id"]
        )
        return {"superseded": superseded}
    cursor = int(state["next_bar_open_ms"])
    if cursor + 60_000 > now_ms:
        return {"waiting": True}
    bar = repo.get_exact_position_protection_bar(state)
    if bar is None:
        repo.record_position_protection_gap(
            state["position_id"],
            expected_next_bar_open_ms=cursor,
            inspected_bar_open_ms=cursor,
            reason="exact_1m_bar_unavailable",
            detected_at=detected_at,
        )
        return {"gap": True, "reason": "exact_1m_bar_unavailable"}

    plan, bisected = repo.get_position_exit_plan_for_bar(
        state["position_id"],
        bar_open_ms=cursor,
        bar_close_ms=cursor + 60_000,
    )
    if plan is None:
        repo.block_position_protection_integrity(
            state["position_id"],
            reason="exit_plan_unavailable_at_cursor",
            blocked_at=detected_at,
        )
        return {"blocked": True, "reason": "exit_plan_unavailable_at_cursor"}
    if bisected:
        repo.skip_position_protection_boundary_bar(
            state["position_id"],
            expected_next_bar_open_ms=cursor,
            skipped_at=detected_at,
        )
        return {"gap": True, "reason": "exit_plan_boundary_unordered"}

    position = repo.get_position(state["position_id"])
    if position is None or position.get("status") != "open":
        repo.block_position_protection_integrity(
            state["position_id"],
            reason="position_not_open_at_barrier_evaluation",
            blocked_at=detected_at,
        )
        return {"blocked": True, "reason": "position_not_open_at_barrier_evaluation"}
    stop = plan.get("stop_price")
    upside = plan.get("exit_limit_price") or plan.get("take_profit_price")
    direction = position.get("direction")
    if direction in {"long", "buy"}:
        stop_hit = stop is not None and float(bar["low"]) <= float(stop)
        target_hit = upside is not None and float(bar["high"]) >= float(upside)
    elif direction in {"short", "sell"}:
        stop_hit = stop is not None and float(bar["high"]) >= float(stop)
        target_hit = upside is not None and float(bar["low"]) <= float(upside)
    else:
        repo.block_position_protection_integrity(
            state["position_id"],
            reason="unsupported_position_direction",
            blocked_at=detected_at,
        )
        return {"blocked": True, "reason": "unsupported_position_direction"}

    if not stop_hit and not target_hit:
        try:
            trailing = json.loads(plan["plan_json"]).get("trailing")
        except (KeyError, TypeError, ValueError):
            trailing = None
        if isinstance(trailing, dict) and trailing.get("observation") == "completed_1m":
            from aletheia.config.loader import load_config
            from aletheia.portfolio.position_manager import _maybe_update_trailing_stop

            position_for_bar = dict(position)
            position_for_bar["trailing_json"] = json.dumps(trailing, sort_keys=True)
            favorable_price = (
                float(bar["high"])
                if direction in {"long", "buy"}
                else float(bar["low"])
            )
            _maybe_update_trailing_stop(
                position_for_bar,
                favorable_price,
                load_config(),
                completed_bar=True,
                effective_at=datetime.fromtimestamp(
                    (cursor + 60_000) / 1000,
                    timezone.utc,
                ).isoformat(),
                protection_venue=str(state["venue"]),
            )
        repo.advance_position_protection_cursor(
            state["position_id"],
            expected_next_bar_open_ms=cursor,
            evaluated_bar_open_ms=cursor,
            next_bar_open_ms=cursor + 60_000,
            evaluated_at=detected_at,
        )
        return {"evaluated": True}

    reason = "stop_loss" if stop_hit else "exit_limit" if plan.get("exit_limit_price") is not None else "take_profit"
    trigger = PositionProtectionTrigger(
        position_id=state["position_id"],
        trigger_id=str(uuid.uuid4()),
        detected_at=detected_at,
        venue=state["venue"],
        symbol=state["symbol"],
        market_type=state["market_type"],
        bar_open_ms=cursor,
        bar_close_ms=cursor + 60_000,
        bar_open=bar["open"],
        bar_high=bar["high"],
        bar_low=bar["low"],
        bar_close=bar["close"],
        bar_volume=bar["volume"],
        provider=bar["provider"],
        bar_hash=repo.hash_position_protection_bar(
            venue=state["venue"], symbol=state["symbol"], market_type=state["market_type"],
            timeframe="1m", bar_open_ms=cursor, bar_close_ms=cursor + 60_000,
            bar_open=bar["open"], bar_high=bar["high"], bar_low=bar["low"],
            bar_close=bar["close"], bar_volume=bar["volume"], provider=bar["provider"],
        ),
        plan_version=plan["version"],
        plan_effective_at=plan["effective_at"],
        plan_hash=plan["plan_hash"],
        stop_price=stop,
        upside_price=upside,
        stop_hit=stop_hit,
        target_hit=target_hit,
        reason=reason,
    )
    repo.record_position_protection_trigger(trigger)
    refreshed = repo.get_position_protection_state(state["position_id"])
    if refreshed is None:
        raise RuntimeError("trigger projection disappeared")
    return _materialize_triggered(refreshed)


def evaluate_due_position_protection(
    *, limit: int = 200, max_bars_per_position: int = 120
) -> dict:
    """Evaluate a bounded workset and catch each Position up in bar order."""

    now = _now()
    detected_at = now.isoformat()
    now_ms = int(now.timestamp() * 1000)
    summary = {"checked": 0, "bars_evaluated": 0, "closed": 0, "errors": []}
    for state in repo.list_position_protection_work(limit=limit):
        summary["checked"] += 1
        try:
            current = state
            for _ in range(max(1, min(int(max_bars_per_position), 500))):
                result = _evaluate_state(
                    current, now_ms=now_ms, detected_at=detected_at
                )
                if result.get("evaluated") is True:
                    summary["bars_evaluated"] += 1
                if result.get("closed") is True:
                    summary["closed"] += 1
                    break
                previous_cursor = int(current["next_bar_open_ms"])
                refreshed = repo.get_position_protection_state(current["position_id"])
                if (
                    refreshed is None
                    or int(refreshed["next_bar_open_ms"]) <= previous_cursor
                    or result.get("waiting") is True
                    or result.get("blocked") is True
                ):
                    break
                current = refreshed
        except Exception as exc:
            logger.exception("protective barrier failed position=%s", state.get("position_id"))
            summary["errors"].append(
                {
                    "position_id": state.get("position_id"),
                    "code": "protective_barrier_failed",
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:200],
                }
            )
    return summary


def run_due_position_protection_cycle(*, limit: int = 200) -> dict:
    """Forward-adopt, refresh exact bars, then evaluate with visible evidence."""

    adoption = adopt_existing_paper_positions(limit=limit)
    refresh = refresh_due_position_protection_bars(limit=limit)
    evaluation = evaluate_due_position_protection(limit=limit)
    return {
        **evaluation,
        "adoption_checked": adoption["checked"],
        "adopted": adoption["adopted"],
        "already_protected": adoption["already_protected"],
        "adoption_skipped": adoption["skipped"],
        "refresh_requested": refresh["requested"],
        "refresh_available": refresh["available"],
        "errors": [
            *(adoption.get("errors") or []),
            *(refresh.get("errors") or []),
            *(evaluation.get("errors") or []),
        ],
    }
