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


def gate_operator_learning_context(ctx: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    """Return the static Operator baseline when autonomous Learning is disabled."""
    gated = deepcopy(ctx)
    if enabled:
        return gated
    layer2 = gated.setdefault("layer2", {})
    for key in _LAYER2_COMPONENTS.values():
        layer2[key] = None
    layer3 = gated.setdefault("layer3", {})
    for key in (*_LAYER3_COMPONENTS.values(), "guard_candidates"):
        layer3[key] = []
    return gated
