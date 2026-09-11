"""Canonical read model over Candidate, planning, Plan, and execution owners."""

from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

from aletheia.trading.entry_compat import classify_legacy_planning_outcome


def _effective_outcome(cycle: dict) -> tuple[str | None, str | None]:
    if cycle.get("outcome_class"):
        return str(cycle["outcome_class"]), cycle.get("outcome_reason")
    legacy = classify_legacy_planning_outcome(cycle)
    return legacy.outcome_class, legacy.reason


def _state(cycle: dict | None) -> tuple[str, str | None, str]:
    if cycle is None:
        return "AWAITING_PLANNING", "candidate_selected", "await_planner"
    plan_status = cycle.get("entry_plan_status")
    if plan_status == "expired":
        return "PLAN_EXPIRED", cycle.get("entry_plan_terminal_reason"), "await_requalification"
    if ((plan_status in {"triggered", "risk_reserved"}
         or (plan_status == "active" and cycle.get("execution_mode") == "TRIGGERED"))
        and cycle.get("activation_recoverable")):
        expires = cycle.get("activation_expires_at")
        if not expires:
            return "WAITING_TRIGGER", "entry_trigger_activation_required", "await_fresh_trigger"
        if datetime.fromisoformat(expires) <= datetime.now(timezone.utc):
            return "WAITING_TRIGGER", "entry_trigger_activation_expired", "await_fresh_trigger"
        if not cycle.get("activation_stream_current"):
            return "WAITING_TRIGGER", "entry_trigger_activation_monitoring_mismatch", "await_fresh_trigger"
        if (cycle.get("passive_entry") or {}).get("eligible") is False:
            return "WAITING_MARKET", "post_only_would_cross_bbo", "await_passive_market"
    if plan_status == "waiting" or (plan_status == "active" and cycle.get("execution_mode") != "TRIGGERED"):
        if (cycle.get("execution_mode") == "RESTING_MAKER"
            and cycle.get("entry_plan_monitoring_state") == "ready"
            and (cycle.get("passive_entry") or {}).get("eligible") is False):
            return "WAITING_MARKET", "post_only_would_cross_bbo", "await_passive_market"
        state = "WAITING_TRIGGER" if cycle.get("execution_mode") == "TRIGGERED" else "PLAN_READY"
        return state, None, "fast_monitoring"
    if plan_status in {"active", "triggered"}:
        return "TRIGGERED", None, "risk_reservation"
    if plan_status == "risk_reserved":
        return "RISK_RESERVED", None, "gateway_execution"
    if plan_status == "ordered":
        return "ORDER_WORKING", None, "execution_reconciliation"
    if plan_status == "partially_filled":
        return "PARTIALLY_FILLED", None, "execution_reconciliation"
    if plan_status == "filled":
        return "FILLED", None, "execution_lifecycle"
    if plan_status in {"invalidated", "cancelled", "failed"}:
        return f"PLAN_{str(plan_status).upper()}", cycle.get("entry_plan_terminal_reason"), "terminal"
    outcome, reason = _effective_outcome(cycle)
    if outcome == "planning_blocked":
        return "PLANNING_BLOCKED", reason, "resume_on_authority_change"
    if outcome == "planning_unavailable":
        return "PLANNING_UNAVAILABLE", reason, (
            "await_material_input_or_owner_replan" if cycle.get("attention_status") == "terminal" else "bounded_retry")
    if outcome == "planning_failed":
        return "PLANNING_FAILED", reason, "await_material_input_or_owner_replan"
    if outcome == "no_plan":
        return "NO_PLAN", reason, "await_material_input_or_owner_replan"
    if cycle.get("status") == "planning":
        return ("REPLANNING" if int(cycle.get("cycle_number") or 1) > 1 else "PLANNING"), None, "await_planner_result"
    return (
        "REPLAN_QUEUED" if int(cycle.get("cycle_number") or 1) > 1 else "PLANNING_QUEUED",
        cycle.get("cycle_reason"),
        "run_planner",
    )


