"""Prospective identity for the non-authoritative Universe curve comparison.

This module opens a measurement window and assigns complete scanner clusters.
It never changes the scorer used by allocation, emits no Opportunity, and does
not register the future factor-suppression Learning path.
"""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from typing import Mapping, Sequence

from aletheia.config.schema import UniverseConfig
from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch
from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
from aletheia.universe.twin_persistence import (
    TwinExperimentContext,
    TwinShadowBundle,
    build_twin_shadow_bundle,
    feature_source_fingerprint,
)
from aletheia.universe.outcome_economics import COST_MODEL_VERSION
from aletheia.universe.twin_scoring import TwinShadowInput, TwinShadowRecord


TWIN_EXPERIMENT_PATH_ID = "universe_continuous_shadow_comparison"
TWIN_EXPERIMENT_CONTRACT_VERSION = "universe-continuous-shadow-experiment/v1"
TWIN_EXPERIMENT_BEHAVIOR_VERSION = "universe-continuous-shadow-comparison/v1"
TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT = "sha256-cluster-assignment/v1"
TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY = 0.5
TWIN_EXPERIMENT_PRIMARY_HORIZON = "4h"
TWIN_EXPERIMENT_OUTCOME_CONTRACT = "fixed-horizon-candidate-net/v1"
TWIN_EXPERIMENT_COST_MODEL_VERSION = COST_MODEL_VERSION
TWIN_EXPERIMENT_METRIC_VERSION = (
    "universe-continuous-pessimistic-cluster-allocation-utility/v1"
)
TWIN_EXPERIMENT_TREATMENT_POLICY = "continuous_pessimistic"
TWIN_EXPERIMENT_CONTROL_POLICY = "universe_step_v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def twin_behavior_contract_hash(policy_hash: str) -> str:
    payload = {
        "schema": "universe-shadow-behavior-contract/v1",
        "path_id": TWIN_EXPERIMENT_PATH_ID,
        "behavior_version": TWIN_EXPERIMENT_BEHAVIOR_VERSION,
        "policy_hash": policy_hash,
        "primary_horizon": TWIN_EXPERIMENT_PRIMARY_HORIZON,
        "outcome_contract_version": TWIN_EXPERIMENT_OUTCOME_CONTRACT,
        "cost_model_version": TWIN_EXPERIMENT_COST_MODEL_VERSION,
        "metric_version": TWIN_EXPERIMENT_METRIC_VERSION,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def assign_twin_shadow_cluster(
    policy_hash: str, scanner_run_id: str
) -> tuple[str, float]:
    """Assign one complete scanner cluster without touching a shared RNG."""

    material = (
        f"{TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT}:{policy_hash}:{scanner_run_id}"
    )
    draw = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    fraction = draw / float(0xFFFFFFFFFFFFFFFF)
    arm = (
        "treatment"
        if fraction < TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY
        else "control"
    )
    logged_propensity = (
        TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY
        if arm == "treatment"
        else 1.0 - TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY
    )
    return arm, logged_propensity


def _experiment_context(
    records: Sequence[TwinShadowRecord],
    *,
    scanner_run_id: str,
    policy: ContinuousScoringPolicy,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    provider_id: str,
) -> TwinExperimentContext:
    if not records:
        raise ValueError("twin_experiment_empty_cluster")
    first = records[0]
    source_fingerprint = feature_source_fingerprint(provider_id=provider_id)
    effective_values = {
        "contract_version": TWIN_EXPERIMENT_CONTRACT_VERSION,
        "behavior_version": TWIN_EXPERIMENT_BEHAVIOR_VERSION,
        "authority_behavior_version": first.authority_behavior_version,
        "shadow_behavior_version": first.shadow_behavior_version,
        "authority": "shadow_only",
        "control_policy": TWIN_EXPERIMENT_CONTROL_POLICY,
        "treatment_policy": TWIN_EXPERIMENT_TREATMENT_POLICY,
        "continuous_policy": asdict(policy),
        "allocation_policy": {
            "universe": universe_config.model_dump(mode="json"),
            "enabled_markets": dict(sorted(enabled_markets.items())),
            "preferred_market_type": preferred_market_type,
            "family_tolerances": dict(sorted((family_tolerances or {}).items())),
        },
        "assignment": {
            "contract_version": TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT,
            "unit": "complete_scanner_run",
            "propensity": TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY,
        },
        "primary_horizon": TWIN_EXPERIMENT_PRIMARY_HORIZON,
        "outcome_contract_version": TWIN_EXPERIMENT_OUTCOME_CONTRACT,
        "cost_model_version": TWIN_EXPERIMENT_COST_MODEL_VERSION,
        "metric_version": TWIN_EXPERIMENT_METRIC_VERSION,
        "feature_source_fingerprint": source_fingerprint,
        "availability_profile_id": None,
        "promotion_eligible": False,
    }
    epoch = build_policy_epoch(
        f"decision_policy:{TWIN_EXPERIMENT_PATH_ID}",
        effective_values,
        affected_path_ids=(TWIN_EXPERIMENT_PATH_ID,),
    )
    epoch_id = persist_policy_epoch(epoch)
    arm, propensity = assign_twin_shadow_cluster(epoch.epoch_hash, scanner_run_id)
    return TwinExperimentContext(
        experiment_id=f"universe-shadow-{epoch_id}",
        policy_epoch_id=epoch_id,
        behavior_contract_hash=twin_behavior_contract_hash(epoch.epoch_hash),
        assignment_unit_key=scanner_run_id,
        assignment_contract_version=TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT,
        assignment_arm=arm,
        assignment_propensity=propensity,
        treatment_coefficient=1.0 if arm == "treatment" else 0.0,
        treatment_policy=TWIN_EXPERIMENT_TREATMENT_POLICY,
        control_policy=TWIN_EXPERIMENT_CONTROL_POLICY,
        primary_horizon=TWIN_EXPERIMENT_PRIMARY_HORIZON,
        outcome_contract_version=TWIN_EXPERIMENT_OUTCOME_CONTRACT,
        metric_version=TWIN_EXPERIMENT_METRIC_VERSION,
        availability_profile_id=None,
    )


def build_prospective_twin_shadow_bundle(
    records: Sequence[TwinShadowRecord],
    inputs: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    authority_snapshot_id: str | None,
    policy: ContinuousScoringPolicy,
    allocator_random_state: object,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    regime_state: str | None,
    provider_id: str,
    outcome_venue: str,
    captured_at: str | None = None,
) -> TwinShadowBundle:
    """Open/resolve the exact Shadow epoch, then build one complete cluster."""

    experiment = _experiment_context(
        records,
        scanner_run_id=scanner_run_id,
        policy=policy,
        universe_config=universe_config,
        enabled_markets=enabled_markets,
        preferred_market_type=preferred_market_type,
        family_tolerances=family_tolerances,
        provider_id=provider_id,
    )
    return build_twin_shadow_bundle(
        records,
        inputs,
        scanner_run_id=scanner_run_id,
        authority_snapshot_id=authority_snapshot_id,
        policy=policy,
        allocator_random_state=allocator_random_state,
        universe_config=universe_config,
        enabled_markets=enabled_markets,
        preferred_market_type=preferred_market_type,
        family_tolerances=family_tolerances,
        regime_state=regime_state,
        provider_id=provider_id,
        captured_at=captured_at,
        experiment=experiment,
        outcome_venue=outcome_venue,
    )


__all__ = [
    "TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT",
    "TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY",
    "TWIN_EXPERIMENT_BEHAVIOR_VERSION",
    "TWIN_EXPERIMENT_CONTRACT_VERSION",
    "TWIN_EXPERIMENT_COST_MODEL_VERSION",
    "TWIN_EXPERIMENT_CONTROL_POLICY",
    "TWIN_EXPERIMENT_METRIC_VERSION",
    "TWIN_EXPERIMENT_OUTCOME_CONTRACT",
    "TWIN_EXPERIMENT_PATH_ID",
    "TWIN_EXPERIMENT_PRIMARY_HORIZON",
    "TWIN_EXPERIMENT_TREATMENT_POLICY",
    "assign_twin_shadow_cluster",
    "build_prospective_twin_shadow_bundle",
    "twin_behavior_contract_hash",
]
