"""Value normalization and the canonical Observation treatment adapter registry."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, runtime_checkable

from extensions.observation_lab.manifest import ExecutionManifest

__all__ = [
    "_ADAPTERS",
    "NumericTreatmentAdapter",
    "ObservationTreatmentDecision",
    "TreatmentAdapter",
    "_adapter",
    "_assignment_unit_key",
    "_hash",
    "_json",
    "_safe_json_object",
    "_text",
    "_utc_text",
    "register_treatment_adapter",
    "registered_treatment_adapters",
]


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)



def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()



def _utc_text(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_must_be_utc") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field}_must_be_utc")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")



def _text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field}_required")
    return result



@runtime_checkable
class TreatmentAdapter(Protocol):
    """One exact production behavior adapter registered by identity."""

    adapter_id: str
    adapter_version: str
    behavior_dimension: str
    requires_outcome_seed: bool

    def assignment_unit_key(self, context: dict[str, Any]) -> str: ...

    def eligible(self, manifest: ExecutionManifest, context: dict[str, Any]) -> bool: ...

    def deterministic_baseline(self, context: dict[str, Any]) -> dict[str, Any]: ...

    def apply_payload(
        self,
        baseline: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    def build_outcome_seed(self, context: dict[str, Any]) -> Any: ...



@dataclass(frozen=True)
class NumericTreatmentAdapter:
    """Built-in bounded numeric adapter for legacy Main call sites.

    The adapter carries no Experiment state. Lab supplies the immutable
    payload; Main only converts that payload back to the caller's numeric
    value.
    """

    adapter_id: str
    adapter_version: str = "v1"
    behavior_dimension: str = "numeric"
    requires_outcome_seed: bool = False

    def assignment_unit_key(self, context: dict[str, Any]) -> str:
        return str(context.get("assignment_key") or context.get("candidate_key") or "unassigned")

    def eligible(self, manifest: ExecutionManifest, context: dict[str, Any]) -> bool:
        population = manifest.eligible_population or {}
        expected_path = population.get("path_id")
        return expected_path is None or expected_path == context.get("path_id")

    def deterministic_baseline(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(context["baseline"])

    def apply_payload(
        self,
        baseline: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        value = payload.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("numeric_observation_payload_unavailable")
        return {"value": float(value)}

    def build_outcome_seed(self, context: dict[str, Any]) -> Any:
        return context.get("outcome_seed")



_ADAPTERS: dict[tuple[str, str], TreatmentAdapter] = {}



def register_treatment_adapter(adapter: TreatmentAdapter, *, replace: bool = False) -> None:
    key = (_text(adapter.adapter_id, "adapter_id"), _text(adapter.adapter_version, "adapter_version"))
    if key in _ADAPTERS and not replace:
        raise ValueError(f"treatment_adapter_already_registered:{key[0]}:{key[1]}")
    _ADAPTERS[key] = adapter



def registered_treatment_adapters() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(_ADAPTERS))



def _adapter(adapter_id: str, adapter_version: str | None) -> TreatmentAdapter | None:
    if adapter_version is not None:
        return _ADAPTERS.get((adapter_id, adapter_version))
    matches = [item for key, item in _ADAPTERS.items() if key[0] == adapter_id]
    return matches[0] if len(matches) == 1 else None



def _assignment_unit_key(
    adapter: TreatmentAdapter | None,
    context: Mapping[str, Any],
    candidate_key: str,
) -> str:
    if adapter is None:
        return candidate_key
    try:
        value = context.get("assignment_key") or adapter.assignment_unit_key(dict(context))
        return str(value).strip() or candidate_key
    except Exception:
        return candidate_key



@dataclass(frozen=True)
class ObservationTreatmentDecision:
    payload: dict[str, Any]
    applied: bool
    assignment: str
    behavior_changed: bool
    reason: str
    observation_id: str | None = None
    generation: int | None = None
    manifest_id: str | None = None
    manifest_hash: str | None = None
    behavior_contract_hash: str | None = None
    behavior_policy_epoch_ref: str | None = None
    active_manifest_snapshot: tuple[str, ...] = ()
    manifest_set_fingerprint: str | None = None
    revocation_snapshot_fingerprint: str | None = None
    authority_source: str | None = None
    authority_epoch: str | None = None
    outcome_source: str | None = None
    receipt_id: str | None = None



def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}
