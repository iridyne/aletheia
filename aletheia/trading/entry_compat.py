"""Read-only classification for historical Entry formats."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
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
    "PLANNER_SCHEMA_INVALID",
    "PLANNER_INVALID_JSON",
    "INTENT_SEMANTIC",
    "INVALID_HYPOTHESIS_REFERENCE",
    "ENTRY_PLAN_COMPILE_INVALID",
}
_LEGACY_PLANNER_FORMATS = {
    "entry-planner/v1",
    "entry-planner/v2",
    "entry-planner/v3",
    "entry-planner/v4",
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
    input_json, planner_input = _read_json_object(generation.get("planner_input_json"), "planner_input_json")
    plan_json, plan_payload = _read_json_object(plan.get("plan_json") if plan else None, "plan_json")

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
        planner_input_hash_valid=hash_valid(input_json, generation.get("planner_input_hash")),
        plan_hash_valid=hash_valid(plan_json, plan.get("plan_hash") if plan else None),
    )


def classify_legacy_planning_outcome(row: dict) -> LegacyPlanningOutcome:
    """Project old rows without mutating them or granting current authority."""

    try:
        payload = json.loads(str(row.get("result_json") or "{}"))
        if not isinstance(payload, dict):
            raise TypeError
    except (TypeError, json.JSONDecodeError):
        return LegacyPlanningOutcome("planning_failed", "legacy_result_json_invalid", False)
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


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _legacy_anchor_id(anchor: dict[str, Any]) -> str:
    material = {key: value for key, value in anchor.items() if key not in {"anchor_id", "aliases"}}
    return hashlib.sha256(_canonical(material).encode()).hexdigest()


_LEGACY_REQUIRED_FIELDS = {
    "planning_generation_id",
    "candidate_id",
    "candidate_family",
    "venue",
    "symbol",
    "market_type",
    "direction",
    "thesis",
    "scenario",
    "execution_mode",
    "entry_zone",
    "invalidation_conditions",
    "invalidation_price",
    "stop_price",
    "target_price",
    "expected_horizon_hours",
    "approved_at",
    "expires_at",
    "order_tactic",
    "maker_required",
    "time_in_force",
    "price_rule",
    "max_wait_sec",
    "cancel_replace_policy",
    "max_requotes",
    "partial_fill_policy",
    "max_notional",
    "max_risk_pct",
    "max_leverage",
    "required_permission",
    "execution_account_mode",
    "risk_policy_id",
    "risk_policy_revision",
    "risk_policy_hash",
}
_LEGACY_ENUMS = {
    "market_type": {"spot", "perp"},
    "direction": {"long", "short"},
    "execution_mode": {"RESTING_MAKER", "TRIGGERED"},
    "order_tactic": {"POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"},
    "time_in_force": {"POST_ONLY", "GTC", "IOC"},
    "price_rule": {"EXACT_LIMIT", "PASSIVE_BBO", "OPPOSING_BBO"},
    "cancel_replace_policy": {"CANCEL_ONLY", "BOUNDED_REQUOTE"},
    "partial_fill_policy": {"KEEP_PARTIAL_CANCEL_REMAINDER", "KEEP_RESTING"},
}


def _validate_legacy_plan_structure(data: dict[str, Any], metadata: dict[str, Any]) -> None:
    if _LEGACY_REQUIRED_FIELDS.difference(data):
        raise ValueError("legacy_entry_plan_required_fields_missing")
    if any(data.get(field) not in allowed for field, allowed in _LEGACY_ENUMS.items()):
        raise ValueError("legacy_entry_plan_enum_invalid")
    try:
        low, high = (float(value) for value in data["entry_zone"])
        invalidation = float(data["invalidation_price"])
        stop = float(data["stop_price"])
        target = float(data["target_price"])
        entry = float(data["limit_price"]) if data.get("limit_price") is not None else (low + high) / 2
        positive = (
            low,
            high,
            invalidation,
            stop,
            target,
            float(data["expected_horizon_hours"]),
            float(data["max_notional"]),
            float(data["max_risk_pct"]),
            float(data["max_leverage"]),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("legacy_entry_plan_numeric_invalid") from exc
    if not all(math.isfinite(value) and value > 0 for value in positive) or high < low:
        raise ValueError("legacy_entry_plan_numeric_invalid")
    if data["direction"] == "long":
        coherent = stop < entry < target and invalidation < (entry if data.get("limit_price") is not None else low)
    else:
        coherent = target < entry < stop and invalidation > (entry if data.get("limit_price") is not None else high)
    if not coherent:
        raise ValueError("legacy_entry_plan_geometry_invalid")
    try:
        approved = datetime.fromisoformat(str(data["approved_at"]))
        expires = datetime.fromisoformat(str(data["expires_at"]))
    except ValueError as exc:
        raise ValueError("legacy_entry_plan_instant_invalid") from exc
    if approved.tzinfo is None or expires.tzinfo is None or expires <= approved:
        raise ValueError("legacy_entry_plan_expiry_invalid")
    if data["execution_mode"] == "TRIGGERED" and not (
        data.get("trigger_spec_id") and metadata.get("trigger_spec_version")
    ):
        raise ValueError("legacy_entry_plan_trigger_identity_missing")


def decode_entry_plan(payload: str):
    """Validate one current or immutable historical Plan without rewriting stored bytes."""

    from aletheia.trading.entry_plan import EntryPlan

    try:
        return EntryPlan.model_validate_json(payload)
    except Exception as current_error:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError("entry_plan_json_invalid") from exc
        if not isinstance(data, dict):
            raise ValueError("entry_plan_json_not_object")
        historical_version = data.get("version")
        if historical_version is None:
            raise current_error
    if historical_version not in {"entry-plan/v1", "entry-plan/v2"}:
        raise ValueError("legacy_entry_plan_format_unrecognized")
    source_metadata = {
        key: data.pop(key)
        for key in (
            "version",
            "behavior_version",
            "analysis_context_version",
            "entry_hypothesis_version",
            "entry_profile_version",
            "trigger_spec_version",
        )
        if key in data
    }
    if historical_version == "entry-plan/v2":
        hypothesis_json = data.get("entry_hypothesis_json")
        if not isinstance(hypothesis_json, str):
            raise ValueError("legacy_entry_plan_hypothesis_invalid")
        if hashlib.sha256(hypothesis_json.encode()).hexdigest() != data.get("entry_hypothesis_hash"):
            raise ValueError("legacy_entry_plan_hypothesis_hash_mismatch")
        hypothesis = json.loads(hypothesis_json)
        primary = data.get("primary_anchor_snapshot")
        invalidation = data.get("invalidation_anchor_snapshot")
        if not isinstance(primary, dict) or not isinstance(invalidation, dict):
            raise ValueError("legacy_entry_plan_anchor_invalid")
        if hypothesis.get("primary_anchor_ref") != _legacy_anchor_id(primary):
            raise ValueError("legacy_entry_plan_primary_anchor_mismatch")
        if hypothesis.get("invalidation_anchor_ref") != _legacy_anchor_id(invalidation):
            raise ValueError("legacy_entry_plan_invalidation_anchor_mismatch")
        geometry = {
            "entry_zone": data.get("entry_zone"),
            "invalidation_price": data.get("invalidation_price"),
            "stop_price": data.get("stop_price"),
            "target_price": data.get("target_price"),
            "trigger_params": data.get("trigger_params"),
        }
        if hashlib.sha256(_canonical(geometry).encode()).hexdigest() != data.get("compiled_geometry_fingerprint"):
            raise ValueError("legacy_entry_plan_geometry_hash_mismatch")
    _validate_legacy_plan_structure(data, source_metadata)
    plan = EntryPlan.model_construct(**data)
    object.__setattr__(plan, "_historical_entry_metadata", source_metadata)
    return plan


def historical_plan_version(plan: object) -> str | None:
    metadata = getattr(plan, "_historical_entry_metadata", {})
    return str(metadata.get("version")) if metadata.get("version") else None


def historical_trigger_id(plan: object) -> str | None:
    metadata = getattr(plan, "_historical_entry_metadata", {})
    spec_id = getattr(plan, "trigger_spec_id", None)
    spec_version = metadata.get("trigger_spec_version")
    if spec_id is None or spec_version is None:
        return None
    if (str(spec_id), str(spec_version)) == ("momentum-retrace-reclaim", "v2"):
        return "momentum-retrace-reclaim-geometry"
    return str(spec_id)
