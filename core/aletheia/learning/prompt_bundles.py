"""One attributable manifest for overlapping Operator Learning prompt channels."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any


_LAYER2_COMPONENTS = {
    "reflection": "reflection",
    "trading_memo": "trading_memo",
    "reasoning_feedback": "reasoning_feedback",
    "calibration_feedback": "calibration_feedback",
}
_LAYER3_COMPONENTS = {
    "trade_cases": "lessons",
    "structured_lessons": "ps_lessons",
}


def _authority_manifest(path_id: str, candidate_key: str, decision) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "candidate_key": candidate_key,
        "assignment": decision.assignment,
        "applied": bool(decision.applied),
        "reason": decision.reason,
        "hypothesis_id": decision.hypothesis_id,
        "experiment_id": decision.experiment_id,
        "exposure_id": decision.exposure_id,
    }


def _unavailable_authority(
    path_id: str, candidate_key: str, *, reason: str = "authority_adapter_error",
) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "candidate_key": candidate_key,
        "assignment": "unavailable",
        "applied": False,
        "reason": reason,
        "hypothesis_id": None,
        "experiment_id": None,
        "exposure_id": None,
    }


def _fingerprint(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_operator_learning_manifest(ctx: dict[str, Any]) -> dict[str, Any]:
    components: dict[str, str] = {}
    layer2 = ctx.get("layer2") or {}
    layer3 = ctx.get("layer3") or {}
    for name, key in _LAYER2_COMPONENTS.items():
        value = layer2.get(key)
        if value:
            components[name] = _fingerprint(value)
    for name, key in _LAYER3_COMPONENTS.items():
        value = layer3.get(key)
        if value:
            components[name] = _fingerprint(value)
    return {
        "manifest_version": "operator_bundle:v1",
        "bundle_id": _fingerprint(components),
        "components": components,
        "attribution_unit": "operator_learning_bundle",
    }


def build_observer_learning_manifest(
    memory_block: str, guard_advisory: str = "",
) -> dict[str, Any]:
    components = {"advisory_memory": _fingerprint(memory_block)} if memory_block else {}
    if guard_advisory:
        components["guard_advisory"] = _fingerprint(guard_advisory)
    return {
        "manifest_version": "observer_bundle:v1",
        "bundle_id": _fingerprint(components),
        "components": components,
        "attribution_unit": "observer_advisory_bundle",
    }


def operator_learning_candidate_key(ctx: dict[str, Any]) -> str:
    """Stable identity for one visible durable pending-queue context."""
    layer2 = ctx.get("layer2") or {}
    pending = []
    for row in layer2.get("pending_signals") or []:
        pending.append({
            "opportunity_id": row.get("opportunity_id"),
            "signal_source": row.get("signal_source") or "market_observer",
            "symbol": row.get("symbol"),
            "direction": row.get("direction"),
            "market_type": row.get("market_type"),
            "at": row.get("at") or row.get("created_at"),
        })
    if len(pending) == 1 and pending[0].get("opportunity_id"):
        identity = {
            key: pending[0].get(key)
            for key in (
                "opportunity_id", "signal_source", "symbol", "direction", "market_type"
            )
        }
        return f"operator_candidate:{_fingerprint(identity)}"
    queue = layer2.get("pending_queue") or {}
    identity = {
        "pending": pending,
        "read_status": queue.get("read_status"),
        "unresolved_total": queue.get("unresolved_total"),
        "oldest_at": queue.get("oldest_at"),
    }
    return f"operator_queue:{_fingerprint(identity)}"


def authorize_operator_learning_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return an Operator context with Learning present only for its treatment arm."""
    proposed_manifest = build_operator_learning_manifest(ctx)
    candidate_key = operator_learning_candidate_key(ctx)
    pending = (ctx.get("layer2") or {}).get("pending_signals") or []
    opportunity_ids = {
        str(row.get("opportunity_id")) for row in pending if row.get("opportunity_id")
    }
    opportunity_id = next(iter(opportunity_ids)) if len(opportunity_ids) == 1 else None
    market_observer_sources = {
        str(row.get("signal_source") or "market_observer") for row in pending
    }
    exact_market_candidate = (
        len(pending) == 1
        and len(opportunity_ids) == 1
        and market_observer_sources == {"market_observer"}
    )
    if not exact_market_candidate:
        gated = gate_operator_learning_context(ctx, enabled=False)
        manifest = build_operator_learning_manifest(gated)
        manifest["proposed_bundle_id"] = proposed_manifest["bundle_id"]
        manifest["authority"] = _unavailable_authority(
            "operator_learning_bundle",
            candidate_key,
            reason="exact_candidate_scope_required",
        )
        gated["learning_manifest"] = manifest
        return gated
    outcome_candidate_key = (
        f"opportunity:{opportunity_id}:4h"
        if opportunity_id and market_observer_sources == {"market_observer"}
        else None
    )
    try:
        from aletheia.learning.experiments import apply_boolean_treatment
        decision = apply_boolean_treatment(
            path_id="operator_learning_bundle",
            scope={"consumer": "system_operator", "bundle_version": "v1"},
            action={"type": "prompt_bundle", "enabled": True, "bundle_version": "v1"},
            source_cohort="operator_learning_bundle:v1",
            target_horizon="4h",
            candidate_key=candidate_key,
            consumer="system_operator",
            consumer_stage="prompt_context",
            outcome_candidate_key=outcome_candidate_key,
            opportunity_id=opportunity_id,
            manifest={"proposed_bundle_id": proposed_manifest["bundle_id"]},
        )
        authority = _authority_manifest("operator_learning_bundle", candidate_key, decision)
        enabled = bool(decision.applied and decision.value >= 1.0)
    except Exception:
        authority = _unavailable_authority("operator_learning_bundle", candidate_key)
        enabled = False
    gated = gate_operator_learning_context(ctx, enabled=enabled)
    manifest = build_operator_learning_manifest(gated)
    manifest["proposed_bundle_id"] = proposed_manifest["bundle_id"]
    manifest["authority"] = authority
    gated["learning_manifest"] = manifest
    return gated