def project_candidate_entry_lifecycle(candidate: dict, cycles: list[dict]) -> dict[str, Any]:
    ordered = sorted(cycles, key=lambda item: (int(item.get("cycle_number") or 0), str(item.get("created_at") or ""), str(item.get("id") or "")))
    current = next((item for item in reversed(ordered) if item.get("is_current")), ordered[-1] if ordered else None)
    state, reason, next_action = _state(current)
    expired_count = sum(item.get("entry_plan_status") == "expired" for item in ordered)
    changed_at = (
        current.get("entry_plan_terminal_at")
        or current.get("entry_plan_updated_at")
        or current.get("updated_at")
        if current else candidate.get("created_at")
    )
    if (current and state in {"PLAN_READY", "WAITING_MARKET"}
        and current.get("entry_plan_monitoring_state") == "ready"):
        changed_at = (current.get("passive_entry") or {}).get("observed_at") or changed_at
    if current and reason == "entry_trigger_activation_expired":
        changed_at = current["activation_expires_at"]
    elif current and reason == "entry_trigger_activation_monitoring_mismatch":
        changed_at = current.get("monitoring_changed_at")
    return {
        "lifecycle_state": state,
        "execution_state": (current.get("execution") or {}).get("execution_state", "NOT_STARTED") if current else "NOT_STARTED",
        "lifecycle_reason": reason,
        "current_cycle_number": int(current.get("cycle_number") or 1) if current else None,
        "expired_plan_count": expired_count,
        "current_generation_id": current.get("id") if current else None,
        "current_plan_id": current.get("entry_plan_id") if current else None,
        "last_stage": current.get("last_stage") if current else None,
        "model_invoked": bool(current.get("model_invoked")) if current else False,
        "compiler_invoked": bool(current.get("compiler_invoked")) if current else False,
        "state_changed_at": changed_at,
        "next_action": next_action,
        "cycles": [_cycle_projection(item) for item in ordered],
    }


def _cycle_projection(cycle: dict) -> dict[str, Any]:
    outcome, reason = _effective_outcome(cycle)
    events = [
        {
            "event": (
                "candidate_requalified"
                if int(cycle.get("cycle_number") or 1) > 1
                else "candidate_selected"
            ),
            "at": cycle.get("created_at"),
            "reason": cycle.get("requalification_candidate_id"),
        },
        {"event": "cycle_queued", "at": cycle.get("created_at"), "reason": cycle.get("cycle_reason")},
    ]
    if cycle.get("planning_started_at"):
        events.append({"event": "planning_started", "at": cycle["planning_started_at"], "reason": None})
    if cycle.get("model_invoked") or outcome:
        events.append({
            "event": ("model_invoked" if cycle.get("model_invoked") else
                      "model_dispatch_unavailable" if reason == "PLANNER_CANCELLED_DISPATCH_UNKNOWN" else
                      "model_skipped"),
            "at": cycle.get("model_invoked_at") or cycle.get("completed_at") or cycle.get("updated_at"),
            "reason": None if cycle.get("model_invoked") else reason,
        })
    if cycle.get("compiler_invoked") or outcome:
        events.append({
            "event": "compiler_invoked" if cycle.get("compiler_invoked") else "compiler_skipped",
            "at": cycle.get("compiler_invoked_at") or cycle.get("completed_at") or cycle.get("updated_at"),
            "reason": None if cycle.get("compiler_invoked") else reason,
        })
    if outcome:
        events.append({"event": outcome, "at": cycle.get("completed_at") or cycle.get("updated_at"), "reason": reason})
    if cycle.get("entry_plan_id"):
        events.append({"event": "entry_plan_created", "at": cycle.get("entry_plan_created_at"), "reason": None})
    if cycle.get("entry_plan_status"):
        events.append({
            "event": f"entry_plan_{cycle['entry_plan_status']}",
            "at": cycle.get("entry_plan_terminal_at") or cycle.get("entry_plan_updated_at"),
            "reason": cycle.get("entry_plan_terminal_reason"),
        })
    if cycle.get("passive_entry"):
        passive = cycle["passive_entry"]
        events.append({"event": "passive_entry_eligible" if passive["eligible"] else "waiting_for_passive_market",
                       "at": passive.get("observed_at"),
                       "reason": None if passive["eligible"] else "post_only_would_cross_bbo"})
    for activation in cycle.get("activation_history", []):
        events.append({"event": "trigger_activation_created", "at": activation["created_at"], "reason": None})
    return {
        "generation_id": cycle.get("id"),
        "cycle_number": int(cycle.get("cycle_number") or 1),
        "previous_generation_id": cycle.get("previous_generation_id"),
        "cycle_reason": cycle.get("cycle_reason"),
        "requalification_candidate_id": cycle.get("requalification_candidate_id"),
        "last_stage": cycle.get("last_stage"),
        "model_invoked": bool(cycle.get("model_invoked")),
        "compiler_invoked": bool(cycle.get("compiler_invoked")),
        "outcome_class": outcome,
        "outcome_reason": reason,
        "entry_plan_id": cycle.get("entry_plan_id"),
        "execution": cycle.get("execution"),
        "execution_state": (cycle.get("execution") or {}).get("execution_state", "NOT_STARTED"),
        "entry_plan_status": cycle.get("entry_plan_status"),
        "events": sorted(events, key=lambda event: (str(event.get("at") or ""), event["event"])),
    }
