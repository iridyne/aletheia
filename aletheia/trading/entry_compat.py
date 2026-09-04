"""Read-only classification for historical Entry formats."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LegacyPlanningOutcome:
    outcome_class: str
    reason: str | None
    economic_no_plan: bool


@dataclass(frozen=True)
class LegacyEntryRecord:
    planner_contract_version: str
    result: dict[str, Any] | None
    planner_input: dict[str, Any] | None
    plan: dict[str, Any] | None
    result_json: str | None
    planner_input_json: str | None
    plan_json: str | None
    planner_input_hash_valid: bool | None
    plan_hash_valid: bool | None


_BLOCKED_REASONS = {"AI_ENTRY_RUNTIME_UNSUPPORTED", "EXECUTION_VENUE_UNAVAILABLE"}
_FAILED_REASONS = {
    "PLANNER_SCHEMA_INVALID", "PLANNER_INVALID_JSON", "INTENT_SEMANTIC",
    "INVALID_HYPOTHESIS_REFERENCE", "ENTRY_PLAN_COMPILE_INVALID",
}
_LEGACY_PLANNER_FORMATS = {
    "entry-planner/v2", "entry-planner/v3", "entry-planner/v4",
}


def _read_json_object(raw: object, field: str) -> tuple[str | None, dict[str, Any] | None]:
    if raw is None:
        return None, None
    source = str(raw)
    try:
        value = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ValueError(f"legacy_entry_json_invalid:{field}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"legacy_entry_json_not_object:{field}")
    return source, value


def read_legacy_entry_record(
    generation: dict[str, Any],
    plan: dict[str, Any] | None = None,
) -> LegacyEntryRecord:
    """Decode immutable legacy payloads without granting current writer authority."""

    version = str(generation.get("planner_contract_version") or "")
    if version not in _LEGACY_PLANNER_FORMATS:
        raise ValueError("legacy_entry_format_unrecognized")
    result_json, result = _read_json_object(generation.get("result_json"), "result_json")
    input_json, planner_input = _read_json_object(
        generation.get("planner_input_json"), "planner_input_json"
    )
    plan_json, plan_payload = _read_json_object(
        plan.get("plan_json") if plan else None, "plan_json"
    )

    def hash_valid(source: str | None, expected: object) -> bool | None:
        if source is None or not expected:
            return None
        return hashlib.sha256(source.encode()).hexdigest() == str(expected)

    return LegacyEntryRecord(
        planner_contract_version=version,
        result=result,
        planner_input=planner_input,
        plan=plan_payload,
        result_json=result_json,
        planner_input_json=input_json,
        plan_json=plan_json,
        planner_input_hash_valid=hash_valid(
            input_json, generation.get("planner_input_hash")
        ),
        plan_hash_valid=hash_valid(plan_json, plan.get("plan_hash") if plan else None),
    )


def classify_legacy_planning_outcome(row: dict) -> LegacyPlanningOutcome:
    """Project old rows without mutating them or granting current authority."""

    try:
        payload = json.loads(str(row.get("result_json") or "{}"))
    except (TypeError, json.JSONDecodeError):
        payload = {}
    reason = str(payload.get("reason_code") or payload.get("reason") or "") or None
    reason_root = str(reason or "").split(":", 1)[0]
    if reason_root in _BLOCKED_REASONS:
        return LegacyPlanningOutcome("planning_blocked", reason, False)
    if reason_root in _FAILED_REASONS:
        return LegacyPlanningOutcome("planning_failed", reason, False)
    if row.get("status") == "no_plan":
        return LegacyPlanningOutcome("no_plan", reason, True)
    if row.get("status") == "unavailable":
        return LegacyPlanningOutcome("planning_unavailable", reason, False)
    if row.get("status") == "planned":
        return LegacyPlanningOutcome("entry_plan", reason, False)
    return LegacyPlanningOutcome(str(row.get("status") or "awaiting_planning"), reason, False)
