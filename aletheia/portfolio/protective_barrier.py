"""Durable exact-1m protective barriers for new ``paper_internal`` positions.

Only a closed candle from the activation-time frozen instrument may authorize a
trigger.  The candle records evidence; the existing routed close path obtains a
fresh mark and remains the sole source of the simulated fill.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from aletheia.data.symbols import to_canonical
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    PositionExitPlanVersion,
    PositionProtectionState,
    PositionProtectionTrigger,
)

logger = logging.getLogger("aletheia.protective_barrier")
_UNSET = object()


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
) -> str:
    return json.dumps(
        {
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "exit_limit_price": exit_limit_price,
            "exit_limit_source": exit_limit_source,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def activate_new_paper_position(position_id: str) -> dict:
    """Atomically activate immutable protection for one newly inserted Position."""

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

    cfg = load_config()
    venue = protective_price_venue(
        "paper_internal",
        configured_data_venue=cfg.market_data.ccxt_exchange,
    )
    if not venue:
        raise ValueError("paper protection venue is unavailable")
    resolved = effective_exit_plan(position, cfg)
    if resolved.stop_price is None and resolved.target_price is None:
        raise ValueError("paper position has no resolved protective level")

    effective_at = str(position.get("opened_at") or "")
    activated_ms = _timestamp_ms(effective_at)
    next_bar_open_ms = ((activated_ms + 59_999) // 60_000) * 60_000
    created_at = _now().isoformat()
    payload = _plan_payload(
        stop_price=resolved.stop_price,
        take_profit_price=resolved.target_price,
        exit_limit_price=position.get("exit_limit_price"),
        exit_limit_source=position.get("exit_limit_source"),
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
        provenance="paper_entry_protection_v1",
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
        )
        repo.insert_position_protection_state(state)
    return {"activated": True, "position_id": position_id, "next_bar_open_ms": next_bar_open_ms}


def persist_position_exit_plan(
    position_id: str,
    plan,
    *,
    provenance: str,
    exit_limit_price: object = _UNSET,
    exit_limit_source: object = _UNSET,
    extra_position_updates: dict | None = None,
):
    """Atomically update flat intent and append its protected immutable version."""

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
    effective = _now()
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


def _evaluate_state(state: dict, *, now_ms: int, detected_at: str) -> dict:
    if state["status"] == "triggered":
        return _materialize_triggered(state)
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


def evaluate_due_position_protection(*, limit: int = 200) -> dict:
    """Evaluate a bounded workset; one Position failure never stops other protection."""

    now = _now()
    detected_at = now.isoformat()
    now_ms = int(now.timestamp() * 1000)
    summary = {"checked": 0, "closed": 0, "errors": []}
    for state in repo.list_position_protection_work(limit=limit):
        summary["checked"] += 1
        try:
            result = _evaluate_state(state, now_ms=now_ms, detected_at=detected_at)
            if result.get("closed") is True:
                summary["closed"] += 1
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
