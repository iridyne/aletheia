"""Durable, replayable evidence for the non-authoritative Universe twin scorer.

This module has no runtime authority. It samples an already-computed twin
batch, persists compact scorer/allocation diagnostics, and can replay the pure
path for integrity checks. The legacy Task-5 builder creates no outcome; a
caller-supplied Task-6 experiment context may attach its one declared canonical
outcome. Assignment and propensity are never inferred here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from aletheia.config.schema import UniverseConfig
from aletheia.storage.models import (
    FilterCandidateOutcome,
    UniverseTwinShadowBatch,
    UniverseTwinShadowEvidence,
)
from aletheia.universe.continuous_scoring import (
    ContinuousScoringPolicy,
    raw_feature_declarations,
)
from aletheia.universe.twin_allocation import (
    TWIN_ALLOCATION_REPLAY_CONTRACT,
    replay_twin_shadow_allocations,
)
from aletheia.universe.twin_scoring import (
    TwinShadowInput,
    TwinShadowRecord,
    annotate_authority_selection,
    build_twin_shadow_batch,
)


TWIN_FEATURE_SOURCE_CONTRACT = "universe-twin-feature-source-v1"
TWIN_METRIC_VERSION = "universe-continuous-shadow-cluster-allocation-utility-v1"
TWIN_SHADOW_CAPTURE_INTERVAL_SEC = 55 * 60
TWIN_SHADOW_MAX_TOP_SENSITIVITIES = 8
TWIN_SHADOW_MAX_OUTPUT_BYTES = 4 * 1024
TWIN_SHADOW_MAX_INPUT_BYTES = 2 * 1024

_IDENTITY_AND_MARKET_FIELDS = frozenset(
    {
        "feature_snapshot_id",
        "asset_id",
        "symbol",
        "market_type",
        "venue",
        "last_price",
        "price",
        "close",
    }
)
_SCANNER_FIELDS = frozenset(
    {"scanner_family", "scanner_direction", "direction", "opportunity_score"}
)
_AUDIT_FIELDS = frozenset(
    {"contract", "effective_opportunity_multiplier", "calibration_authority_applied"}
)
_TASK5_BLOCKERS = (
    "assignment_not_available",
    "authority_allocator_random_lineage_not_available",
    "availability_profile_not_available",
    "feature_observation_lineage_not_available",
    "fixed_horizon_outcome_not_collected",
    "policy_epoch_not_available",
    "propensity_not_available",
    "sampled_diagnostic_only",
)
_TASK5_UNAVAILABLE_IDENTITIES = (
    "policy_epoch",
    "availability_profile",
    "authority_allocator_random_lineage",
    "feature_observation_lineage",
    "fixed_horizon_outcome",
    "assignment",
    "propensity",
)


@dataclass(frozen=True)
class TwinShadowBundle:
    batch: UniverseTwinShadowBatch
    evidence: tuple[UniverseTwinShadowEvidence, ...]
    outcomes: tuple[FilterCandidateOutcome, ...] = ()


@dataclass(frozen=True)
class TwinExperimentContext:
    """Pre-registered identity for the prospective Shadow comparison only."""

    experiment_id: str
    policy_epoch_id: str
    behavior_contract_hash: str
    assignment_unit_key: str
    assignment_contract_version: str
    assignment_arm: str
    assignment_propensity: float
    treatment_coefficient: float
    treatment_policy: str
    control_policy: str
    primary_horizon: str
    outcome_contract_version: str
    metric_version: str
    availability_profile_id: str | None = None


@dataclass(frozen=True)
class TwinReplayReport:
    scanner_run_id: str
    candidate_count: int
    matched_count: int
    mismatched_candidate_keys: tuple[str, ...]
    batch_hash_matches: bool

    @property
    def matches(self) -> bool:
        return self.batch_hash_matches and not self.mismatched_candidate_keys


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _safe_value(item) for key, item in value.items()}
    return None


def _selected(source: Mapping[str, object], allowed: frozenset[str]) -> dict[str, object]:
    return {
        key: _safe_value(source[key])
        for key in sorted(allowed)
        if key in source
    }


def _safe_input(item: TwinShadowInput) -> dict[str, object]:
    feature_fields = frozenset(raw_feature_declarations()) | _IDENTITY_AND_MARKET_FIELDS
    scanner = _selected(item.authority_scanner_result, _SCANNER_FIELDS)
    reasons = item.authority_scanner_result.get("reasons")
    scanner["reasons"] = (
        ["no chase"]
        if isinstance(reasons, Sequence)
        and not isinstance(reasons, (str, bytes))
        and any("no chase" in str(reason).lower() for reason in reasons)
        else []
    )
    return {
        "feature": _selected(item.feature, feature_fields),
        "authority_scanner_result": scanner,
        "authority_total_score": float(item.authority_total_score),
        "authority_score_audit": _selected(item.authority_score_audit, _AUDIT_FIELDS),
    }


def feature_source_fingerprint(*, provider_id: str) -> str:
    manifest = {
        feature_id: asdict(declaration)
        for feature_id, declaration in sorted(raw_feature_declarations().items())
    }
    return _sha256(
        {
            "contract": TWIN_FEATURE_SOURCE_CONTRACT,
            "provider_id": provider_id or "not_available",
            "availability_profile_id": "not_available",
            "raw_feature_manifest": manifest,
        }
    )


def encode_random_state(state: object) -> str:
    return _canonical_json(state)


def _tuples(value: object) -> object:
    if isinstance(value, list):
        return tuple(_tuples(item) for item in value)
    return value


def decode_random_state(payload: str) -> object:
    return _tuples(json.loads(payload))


def _candidate_key(scanner_run_id: str, feature: Mapping[str, object]) -> str:
    feature_id = feature.get("feature_snapshot_id")
    asset_id = feature.get("asset_id")
    symbol = str(feature.get("symbol") or "")
    market_type = str(feature.get("market_type") or "spot")
    source_id = feature_id or asset_id or "not_available"
    return (
        f"universe-twin:{scanner_run_id or 'not_available'}:"
        f"{source_id}:{market_type}:{symbol or 'not_available'}"
    )


def _outcome_candidate_key(record: TwinShadowRecord) -> str:
    return f"{record.candidate_key}:prospective"


def _compact_output_material(
    record: TwinShadowRecord,
    *,
    candidate_index: int,
    experiment: TwinExperimentContext | None = None,
    outcome_candidate_key: str | None = None,
    assigned_policy_selected: bool | None = None,
) -> dict[str, object]:
    shadow = record.shadow
    top_sensitivities = sorted(
        shadow.sensitivities,
        key=lambda item: (-abs(item.local_score_sensitivity), item.raw_feature_id),
    )[:TWIN_SHADOW_MAX_TOP_SENSITIVITIES]
    if experiment is None:
        blockers = sorted(
            {
                *(reason for reason in record.blocking_reasons if reason != "task4_diagnostic_only"),
                *_TASK5_BLOCKERS,
            }
        )
        unavailable_identities = list(_TASK5_UNAVAILABLE_IDENTITIES)
    else:
        blockers = sorted(
            {
                *(reason for reason in record.blocking_reasons if reason != "task4_diagnostic_only"),
                "anytime_valid_lower_bound_not_available",
                "availability_profile_not_available",
                "feature_observation_lineage_not_available",
                "independent_dr_outcome_model_not_available",
                "noninferiority_margins_not_configured",
                "prospective_shadow_only",
            }
        )
        unavailable_identities = [
            "availability_profile",
            "feature_observation_lineage",
            "independent_dr_outcome_model",
            "anytime_valid_lower_bound",
            "noninferiority_margins",
        ]
    evidence_contract: dict[str, object] = {
        "shadow_authority_applied": False,
        "promotion_eligible": False,
        "unavailable_identities": unavailable_identities,
        "blocking_reasons": blockers,
        "fixed_horizon_outcomes_collected": experiment is not None,
    }
    if experiment is not None:
        evidence_contract.update(
            {
                "authority_applied": False,
                "experiment_id": experiment.experiment_id,
                "policy_epoch_id": experiment.policy_epoch_id,
                "behavior_contract_hash": experiment.behavior_contract_hash,
                "assignment_unit_key": experiment.assignment_unit_key,
                "assignment_arm": experiment.assignment_arm,
                "assignment_propensity": experiment.assignment_propensity,
                "treatment_coefficient": experiment.treatment_coefficient,
                "primary_horizon": experiment.primary_horizon,
                "outcome_contract_version": experiment.outcome_contract_version,
                "metric_version": experiment.metric_version,
                "outcome_candidate_key": outcome_candidate_key,
                "assigned_policy_selected": assigned_policy_selected,
            }
        )
    return {
        "record_version": record.record_version,
        "candidate_index": candidate_index,
        "authority_behavior_version": record.authority_behavior_version,
        "shadow_behavior_version": record.shadow_behavior_version,
        "authority": {
            "family": record.authority_family,
            "direction": record.authority_direction,
            "opportunity_score": record.authority_opportunity_score,
            "total_score": record.authority_total_score,
            "rank": record.authority_rank,
            "fine_filter_passed": record.fine_filter_passed,
            "selected": record.authority_selected,
        },
        "shadow": {
            "valid": shadow.valid,
            "invalid_reason": shadow.invalid_reason,
            "point_family": shadow.point_family,
            "pessimistic_family": shadow.pessimistic_family,
            "point_direction": shadow.point_direction,
            "pessimistic_direction": shadow.pessimistic_direction,
            "point_score": shadow.point_score,
            "pessimistic_score": shadow.pessimistic_score,
            "point_rank": record.shadow_point_rank,
            "pessimistic_rank": record.shadow_pessimistic_rank,
            "point_selected": record.shadow_point_selected,
            "pessimistic_selected": record.shadow_pessimistic_selected,
            "missingness_delta": shadow.missingness_delta,
            "missingness_feature_ids": list(shadow.missingness_feature_ids),
            "observed_signal_group_count": shadow.observed_signal_group_count,
            "concentration_share": shadow.concentration_share,
            "joint_neutral_score_delta": shadow.joint_neutral_score_delta,
            "interaction_residual": shadow.interaction_residual,
            "flags": list(shadow.flags),
        },
        "thresholds": [
            [
                item.threshold_id,
                item.threshold,
                item.authority_passed,
                item.shadow_point_passed,
                item.shadow_pessimistic_passed,
            ]
            for item in record.threshold_comparisons
        ],
        "top_sensitivities": [
            [
                item.raw_feature_id,
                item.evidence_group_id,
                item.local_score_sensitivity,
                item.score_delta_to_neutral,
                item.budget_exceeded,
            ]
            for item in top_sensitivities
        ],
        "group_sensitivities": [
            [group_id, sensitivity]
            for group_id, sensitivity in shadow.group_sensitivities
        ],
        "evidence_contract": evidence_contract,
    }


def _allocation_context(
    *,
    universe_config: UniverseConfig,
    enabled_markets: Mapping[str, bool],
    preferred_market_type: str,
    family_tolerances: Mapping[str, float] | None,
    regime_state: str | None,
) -> dict[str, object]:
    return {
        "universe_config": universe_config.model_dump(mode="json"),
        "exchange": {
            "enabled_markets": dict(sorted(enabled_markets.items())),
            "preferred_market_type": preferred_market_type,
        },
        "family_tolerances": dict(sorted((family_tolerances or {}).items())),
        "regime_state": regime_state,
    }


def build_twin_shadow_bundle(
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
    captured_at: str | None = None,
    experiment: TwinExperimentContext | None = None,
    outcome_venue: str | None = None,
) -> TwinShadowBundle:
    """Build one compact diagnostic sample in memory; performs no writes."""

    captured = captured_at or datetime.now(timezone.utc).isoformat()
    safe_inputs = {_candidate_key(scanner_run_id, item.feature): _safe_input(item) for item in inputs}
    evidence: list[UniverseTwinShadowEvidence] = []
    outcomes: list[FilterCandidateOutcome] = []

    for candidate_index, record in enumerate(records):
        safe_input = safe_inputs.get(record.candidate_key)
        if safe_input is None:
            raise ValueError(f"twin_input_not_available:{record.candidate_key}")
        outcome_key = None
        assigned_policy_selected = None
        if experiment is not None:
            if experiment.assignment_arm == "treatment":
                assigned_policy_selected = record.shadow_pessimistic_selected
            elif experiment.assignment_arm == "control":
                assigned_policy_selected = record.authority_selected
            else:
                raise ValueError("twin_experiment_assignment_arm_invalid")
            feature = safe_input["feature"]
            direction = str(record.authority_direction or "").lower()
            venue = str(feature.get("venue") or outcome_venue or "")
            symbol = str(record.symbol or "")
            entry_price = feature.get("last_price") or feature.get("price") or feature.get("close")
            try:
                price = float(entry_price)
            except (TypeError, ValueError):
                price = 0.0
            if direction not in {"long", "short"} or not venue or not symbol or price <= 0:
                raise ValueError(
                    f"twin_experiment_outcome_identity_not_available:{record.candidate_key}"
                )
            outcome_key = _outcome_candidate_key(record)
            from aletheia.analytics.filter_evidence import build_candidate_outcomes

            candidate_outcomes = build_candidate_outcomes(
                outcome_candidate_key=outcome_key,
                venue=venue,
                symbol=symbol,
                market_type=record.market_type,
                direction=direction,
                scanner_family=record.authority_family,
                regime_tag=regime_state,
                entry_price_usd=price,
                entry_spread_bps=feature.get("spread_bps"),
                funding_rate_at_entry=feature.get("funding_rate"),
                entry_ts=captured,
                allocation_selected=assigned_policy_selected,
                horizons=(experiment.primary_horizon,),
            )
            if len(candidate_outcomes) != 1:
                raise ValueError(
                    f"twin_experiment_primary_outcome_not_available:{record.candidate_key}"
                )
            outcomes.extend(candidate_outcomes)
        material = _compact_output_material(
            record,
            candidate_index=candidate_index,
            experiment=experiment,
            outcome_candidate_key=outcome_key,
            assigned_policy_selected=assigned_policy_selected,
        )
        compact_output_json = _canonical_json(material)
        input_json = _canonical_json(safe_input)
        if len(compact_output_json.encode("utf-8")) > TWIN_SHADOW_MAX_OUTPUT_BYTES:
            raise ValueError("twin_compact_output_too_large")
        if len(input_json.encode("utf-8")) > TWIN_SHADOW_MAX_INPUT_BYTES:
            raise ValueError("twin_replay_input_too_large")
        evidence.append(
            UniverseTwinShadowEvidence(
                id=_sha256({"scanner_run_id": scanner_run_id, "candidate_key": record.candidate_key})[:24],
                scanner_run_id=scanner_run_id,
                candidate_key=record.candidate_key,
                candidate_index=candidate_index,
                feature_snapshot_id=record.feature_snapshot_id,
                asset_id=record.asset_id,
                symbol=record.symbol,
                market_type=record.market_type,
                experiment_id=experiment.experiment_id if experiment else None,
                policy_epoch_id=experiment.policy_epoch_id if experiment else None,
                behavior_contract_hash=(
                    experiment.behavior_contract_hash if experiment else None
                ),
                availability_profile_id=(
                    experiment.availability_profile_id if experiment else None
                ),
                assignment_unit_key=(
                    experiment.assignment_unit_key if experiment else None
                ),
                assignment_arm=experiment.assignment_arm if experiment else None,
                assignment_propensity=(
                    experiment.assignment_propensity if experiment else None
                ),
                treatment_coefficient=(
                    experiment.treatment_coefficient if experiment else None
                ),
                primary_horizon=experiment.primary_horizon if experiment else None,
                outcome_contract_version=(
                    experiment.outcome_contract_version if experiment else None
                ),
                metric_version=experiment.metric_version if experiment else None,
                outcome_candidate_key=outcome_key,
                authority_total_score=record.authority_total_score,
                shadow_point_score=record.shadow.point_score if record.shadow.valid else None,
                shadow_pessimistic_score=record.shadow.pessimistic_score if record.shadow.valid else None,
                authority_rank=record.authority_rank,
                shadow_point_rank=record.shadow_point_rank,
                shadow_pessimistic_rank=record.shadow_pessimistic_rank,
                fine_filter_passed=record.fine_filter_passed,
                authority_selected=record.authority_selected,
                shadow_point_selected=record.shadow_point_selected,
                shadow_pessimistic_selected=record.shadow_pessimistic_selected,
                assigned_policy_selected=assigned_policy_selected,
                shadow_authority_applied=False,
                promotion_eligible=False,
                shadow_valid=record.shadow.valid,
                compact_output_json=compact_output_json,
                input_json=input_json,
                record_hash=_sha256({"output": material, "input": safe_input}),
                captured_at=captured,
            )
        )

    context = _allocation_context(
        universe_config=universe_config,
        enabled_markets=enabled_markets,
        preferred_market_type=preferred_market_type,
        family_tolerances=family_tolerances,
        regime_state=regime_state,
    )
    policy_json = _canonical_json(asdict(policy))
    random_state_json = encode_random_state(allocator_random_state)
    source_fingerprint = feature_source_fingerprint(provider_id=provider_id)
    first = records[0] if records else None
    record_version = first.record_version if first else "universe-twin-shadow-record-v1"
    authority_behavior_version = first.authority_behavior_version if first else "not_available"
    shadow_behavior_version = first.shadow_behavior_version if first else "not_available"
    if experiment is None:
        batch_blockers = list(_TASK5_BLOCKERS)
    else:
        batch_blockers = sorted(
            {
                reason
                for item in evidence
                for reason in json.loads(item.compact_output_json)["evidence_contract"][
                    "blocking_reasons"
                ]
            }
        )
    batch_material = {
        "scanner_run_id": scanner_run_id,
        "authority_snapshot_id": authority_snapshot_id,
        "record_version": record_version,
        "authority_behavior_version": authority_behavior_version,
        "shadow_behavior_version": shadow_behavior_version,
        "allocation_replay_contract": TWIN_ALLOCATION_REPLAY_CONTRACT,
        "policy_epoch_id": experiment.policy_epoch_id if experiment else None,
        "feature_source_fingerprint": source_fingerprint,
        "availability_profile_id": (
            experiment.availability_profile_id if experiment else None
        ),
        "metric_version": (
            experiment.metric_version if experiment else TWIN_METRIC_VERSION
        ),
        "policy": json.loads(policy_json),
        "allocation_context": context,
        "allocator_random_state": json.loads(random_state_json),
        "candidate_count": len(evidence),
        "promotion_eligible": False,
        "blocking_reasons": batch_blockers,
        "captured_at": captured,
        "record_hashes": [
            item.record_hash for item in sorted(evidence, key=lambda value: value.candidate_key)
        ],
    }
    if experiment is not None:
        batch_material.update(
            {
                "experiment_id": experiment.experiment_id,
                "behavior_contract_hash": experiment.behavior_contract_hash,
                "assignment_unit_key": experiment.assignment_unit_key,
                "assignment_contract_version": experiment.assignment_contract_version,
                "assignment_arm": experiment.assignment_arm,
                "assignment_propensity": experiment.assignment_propensity,
                "treatment_coefficient": experiment.treatment_coefficient,
                "treatment_policy": experiment.treatment_policy,
                "control_policy": experiment.control_policy,
                "primary_horizon": experiment.primary_horizon,
                "outcome_contract_version": experiment.outcome_contract_version,
            }
        )
    batch = UniverseTwinShadowBatch(
        scanner_run_id=scanner_run_id,
        authority_snapshot_id=authority_snapshot_id,
        experiment_id=experiment.experiment_id if experiment else None,
        record_version=record_version,
        authority_behavior_version=authority_behavior_version,
        shadow_behavior_version=shadow_behavior_version,
        allocation_replay_contract=TWIN_ALLOCATION_REPLAY_CONTRACT,
        policy_epoch_id=experiment.policy_epoch_id if experiment else None,
        behavior_contract_hash=(
            experiment.behavior_contract_hash if experiment else None
        ),
        assignment_unit_key=(
            experiment.assignment_unit_key if experiment else None
        ),
        assignment_contract_version=(
            experiment.assignment_contract_version if experiment else None
        ),
        assignment_arm=experiment.assignment_arm if experiment else None,
        assignment_propensity=(
            experiment.assignment_propensity if experiment else None
        ),
        treatment_coefficient=(
            experiment.treatment_coefficient if experiment else None
        ),
        treatment_policy=experiment.treatment_policy if experiment else None,
        control_policy=experiment.control_policy if experiment else None,
        primary_horizon=experiment.primary_horizon if experiment else None,
        outcome_contract_version=(
            experiment.outcome_contract_version if experiment else None
        ),
        feature_source_fingerprint=source_fingerprint,
        availability_profile_id=(
            experiment.availability_profile_id if experiment else None
        ),
        metric_version=(
            experiment.metric_version if experiment else TWIN_METRIC_VERSION
        ),
        allocator_random_state_json=random_state_json,
        policy_json=policy_json,
        allocation_context_json=_canonical_json(context),
        candidate_count=len(evidence),
        promotion_eligible=False,
        blocking_reasons_json=_canonical_json(batch_blockers),
        batch_hash=_sha256(batch_material),
        captured_at=captured,
    )
    return TwinShadowBundle(batch, tuple(evidence), tuple(outcomes))


def _dedupe_for_replay(values: list[dict], app_config: Any) -> list[dict]:
    preferred = getattr(app_config.exchange, "preferred_market_type", "auto")
    enabled = app_config.exchange.enabled_markets
    spot_enabled = enabled.get("spot", True)
    perp_enabled = enabled.get("usdt_futures", False)
    preferred_market = "spot" if preferred == "spot" else "perp"

    def market_rank(item: dict) -> int:
        if spot_enabled and perp_enabled:
            return 0 if item.get("market_type", "spot") == preferred_market else 1
        return 0

    selected: dict[str, dict] = {}
    for item in values:
        symbol = str(item["symbol"])
        current = selected.get(symbol)
        if current is None or (
            market_rank(item), -float(item.get("total_score", 0.0))
        ) < (
            market_rank(current), -float(current.get("total_score", 0.0))
        ):
            selected[symbol] = item
    return sorted(selected.values(), key=lambda item: item["total_score"], reverse=True)


def _experiment_context_from_batch(
    batch: Mapping[str, object],
) -> TwinExperimentContext | None:
    if not batch.get("experiment_id") and not batch.get("policy_epoch_id"):
        return None
    required = {
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "assignment_unit_key",
        "assignment_contract_version",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "treatment_policy",
        "control_policy",
        "primary_horizon",
        "outcome_contract_version",
        "metric_version",
    }
    missing = sorted(key for key in required if batch.get(key) is None)
    if missing:
        raise ValueError(f"twin_experiment_identity_incomplete:{','.join(missing)}")
    return TwinExperimentContext(
        experiment_id=str(batch["experiment_id"]),
        policy_epoch_id=str(batch["policy_epoch_id"]),
        behavior_contract_hash=str(batch["behavior_contract_hash"]),
        assignment_unit_key=str(batch["assignment_unit_key"]),
        assignment_contract_version=str(batch["assignment_contract_version"]),
        assignment_arm=str(batch["assignment_arm"]),
        assignment_propensity=float(batch["assignment_propensity"]),
        treatment_coefficient=float(batch["treatment_coefficient"]),
        treatment_policy=str(batch["treatment_policy"]),
        control_policy=str(batch["control_policy"]),
        primary_horizon=str(batch["primary_horizon"]),
        outcome_contract_version=str(batch["outcome_contract_version"]),
        metric_version=str(batch["metric_version"]),
        availability_profile_id=(
            str(batch["availability_profile_id"])
            if batch.get("availability_profile_id")
            else None
        ),
    )


def replay_twin_shadow_bundle(
    batch: Mapping[str, object],
    evidence_rows: Sequence[Mapping[str, object]],
) -> TwinReplayReport:
    """Recompute one persisted batch and compare its canonical record payloads."""

    scanner_run_id = str(batch["scanner_run_id"])
    experiment = _experiment_context_from_batch(batch)
    ordered_rows = sorted(evidence_rows, key=lambda row: int(row["candidate_index"]))
    if [int(row["candidate_index"]) for row in ordered_rows] != list(range(len(ordered_rows))):
        raise ValueError("twin_candidate_index_not_contiguous")
    inputs = []
    for row in ordered_rows:
        payload = json.loads(str(row["input_json"]))
        inputs.append(
            TwinShadowInput(
                feature=payload["feature"],
                authority_scanner_result=payload["authority_scanner_result"],
                authority_total_score=payload["authority_total_score"],
                authority_score_audit=payload["authority_score_audit"],
            )
        )
    policy = ContinuousScoringPolicy(**json.loads(str(batch["policy_json"])))
    replayed = build_twin_shadow_batch(inputs, scanner_run_id=scanner_run_id, base_policy=policy)
    by_key = {str(row["candidate_key"]): row for row in ordered_rows}
    fine = [item.feature for item in inputs if bool(by_key[_candidate_key(scanner_run_id, item.feature)]["fine_filter_passed"])]
    allocated = [item.feature for item in inputs if bool(by_key[_candidate_key(scanner_run_id, item.feature)]["authority_selected"])]
    replayed = annotate_authority_selection(replayed, fine_filtered=fine, allocated=allocated)

    context = json.loads(str(batch["allocation_context_json"]))
    exchange = context["exchange"]
    app_config = SimpleNamespace(
        exchange=SimpleNamespace(
            enabled_markets=exchange["enabled_markets"],
            preferred_market_type=exchange["preferred_market_type"],
        )
    )
    replayed = replay_twin_shadow_allocations(
        replayed,
        inputs,
        scanner_run_id=scanner_run_id,
        universe_config=UniverseConfig.model_validate(context["universe_config"]),
        app_config=app_config,
        family_tolerances=context["family_tolerances"],
        regime_state=context["regime_state"],
        allocator_random_state=decode_random_state(str(batch["allocator_random_state_json"])),
        dedupe=_dedupe_for_replay,
    )

    mismatches: list[str] = []
    record_hashes_by_key: dict[str, str] = {}
    for record in replayed:
        row = by_key[record.candidate_key]
        replay_material = _compact_output_material(
            record,
            candidate_index=int(row["candidate_index"]),
            experiment=experiment,
            outcome_candidate_key=(
                _outcome_candidate_key(record) if experiment is not None else None
            ),
            assigned_policy_selected=(
                record.shadow_pessimistic_selected
                if experiment is not None and experiment.assignment_arm == "treatment"
                else record.authority_selected
                if experiment is not None
                else None
            ),
        )
        input_payload = json.loads(str(row["input_json"]))
        record_hash = _sha256({"output": replay_material, "input": input_payload})
        record_hashes_by_key[record.candidate_key] = record_hash
        if record_hash != str(row["record_hash"]):
            mismatches.append(record.candidate_key)

    batch_material = {
        "scanner_run_id": scanner_run_id,
        "authority_snapshot_id": batch.get("authority_snapshot_id"),
        "record_version": batch["record_version"],
        "authority_behavior_version": batch["authority_behavior_version"],
        "shadow_behavior_version": batch["shadow_behavior_version"],
        "allocation_replay_contract": batch["allocation_replay_contract"],
        "policy_epoch_id": batch.get("policy_epoch_id"),
        "feature_source_fingerprint": batch["feature_source_fingerprint"],
        "availability_profile_id": batch.get("availability_profile_id"),
        "metric_version": batch["metric_version"],
        "policy": json.loads(str(batch["policy_json"])),
        "allocation_context": context,
        "allocator_random_state": json.loads(str(batch["allocator_random_state_json"])),
        "candidate_count": int(batch["candidate_count"]),
        "promotion_eligible": bool(batch["promotion_eligible"]),
        "blocking_reasons": json.loads(str(batch["blocking_reasons_json"])),
        "captured_at": batch["captured_at"],
        "record_hashes": [record_hashes_by_key[key] for key in sorted(record_hashes_by_key)],
    }
    if experiment is not None:
        batch_material.update(
            {
                "experiment_id": batch.get("experiment_id"),
                "behavior_contract_hash": batch.get("behavior_contract_hash"),
                "assignment_unit_key": batch.get("assignment_unit_key"),
                "assignment_contract_version": batch.get(
                    "assignment_contract_version"
                ),
                "assignment_arm": batch.get("assignment_arm"),
                "assignment_propensity": batch.get("assignment_propensity"),
                "treatment_coefficient": batch.get("treatment_coefficient"),
                "treatment_policy": batch.get("treatment_policy"),
                "control_policy": batch.get("control_policy"),
                "primary_horizon": batch.get("primary_horizon"),
                "outcome_contract_version": batch.get("outcome_contract_version"),
            }
        )
    return TwinReplayReport(
        scanner_run_id=scanner_run_id,
        candidate_count=len(evidence_rows),
        matched_count=len(evidence_rows) - len(mismatches),
        mismatched_candidate_keys=tuple(sorted(mismatches)),
        batch_hash_matches=_sha256(batch_material) == str(batch["batch_hash"]),
    )


__all__ = [
    "TWIN_FEATURE_SOURCE_CONTRACT",
    "TWIN_METRIC_VERSION",
    "TWIN_SHADOW_CAPTURE_INTERVAL_SEC",
    "TWIN_SHADOW_MAX_INPUT_BYTES",
    "TWIN_SHADOW_MAX_OUTPUT_BYTES",
    "TwinReplayReport",
    "TwinExperimentContext",
    "TwinShadowBundle",
    "build_twin_shadow_bundle",
    "decode_random_state",
    "encode_random_state",
    "feature_source_fingerprint",
    "replay_twin_shadow_bundle",
]
