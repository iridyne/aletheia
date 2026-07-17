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
from datetime import datetime, timezone
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
    # Strip ``code_commit`` from the hashing payload: a rebuild of the same
    # policy must not rotate the epoch (that churned learning_policy_epochs
    # every restart with no policy delta).  The commit is still recorded on
    # ``learning_exposures.code_commit`` for downstream attribution, so no
    # measurement replay is lost.
    hash_values = _without_code_commit(safe_values)
    payload = {
        "epoch_kind": str(epoch_kind),
        "effective_values": hash_values,
        "affected_path_ids": sorted(set(affected_path_ids)),
        "safety_required": bool(safety_required),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return PolicyEpoch(
        epoch_kind=str(epoch_kind),
        epoch_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        effective_values=hash_values,
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


def persist_policy_epoch(epoch: PolicyEpoch) -> str:
    """Return the durable open epoch id, rotating when effective values change."""
    from aletheia.learning.ledger import (
        close_policy_epoch, get_open_policy_epoch, insert_record,
    )
    from aletheia.learning.models import LearningPolicyEpoch

    current = get_open_policy_epoch(epoch.epoch_kind)
    if current and current.get("effective_values_hash") == epoch.epoch_hash:
        return str(current["epoch_id"])
    now = datetime.now(timezone.utc).isoformat()
    if current:
        close_policy_epoch(str(current["epoch_id"]), now)
    record = LearningPolicyEpoch(
        epoch_kind=epoch.epoch_kind,
        effective_values_hash=epoch.epoch_hash,
        effective_values_json=json.dumps(epoch.effective_values, sort_keys=True, default=str),
        affected_path_ids_json=json.dumps(epoch.affected_path_ids, sort_keys=True),
        safety_required=epoch.safety_required,
        started_at=now,
    )
    insert_record("learning_policy_epochs", record)
    return record.epoch_id


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


def _without_code_commit(value: Any) -> Any:
    """Drop any ``code_commit`` key at the top level of the effective_values dict.

    Only strips the top-level key that the epoch caller feeds in — nested
    dicts (for example ``learning.model_dump()``) are left alone so a knob
    literally named ``code_commit`` inside another config section is
    preserved.  The caller in ``experiments._policy_epoch`` also stops
    populating this key, so this is defense-in-depth.
    """
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items() if str(key) != "code_commit"}
    return value
