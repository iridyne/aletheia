"""Prospective identity for the relative-rank Shadow comparison."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from collections.abc import Mapping, Sequence

from aletheia.config.schema import UniverseConfig
from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch
from aletheia.universe.continuous_scoring import ContinuousScoringPolicy
from aletheia.universe.outcome_economics import COST_MODEL_VERSION
from aletheia.universe.relative_rank_persistence import (
    RelativeRankShadowBundle,
    build_relative_rank_shadow_bundle,
    relative_rank_feature_source_fingerprint,
)
from aletheia.universe.relative_rank_shadow import RelativeRankBatch
from aletheia.universe.twin_persistence import TwinExperimentContext
from aletheia.universe.twin_scoring import TwinShadowInput


RELATIVE_RANK_PATH_ID = "universe_relative_rank_shadow_comparison"
RELATIVE_RANK_EXPERIMENT_CONTRACT = "universe-relative-rank-experiment/v1"
RELATIVE_RANK_ASSIGNMENT_CONTRACT = "sha256-relative-rank-cluster-assignment/v1"
RELATIVE_RANK_PRIMARY_HORIZON = "4h"
RELATIVE_RANK_OUTCOME_CONTRACT = "fixed-horizon-assigned-selection-net/v2"
RELATIVE_RANK_METRIC_VERSION = "universe-relative-rank-cluster-itt-utility/v1"
RELATIVE_RANK_CONTROL_POLICY = "universe_step_v1"
RELATIVE_RANK_TREATMENT_POLICY = "relative_rank_50_30_20"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _behavior_hash(policy_hash: str) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "path_id": RELATIVE_RANK_PATH_ID,
                "contract": RELATIVE_RANK_EXPERIMENT_CONTRACT,
                "policy_hash": policy_hash,
                "horizon": RELATIVE_RANK_PRIMARY_HORIZON,
                "outcome": RELATIVE_RANK_OUTCOME_CONTRACT,
                "cost": COST_MODEL_VERSION,
                "metric": RELATIVE_RANK_METRIC_VERSION,
            }
        ).encode("utf-8")
    ).hexdigest()


def assign_relative_rank_cluster(policy_hash: str, scanner_run_id: str) -> tuple[str, float]:
    material = f"{RELATIVE_RANK_ASSIGNMENT_CONTRACT}:{policy_hash}:{scanner_run_id}"
    draw = int(hashlib.sha256(material.encode("utf-8")).hexdigest()[:16], 16)
    treatment = draw / float(0xFFFFFFFFFFFFFFFF) < 0.5
    return ("treatment" if treatment else "control"), 0.5


def _context(
    relative_batch: RelativeRankBatch,
    *,
    policy: ContinuousScoringPolicy,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    provider_id: str,
) -> TwinExperimentContext:
    effective = {
        "contract_version": RELATIVE_RANK_EXPERIMENT_CONTRACT,
        "behavior_version": relative_batch.behavior_version,
        "authority": "shadow_only",
        "control_policy": RELATIVE_RANK_CONTROL_POLICY,
        "treatment_policy": RELATIVE_RANK_TREATMENT_POLICY,
        "weights": {"opportunity": 0.5, "executability": 0.3, "robustness": 0.2},
        "minimum_candidate_count": relative_batch.min_candidate_count,
        "continuous_policy": asdict(policy),
        "allocation_policy": {
            "universe": universe_config.model_dump(mode="json"),
            "enabled_markets": dict(sorted(enabled_markets.items())),
            "preferred_market_type": preferred_market_type,
            "family_tolerances": dict(sorted((family_tolerances or {}).items())),
        },
        "assignment": {
            "contract_version": RELATIVE_RANK_ASSIGNMENT_CONTRACT,
            "unit": "complete_scanner_run",
            "propensity": 0.5,
        },
        "primary_horizon": RELATIVE_RANK_PRIMARY_HORIZON,
        "outcome_contract_version": RELATIVE_RANK_OUTCOME_CONTRACT,
        "cost_model_version": COST_MODEL_VERSION,
        "metric_version": RELATIVE_RANK_METRIC_VERSION,
        "feature_source_fingerprint": relative_rank_feature_source_fingerprint(provider_id=provider_id),
        "promotion_eligible": False,
    }
    epoch = build_policy_epoch(
        f"decision_policy:{RELATIVE_RANK_PATH_ID}",
        effective,
        affected_path_ids=(RELATIVE_RANK_PATH_ID,),
    )
    epoch_id = persist_policy_epoch(epoch)
    arm, propensity = assign_relative_rank_cluster(epoch.epoch_hash, relative_batch.scanner_run_id)
    return TwinExperimentContext(
        experiment_id=f"universe-relative-rank-shadow-{epoch_id}",
        policy_epoch_id=epoch_id,
        behavior_contract_hash=_behavior_hash(epoch.epoch_hash),
        assignment_unit_key=relative_batch.scanner_run_id,
        assignment_contract_version=RELATIVE_RANK_ASSIGNMENT_CONTRACT,
        assignment_arm=arm,
        assignment_propensity=propensity,
        treatment_coefficient=1.0 if arm == "treatment" else 0.0,
        treatment_policy=RELATIVE_RANK_TREATMENT_POLICY,
        control_policy=RELATIVE_RANK_CONTROL_POLICY,
        primary_horizon=RELATIVE_RANK_PRIMARY_HORIZON,
        outcome_contract_version=RELATIVE_RANK_OUTCOME_CONTRACT,
        metric_version=RELATIVE_RANK_METRIC_VERSION,
    )


def build_prospective_relative_rank_bundle(
    relative_batch: RelativeRankBatch,
    inputs: Sequence[TwinShadowInput],
    *,
    authority_snapshot_id: str | None,
    policy: ContinuousScoringPolicy,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    provider_id: str,
    outcome_venue: str,
    regime_state: str | None,
    captured_at: str | None = None,
) -> RelativeRankShadowBundle:
    experiment = _context(
        relative_batch,
        policy=policy,
        universe_config=universe_config,
        enabled_markets=enabled_markets,
        preferred_market_type=preferred_market_type,
        family_tolerances=family_tolerances,
        provider_id=provider_id,
    )
    return build_relative_rank_shadow_bundle(
        relative_batch,
        inputs,
        authority_snapshot_id=authority_snapshot_id,
        experiment=experiment,
        provider_id=provider_id,
        outcome_venue=outcome_venue,
        regime_state=regime_state,
        captured_at=captured_at,
    )


__all__ = [
    "RELATIVE_RANK_PATH_ID",
    "assign_relative_rank_cluster",
    "build_prospective_relative_rank_bundle",
]