def authorize_observer_learning_bundle(
    memory_block: str,
    guard_advisory: str = "",
    *,
    candidate_key: str,
    scope: dict[str, Any] | None = None,
    outcome_candidate_key: str | None = None,
    opportunity_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Authorize one per-instrument Observer prompt bundle and expose its trace."""
    proposed = build_observer_learning_manifest(memory_block, guard_advisory)
    try:
        from aletheia.learning.experiments import apply_boolean_treatment
        decision = apply_boolean_treatment(
            path_id="observer_advisory_bundle",
            scope=scope or {"candidate_key": candidate_key},
            action={"type": "prompt_bundle", "enabled": True, "bundle_version": "v1"},
            source_cohort="observer_advisory_bundle:v1",
            target_horizon="4h",
            candidate_key=candidate_key,
            consumer="market_observer",
            consumer_stage="prompt_context",
            outcome_candidate_key=outcome_candidate_key,
            opportunity_id=opportunity_id,
            manifest={"proposed_bundle_id": proposed["bundle_id"]},
        )
        authority = _authority_manifest("observer_advisory_bundle", candidate_key, decision)
        enabled = bool(decision.applied and decision.value >= 1.0)
    except Exception:
        authority = _unavailable_authority("observer_advisory_bundle", candidate_key)
        enabled = False
    rendered = "\n".join(part for part in (memory_block, guard_advisory) if part) if enabled else ""
    manifest = build_observer_learning_manifest(
        memory_block if enabled else "", guard_advisory if enabled else "",
    )
    manifest["proposed_bundle_id"] = proposed["bundle_id"]
    manifest["authority"] = authority
    return rendered, manifest


def gate_operator_learning_context(ctx: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    """Return the static Operator baseline when autonomous Learning is disabled."""
    gated = deepcopy(ctx)
    if enabled:
        return gated
    layer2 = gated.setdefault("layer2", {})
    for key in _LAYER2_COMPONENTS.values():
        layer2[key] = None
    layer3 = gated.setdefault("layer3", {})
    for key in (*_LAYER3_COMPONENTS.values(), "guard_candidates", "serenity_opportunities"):
        layer3[key] = []
    return gated
