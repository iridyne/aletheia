"""Pure policy-epoch helpers for Learning attribution.

Phase 0 does not intercept operator/risk actions. It defines the deterministic,
secret-free epoch semantics that later adapters persist around those existing
controls. Safety-required changes always continue and invalidate affected
comparisons instead of being withheld for experimental cleanliness.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


_SECRET_FRAGMENTS = (
    "secret",
    "password",
    "passphrase",
    "api_key",
    "token",
    "credential",
    "private_key",
)


@dataclass(frozen=True)
class PolicyEpoch:
    epoch_kind: str
    epoch_hash: str
    effective_values: dict[str, Any]
    affected_path_ids: tuple[str, ...]
    safety_required: bool
    is_learning_treatment: bool = False


@dataclass(frozen=True)
class EpochComparison:
    experiment_action: str
    safety_action: str
    reason: str


def build_policy_epoch(
    epoch_kind: str,
    effective_values: dict[str, Any],
    *,
    affected_path_ids: tuple[str, ...],
    safety_required: bool = False,
) -> PolicyEpoch:
    safe_values = _without_secrets(effective_values)
    payload = {
        "epoch_kind": str(epoch_kind),
        "effective_values": safe_values,
        "affected_path_ids": sorted(set(affected_path_ids)),
        "safety_required": bool(safety_required),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return PolicyEpoch(
        epoch_kind=str(epoch_kind),
        epoch_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        effective_values=safe_values,
        affected_path_ids=tuple(payload["affected_path_ids"]),
        safety_required=bool(safety_required),
    )


def compare_policy_epochs(
    before: PolicyEpoch,
    after: PolicyEpoch,
    path_id: str,
) -> EpochComparison:
    if before.epoch_hash == after.epoch_hash:
        return EpochComparison("continue", "unchanged", "same_effective_policy")
    affected = path_id in set(before.affected_path_ids) | set(after.affected_path_ids)
    if not affected:
        return EpochComparison("continue", "unchanged", "path_not_affected")
    if after.safety_required:
        return EpochComparison(
            "invalidate",
            "apply_immediately",
            "safety_change_must_continue_and_cannot_be_credited_to_learning",
        )
    return EpochComparison("new_epoch", "apply_normally", "effective_policy_changed")


def _without_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_secrets(item)
            for key, item in value.items()
            if not _is_secret_key(str(key))
        }
    if isinstance(value, (list, tuple)):
        return [_without_secrets(item) for item in value]
    return value


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SECRET_FRAGMENTS)

