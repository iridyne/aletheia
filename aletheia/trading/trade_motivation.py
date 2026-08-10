"""Canonical structured expression of one System Operator entry decision."""

from __future__ import annotations

import json
from typing import Any


TRADE_MOTIVATION_VERSION = "structured-trade-motivation/v1"
_FIELDS = {
    "schema_version",
    "action_intent",
    "thesis",
    "observed_facts",
    "interpretations",
    "supporting_evidence",
    "opposing_evidence",
    "key_assumptions",
    "invalidation_conditions",
    "expected_horizon",
    "confidence",
    "uncertainty_reason",
    "evidence_manifest",
}
_ACTION_INTENTS = {"long", "short", "hold", "close", "reduce", "unavailable"}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _manifest_reference_allowlist(manifest: dict[str, Any]) -> set[str]:
    allowed: set[str] = set()

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*path, str(key)))
            return
        if isinstance(value, list):
            for child in value:
                visit(child, path)
            return
        if not isinstance(value, (str, int)) or not path:
            return
        key = path[-1]
        label = None
        if key == "id" and len(path) > 1:
            label = path[-2]
        elif key.endswith("_id"):
            label = key[:-3]
        elif key.endswith("_ids"):
            label = key[:-4]
        elif key == "source_ref":
            label = key
        if label:
            identity = str(value)
            allowed.update({identity, f"{label}:{identity}"})

    visit(manifest)
    return allowed


def _facts(value: Any, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    allowed_refs = _manifest_reference_allowlist(manifest)
    facts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact") or "").strip()
        evidence_refs = [
            ref for ref in _strings(item.get("evidence_refs")) if ref in allowed_refs
        ]
        if fact and evidence_refs:
            facts.append({"fact": fact, "evidence_refs": evidence_refs})
    return facts


def parse_trade_motivation(value: Any) -> dict[str, Any] | None:
    """Return the canonical object, or ``None`` for legacy/malformed input."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if (
        not isinstance(value, dict)
        or not _FIELDS.issubset(value)
        or value.get("schema_version") != TRADE_MOTIVATION_VERSION
    ):
        return None
    horizon = value.get("expected_horizon")
    if not isinstance(horizon, dict) or not isinstance(horizon.get("hours"), (int, float)):
        horizon = None
    confidence = value.get("confidence")
    if not isinstance(confidence, int) or isinstance(confidence, bool):
        confidence = None
    manifest = value.get("evidence_manifest")
    manifest = manifest if isinstance(manifest, dict) else {}
    action_intent = str(value.get("action_intent") or "unavailable")
    if action_intent not in _ACTION_INTENTS:
        action_intent = "unavailable"
    return {
        "schema_version": TRADE_MOTIVATION_VERSION,
        "action_intent": action_intent,
        "thesis": str(value.get("thesis") or "unavailable"),
        "observed_facts": _facts(value.get("observed_facts"), manifest),
        "interpretations": _strings(value.get("interpretations")),
        "supporting_evidence": _strings(value.get("supporting_evidence")),
        "opposing_evidence": _strings(value.get("opposing_evidence")),
        "key_assumptions": _strings(value.get("key_assumptions")),
        "invalidation_conditions": _strings(value.get("invalidation_conditions")),
        "expected_horizon": horizon,
        "confidence": confidence,
        "uncertainty_reason": str(value.get("uncertainty_reason") or "unavailable"),
        "evidence_manifest": manifest,
    }


def serialize_trade_motivation(value: Any) -> str:
    motivation = parse_trade_motivation(value)
    if motivation is None:
        raise ValueError("invalid_structured_trade_motivation")
    return json.dumps(motivation, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_action_intent(action: Any, direction: Any) -> str:
    action = str(action or "").lower()
    direction = str(direction or "").lower()
    if action == "trade":
        action_intent = direction if direction in {"long", "short"} else "unavailable"
    elif action in {"skip", "watch", "portfolio_guard_skip"}:
        action_intent = "hold"
    elif action in {"close", "reduce"}:
        action_intent = action
    else:
        return "unavailable"
    return action_intent


def build_trade_motivation(decision: Any, evidence_manifest: dict | None = None) -> dict[str, Any]:
    horizon = getattr(decision, "expected_horizon_hours", None)
    action_intent = normalize_action_intent(
        getattr(decision, "action", None), getattr(decision, "direction", None)
    )
    opposing = list(getattr(decision, "opposing_signals", None) or [])
    bear_case = str(getattr(decision, "bear_case", None) or "").strip()
    if bear_case and bear_case not in opposing:
        opposing.append(bear_case)
    raw = {
        "schema_version": TRADE_MOTIVATION_VERSION,
        "action_intent": action_intent,
        "thesis": getattr(decision, "reasoning", None) or "unavailable",
        "observed_facts": getattr(decision, "observed_facts", None) or [],
        "interpretations": getattr(decision, "interpretations", None) or [],
        "supporting_evidence": getattr(decision, "supporting_signals", None) or [],
        "opposing_evidence": opposing,
        "key_assumptions": getattr(decision, "key_assumptions", None) or [],
        "invalidation_conditions": getattr(decision, "invalidation_conditions", None) or [],
        "expected_horizon": {"hours": float(horizon)} if isinstance(horizon, (int, float)) else None,
        "confidence": getattr(decision, "conviction", None),
        "uncertainty_reason": getattr(decision, "uncertainty_reason", None) or "unavailable",
        "evidence_manifest": evidence_manifest if isinstance(evidence_manifest, dict) else {},
    }
    parsed = parse_trade_motivation(raw)
    assert parsed is not None
    return parsed
