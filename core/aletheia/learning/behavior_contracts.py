"""Deterministic, secret-free identity for measured Learning behavior."""

from __future__ import annotations

import hashlib
import json

from aletheia.learning.registry import get_learning_path


BEHAVIOR_CONTRACT_SCHEMA = "learning-behavior-contract/v1"


def build_behavior_contract_hash(
    *,
    path_id: str,
    policy_hash: str,
    target_horizon: str,
    outcome_contract_version: str,
    metric_version: str,
) -> str:
    """Hash only declared behavior and measurement-contract inputs."""
    path = get_learning_path(path_id)
    version = path.behavior_contract_version
    if not path.measurement_supported or not version:
        raise ValueError(f"measurement behavior contract unavailable: {path_id}")
    payload = {
        "schema": BEHAVIOR_CONTRACT_SCHEMA,
        "path_id": path.path_id,
        "behavior_contract_version": version,
        "policy_hash": str(policy_hash),
        "target_horizon": str(target_horizon),
        "outcome_contract_version": str(outcome_contract_version),
        "metric_version": str(metric_version),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_behavior_contract_hash(
    *,
    path_id: str,
    policy_epoch: str,
    target_horizon: str,
    outcome_contract_version: str,
    metric_version: str,
) -> str:
    """Resolve a persisted epoch id to its deterministic effective-policy hash."""
    from aletheia.storage.sqlite import get_db

    row = get_db().execute(
        "SELECT effective_values_hash FROM learning_policy_epochs "
        "WHERE epoch_id=? AND epoch_kind=? AND ended_at IS NULL",
        (policy_epoch, f"decision_policy:{path_id}"),
    ).fetchone()
    if row is None or not row["effective_values_hash"]:
        raise ValueError(f"policy epoch unavailable: {policy_epoch}")
    return build_behavior_contract_hash(
        path_id=path_id,
        policy_hash=str(row["effective_values_hash"]),
        target_horizon=target_horizon,
        outcome_contract_version=outcome_contract_version,
        metric_version=metric_version,
    )
