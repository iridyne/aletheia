"""Fail-closed paper-Learning authority resolver.

AT-01 verdicts measure contribution.  This module deliberately consumes their
identity and safety facts without reinterpreting ``positive`` as the legacy
``promote`` authority word.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from aletheia.config.loader import load_config
from aletheia.learning.cohorts import SUPPORTED_PATH_ID, SUPPORTED_PATH_IDS
from aletheia.learning.control import global_learning_refreeze_reason
from aletheia.learning.ledger import (
    get_latest_measurement_verdict,
    get_trial_admission_event,
    get_latest_verdict_for_cohort,
    get_record,
    linked_settled_exposure_counts,
)
from aletheia.learning.models import LearningExperiment
from aletheia.learning.behavior_contracts import current_behavior_contract_hash
from aletheia.learning.verdicts import (
    METRIC_VERSION,
    SHADOW_TRIAL_INTEGRITY_CONTRACT,
    SHADOW_TRIAL_INTEGRITY_REASON,
    SHADOW_TRIAL_MIN_MARKET_DAYS,
    SHADOW_TRIAL_MIN_SAMPLES_PER_ARM,
)
from aletheia.learning.fixed_look import (
    inference_authority_reason,
    inference_collection_reason,
)


PAPER_STAGES = ("paper_10", "paper_25", "paper_50", "paper_active")
PREVIOUS_STAGE = {
    "paper_10": "shadow",
    "paper_25": "paper_10",
    "paper_50": "paper_25",
    "paper_active": "paper_50",
}
MEASUREMENT_VERDICTS = frozenset({
    "positive", "neutral", "negative", "insufficient_data", "invalid",
})
TRIAL_ADMISSION_REASONS = frozenset({
    "no_actual_intervention",
    SHADOW_TRIAL_INTEGRITY_REASON,
})
_AUTHORITY_RETIRED_PATH_IDS = frozenset({"operator_learning_bundle"})


@dataclass(frozen=True)
class AuthorityDecision:
    authorized: bool
    reason: str
    stage: str | None
    allocation_pct: float
    activation_verdict_id: str | None
    current_verdict_id: str | None


def resolve_learning_authority(
    path_id: str,
    experiment: LearningExperiment,
    current_policy_epoch: str,
    *,
    persist_config_migrations: bool = True,
) -> AuthorityDecision:
    """Return the only authority decision used by Learning consumers.

    Any missing/unknown value stays on the declared baseline.  The resolver is
    intentionally read-only so a delayed controller cannot leave treatment on
    after a negative or invalid measurement verdict.
    """
    def blocked(reason: str, current: dict | None = None) -> AuthorityDecision:
        return AuthorityDecision(
            False, reason, experiment.stage, float(experiment.allocation_pct or 0.0),
            experiment.activation_verdict_id,
            str((current or {}).get("verdict_id") or "") or None,
        )

    try:
        global_reason = global_learning_refreeze_reason()
        if global_reason is not None:
            return blocked(global_reason.split(":", 1)[0])
        cfg = (
            load_config()
            if persist_config_migrations
            else load_config(persist_migrations=False)
        )
        learning = cfg.learning
    except Exception:
        return blocked("learning_config_unavailable")
    if not bool(cfg.agent.autonomous_learning):
        return blocked("autonomous_learning_disabled")
    if bool(cfg.runtime.paused):
        return blocked("runtime_paused")
    if not bool(learning.paper_influence_enabled):
        return blocked("paper_influence_disabled")
    if path_id not in set(learning.paper_influence_paths or []):
        return blocked("path_not_allowlisted")
    # A newly registered Shadow experiment records its first exposure before
    # ``insert_exposure`` atomically activates the experiment.  That bootstrap
    # row is still blocked because Shadow has no Paper authority; classify it
    # by the stable authority boundary instead of poisoning the cohort with a
    # transient ``experiment_not_active`` provenance.  Closed/refrozen rows
    # continue to fail as inactive below.
    if experiment.status == "registered" and experiment.stage == "shadow":
        return blocked("experiment_stage_not_paper")
    if experiment.status != "active":
        return blocked("experiment_not_active")
    if experiment.stage not in PAPER_STAGES:
        return blocked("experiment_stage_not_paper")
    allocation = float(experiment.allocation_pct or 0.0)
    if not 0.0 < allocation <= 0.90:
        return blocked("allocation_without_control_holdout")
    if experiment.stage == "paper_active" and allocation > float(learning.paper_active_allocation_pct):
        return blocked("paper_active_allocation_exceeds_config")
    hypothesis = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis is None:
        return blocked("hypothesis_not_found")
    if str(hypothesis.get("path_id") or "") != path_id:
        return blocked("hypothesis_path_mismatch")
    if path_id in _AUTHORITY_RETIRED_PATH_IDS:
        return blocked("measurement_unsupported")
    if path_id not in SUPPORTED_PATH_IDS:
        return _legacy_authority_adapter(experiment, current_policy_epoch, blocked)
    expected_horizon = str(hypothesis.get("target_horizon") or "")
    if not expected_horizon:
        return blocked("hypothesis_horizon_missing")
    try:
        expected_behavior_contract_hash = current_behavior_contract_hash(
            path_id=path_id,
            policy_epoch=current_policy_epoch,
            target_horizon=expected_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        )
    except (KeyError, ValueError):
        return blocked("behavior_contract_unavailable")
    if not experiment.activation_verdict_id:
        measurement = get_latest_measurement_verdict(
            experiment.experiment_id,
            policy_epoch=current_policy_epoch,
            stage=experiment.stage,
            hypothesis_id=experiment.hypothesis_id,
            target_horizon=expected_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
            behavior_contract_hash=expected_behavior_contract_hash,
            inference_role="monitoring",
        )
        if (
            measurement is not None
            and measurement_identity_reason(
                measurement,
                experiment.stage,
                current_policy_epoch,
                expected_horizon=expected_horizon,
                expected_behavior_contract_hash=expected_behavior_contract_hash,
                expected_hypothesis_id=experiment.hypothesis_id,
            ) is None
            and str(measurement.get("verdict") or "") in {"negative", "invalid"}
        ):
            return blocked(
                f"current_measurement_{measurement['verdict']}", measurement,
            )
        harm_reason = (
            None if measurement is None
            else measurement_harm_block_reason(measurement)
        )
        if harm_reason is not None:
            return blocked(harm_reason, measurement)
        return blocked("missing_activation_verdict", measurement)
    current = get_latest_measurement_verdict(
        experiment.experiment_id,
        policy_epoch=current_policy_epoch,
        stage=experiment.stage,
        hypothesis_id=experiment.hypothesis_id,
        target_horizon=expected_horizon,
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        behavior_contract_hash=expected_behavior_contract_hash,
        inference_role="monitoring",
    )
    if current is None:
        current = get_latest_measurement_verdict(
            experiment.experiment_id,
            inference_role="monitoring",
        )
    current_stage_underpowered = False
    if current is not None:
        identity_reason = measurement_identity_reason(
            current,
            experiment.stage,
            current_policy_epoch,
            expected_horizon=expected_horizon,
            expected_behavior_contract_hash=expected_behavior_contract_hash,
            expected_hypothesis_id=experiment.hypothesis_id,
        )
        if identity_reason:
            return blocked(identity_reason, current)
        harm_reason = measurement_harm_block_reason(current)
        if harm_reason:
            if (
                harm_reason == "harm_gate_indeterminate"
                and _underpowered_current_stage_authority_allowed(current)
            ):
                current_stage_underpowered = True
            else:
                return blocked(harm_reason, current)
    # A harmful current-stage result must fail closed even when an older
    # activation record is malformed or a controller has not run yet.
    if current is not None and str(current.get("verdict") or "") in {"negative", "invalid"}:
        return blocked(f"current_measurement_{current['verdict']}", current)
    if current is not None and str(current.get("verdict") or "") == "positive":
        intervention_reason = measurement_intervention_block_reason(
            current,
            required_per_arm=experiment.min_samples_per_arm,
        )
        if intervention_reason:
            return blocked(intervention_reason, current)
    if current is not None and str(current.get("hypothesis_id") or "") != experiment.hypothesis_id:
        return blocked("measurement_hypothesis_mismatch", current)
    collection_reason = inference_collection_reason(
        experiment,
        policy_epoch=current_policy_epoch,
        behavior_contract_hash=expected_behavior_contract_hash,
        target_horizon=expected_horizon,
    )
    if collection_reason is not None:
        return blocked(collection_reason, current)
    activation = get_record("learning_contribution_verdicts", experiment.activation_verdict_id)
    if activation is None:
        return blocked("invalid_activation_verdict")
    trial_admission = None
    if experiment.stage == "paper_10":
        trial_admission = get_trial_admission_event(experiment.experiment_id)
    activation_is_positive = activation.get("verdict") == "positive"
    activation_admission_reason = trial_admission_measurement_block_reason(
        activation,
        required_per_arm=experiment.min_samples_per_arm,
        required_market_days=experiment.min_market_days,
    )
    activation_is_admitted_trial = (
        activation.get("verdict") == "insufficient_data"
        and activation.get("reason") in TRIAL_ADMISSION_REASONS
        and activation_admission_reason is None
        and trial_admission is not None
        and trial_admission.get("verdict_id") == experiment.activation_verdict_id
        and trial_admission.get("from_stage") == "shadow"
        and trial_admission.get("to_stage") == "paper_10"
        and trial_admission.get("policy_epoch") == current_policy_epoch
    )
    if not activation_is_positive and not activation_is_admitted_trial:
        return blocked("invalid_activation_verdict")
    predecessor_stage = PREVIOUS_STAGE.get(experiment.stage)
    if not experiment.parent_experiment_id or not predecessor_stage:
        return blocked("missing_predecessor_experiment")
    predecessor = get_record("learning_experiments", experiment.parent_experiment_id)
    if predecessor is None or str(predecessor.get("hypothesis_id") or "") != experiment.hypothesis_id:
        return blocked("predecessor_hypothesis_mismatch")
    if str(predecessor.get("stage") or "") != predecessor_stage:
        return blocked("predecessor_stage_mismatch")
    if predecessor.get("ended_at") is None or predecessor.get("status") != "closed":
        return blocked("predecessor_not_closed")
    if str(activation.get("experiment_id") or "") != experiment.parent_experiment_id:
        return blocked("activation_experiment_mismatch")
    if str(activation.get("hypothesis_id") or "") != experiment.hypothesis_id:
        return blocked("activation_hypothesis_mismatch")
    if activation_is_positive:
        inference_reason = inference_authority_reason(activation)
        if inference_reason:
            return blocked(inference_reason, activation)
        intervention_reason = measurement_intervention_block_reason(
            activation,
            required_per_arm=experiment.min_samples_per_arm,
        )
        if intervention_reason:
            return blocked(intervention_reason, activation)

    if current is None:
        # A fresh successor may use its validated predecessor result only while
        # the effective policy is unchanged.
        activation_reason = measurement_identity_reason(
            activation, predecessor_stage, current_policy_epoch,
            expected_horizon=expected_horizon,
            expected_behavior_contract_hash=expected_behavior_contract_hash,
            expected_hypothesis_id=experiment.hypothesis_id,
        ) or (
            None
            if activation_is_admitted_trial
            else measurement_harm_block_reason(activation)
        )
        if activation_reason:
            return blocked(activation_reason)
        return AuthorityDecision(
            True,
            "admitted_shadow_trial" if activation_is_admitted_trial else "activated_by_predecessor",
            experiment.stage, allocation,
            experiment.activation_verdict_id, None,
        )
    identity_reason = measurement_identity_reason(
        current, experiment.stage, current_policy_epoch, expected_horizon=expected_horizon,
        expected_behavior_contract_hash=expected_behavior_contract_hash,
        expected_hypothesis_id=experiment.hypothesis_id,
    )
    if identity_reason:
        return blocked(identity_reason, current)
    return AuthorityDecision(
        True,
        (
            "current_stage_measurement_underpowered"
            if current_stage_underpowered
            else "current_stage_measurement_healthy"
        ),
        experiment.stage, allocation,
        experiment.activation_verdict_id, str(current.get("verdict_id") or "") or None,
    )


def _legacy_authority_adapter(experiment: LearningExperiment, policy_epoch: str, blocked) -> AuthorityDecision:
    """Read pre-AT-02 ``promote`` records without ever writing them anew."""
    verdict = get_latest_verdict_for_cohort(
        experiment.experiment_id,
        policy_epoch=policy_epoch,
        stage=experiment.stage,
    )
    if not verdict or verdict.get("verdict") != "promote":
        return blocked("missing_activation_verdict")
    if min(int(verdict.get("unique_treatment_n") or 0), int(verdict.get("unique_control_n") or 0)) < experiment.min_samples_per_arm:
        return blocked("legacy_samples_insufficient", verdict)
    linked = linked_settled_exposure_counts(experiment.experiment_id, policy_epoch=policy_epoch)
    if min(linked["treatment"], linked["control"]) < experiment.min_samples_per_arm:
        return blocked("legacy_outcomes_insufficient", verdict)
    try:
        start = datetime.fromisoformat(str(verdict["window_start"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(verdict["window_end"]).replace("Z", "+00:00"))
        harm = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (KeyError, TypeError, ValueError):
        return blocked("legacy_verdict_invalid", verdict)
    if (end - start).days < experiment.min_market_days or bool(harm.get("breached")):
        return blocked("legacy_verdict_invalid", verdict)
    return AuthorityDecision(
        True, "legacy_promote_compatibility", experiment.stage,
        float(experiment.allocation_pct or 0.0), None,
        str(verdict.get("verdict_id") or "") or None,
    )


def measurement_harm_block_reason(verdict: dict[str, Any]) -> str | None:
    """Return a reason when a verdict's harm gate forbids *granting* authority.

    Deliberately separate from ``measurement_identity_reason``.  A harm breach is
    not a property of measurement identity: it says the measured effect was bad,
    not that the measurement describes something else.  Conflating the two put
    this check upstream of the safety branch in ``process_lifecycle``, where it
    suppressed the very invalidation it is evidence for -- a breaching experiment
    was exempted from the reaping a clean one received.

    Every caller that grants or extends authority must consult this.  The safety
    path (refreeze/invalidate) must not.
    """
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return "measurement_metadata_invalid"
    if not isinstance(metrics, dict):
        return "measurement_metadata_invalid"
    if bool(metrics.get("breached")) or metrics.get("harm_state") == "breached":
        return "harm_gate_breached"
    if metrics.get("harm_state") == "indeterminate":
        return "harm_gate_indeterminate"
    if metrics.get("harm_state") != "clear":
        return "measurement_metadata_invalid"
    return None


def measurement_intervention_block_reason(
    verdict: dict[str, Any],
    *,
    required_per_arm: int,
) -> str | None:
    """Fail closed when a positive verdict lacks enough actual interventions.

    ``unique_treatment_n`` remains the randomized assigned-treatment count for
    the ITT estimand. Effective-intervention counts are a separate qualification
    gate; authorized no-op and refused treatment units remain in ITT but cannot
    satisfy that gate.
    """
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
        required = max(1, int(required_per_arm))
    except (TypeError, ValueError):
        return "treatment_intervention_evidence_unavailable"
    if not isinstance(metrics, dict):
        return "treatment_intervention_evidence_unavailable"
    assigned = _non_negative_int(metrics.get("assigned_treatment_n"))
    authority_applied = _non_negative_int(
        metrics.get("authority_applied_treatment_n")
    )
    actual = _non_negative_int(metrics.get("actual_intervention_treatment_n"))
    baseline = _non_negative_int(metrics.get("baseline_treatment_n"))
    persisted = _non_negative_int(verdict.get("unique_treatment_n"))
    rejection_reasons = metrics.get("treatment_authority_rejection_reasons")
    non_intervention_reasons = metrics.get("treatment_non_intervention_reasons")
    if (
        assigned is None
        or authority_applied is None
        or actual is None
        or baseline is None
        or persisted is None
        or not isinstance(rejection_reasons, dict)
        or not isinstance(non_intervention_reasons, dict)
        or any(_non_negative_int(value) is None for value in rejection_reasons.values())
        or any(
            _non_negative_int(value) is None
            for value in non_intervention_reasons.values()
        )
    ):
        return "treatment_intervention_evidence_unavailable"
    if (
        persisted != assigned
        or actual + baseline != assigned
        or not actual <= authority_applied <= assigned
        or sum(rejection_reasons.values()) != assigned - authority_applied
        or sum(non_intervention_reasons.values()) != baseline
    ):
        return "treatment_intervention_count_mismatch"
    if actual < required:
        return "insufficient_actual_intervention_samples"
    return None


def _underpowered_current_stage_authority_allowed(verdict: dict[str, Any]) -> bool:
    """Let a valid activated paper trial gather its predeclared evidence.

    This is not a promotion waiver. It applies only to an insufficient current
    verdict whose harm uncertainty is exactly sample underpower; activation,
    identity, config, allocation, predecessor and global refreeze gates are still
    evaluated normally by ``resolve_learning_authority``.
    """
    if str(verdict.get("verdict") or "") != "insufficient_data":
        return False
    if str(verdict.get("reason") or "") not in {
        "insufficient unique prospective treatment/control samples",
        "insufficient unique prospective treatment/control samples in continuous window",
        "market_day_coverage_gap",
        "no_actual_intervention",
        "insufficient_actual_intervention_samples",
    }:
        return False
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return False
    return (
        isinstance(metrics, dict)
        and metrics.get("harm_state") == "indeterminate"
        and metrics.get("harm_indeterminate_reason") == "insufficient_samples"
        and not bool(metrics.get("breached"))
    )


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def measurement_identity_reason(
    verdict: dict[str, Any],
    expected_stage: str | int,
    current_policy_epoch: str,
    *,
    expected_horizon: str | None,
    expected_behavior_contract_hash: str | None = None,
    expected_hypothesis_id: str | None = None,
) -> str | None:
    """Return a reason when a verdict does not describe *this* experiment now.

    Identity only: stage, hypothesis, policy epoch, horizon, outcome contract,
    metric version and behavior contract.  The harm gate lives in
    ``measurement_harm_block_reason`` so that it cannot block a safety action.
    """
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return "measurement_metadata_invalid"
    if not isinstance(metrics, dict):
        return "measurement_metadata_invalid"
    if str(verdict.get("stage") or "") != str(expected_stage):
        return "stage_mismatch"
    if (
        expected_hypothesis_id is not None
        and str(verdict.get("hypothesis_id") or "") != expected_hypothesis_id
    ):
        return "measurement_hypothesis_mismatch"
    policy_epoch = verdict.get("policy_epoch") or metrics.get("policy_epoch")
    target_horizon = verdict.get("target_horizon") or metrics.get("horizon")
    outcome_contract_version = (
        verdict.get("outcome_contract_version") or metrics.get("outcome_contract_version")
    )
    metric_version = verdict.get("metric_version") or metrics.get("metric_version")
    behavior_contract_hash = verdict.get("behavior_contract_hash")
    if not all((
        policy_epoch,
        target_horizon,
        outcome_contract_version,
        metric_version,
        behavior_contract_hash,
    )):
        return "measurement_metadata_invalid"
    if expected_behavior_contract_hash is None:
        return "behavior_contract_unavailable"
    if str(policy_epoch) != str(current_policy_epoch or ""):
        return "stale_policy_epoch"
    if expected_horizon is not None and str(target_horizon) != expected_horizon:
        return "horizon_mismatch"
    if str(outcome_contract_version) != "v1":
        return "outcome_contract_mismatch"
    if str(metric_version) != METRIC_VERSION:
        return "metric_version_mismatch"
    if str(behavior_contract_hash) != expected_behavior_contract_hash:
        return "behavior_contract_mismatch"
    return None


def trial_admission_measurement_block_reason(
    verdict: dict[str, Any],
    *,
    required_per_arm: int,
    required_market_days: int,
) -> str | None:
    """Validate the distinct evidence contract for first bounded Paper authority."""

    reason = str(verdict.get("reason") or "")
    if str(verdict.get("verdict") or "") != "insufficient_data":
        return "verdict_not_insufficient_data"
    if reason == "no_actual_intervention":
        # Mature v6-compatible Shadow evidence remains admissible only when its
        # original full harm gate is clear.
        return measurement_harm_block_reason(verdict)
    if reason != SHADOW_TRIAL_INTEGRITY_REASON:
        return "verdict_not_trial_integrity_ready"
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return "trial_integrity_metadata_invalid"
    if not isinstance(metrics, dict):
        return "trial_integrity_metadata_invalid"
    treatment_n = _non_negative_int(verdict.get("unique_treatment_n"))
    control_n = _non_negative_int(verdict.get("unique_control_n"))
    assigned_n = _non_negative_int(metrics.get("assigned_treatment_n"))
    authority_n = _non_negative_int(metrics.get("authority_applied_treatment_n"))
    actual_n = _non_negative_int(metrics.get("actual_intervention_treatment_n"))
    baseline_n = _non_negative_int(metrics.get("baseline_treatment_n"))
    if None in {treatment_n, control_n, assigned_n, authority_n, actual_n, baseline_n}:
        return "trial_integrity_metadata_invalid"
    samples_required = min(
        SHADOW_TRIAL_MIN_SAMPLES_PER_ARM,
        max(1, int(required_per_arm)),
    )
    if min(treatment_n, control_n) < samples_required:
        return "trial_integrity_samples_insufficient"
    if assigned_n != treatment_n or baseline_n != assigned_n:
        return "trial_integrity_count_mismatch"
    if authority_n != 0 or actual_n != 0:
        return "trial_integrity_intervention_present"
    coverage = metrics.get("market_day_coverage")
    if not isinstance(coverage, dict):
        return "trial_integrity_metadata_invalid"
    covered_days = _non_negative_int(coverage.get("current_consecutive_market_days"))
    if covered_days is None:
        return "trial_integrity_metadata_invalid"
    days_required = min(
        SHADOW_TRIAL_MIN_MARKET_DAYS,
        max(0, int(required_market_days)),
    )
    if covered_days < days_required:
        return "trial_integrity_market_days_insufficient"
    readiness = metrics.get("trial_integrity_readiness")
    if (
        not isinstance(readiness, dict)
        or readiness.get("contract_version") != SHADOW_TRIAL_INTEGRITY_CONTRACT
        or readiness.get("status") != "ready"
        or _non_negative_int(readiness.get("required_per_arm")) != samples_required
        or _non_negative_int(readiness.get("required_market_days")) != days_required
        or _non_negative_int(readiness.get("qualified_treatment_n")) != treatment_n
        or _non_negative_int(readiness.get("qualified_control_n")) != control_n
        or _non_negative_int(readiness.get("current_consecutive_market_days"))
        != covered_days
    ):
        return "trial_integrity_contract_invalid"
    if metrics.get("invalid_reasons"):
        return "trial_integrity_evidence_invalid"
    rejections = metrics.get("treatment_authority_rejection_reasons")
    if (
        not isinstance(rejections, dict)
        or set(rejections) != {"experiment_stage_not_paper"}
        or _non_negative_int(rejections.get("experiment_stage_not_paper")) != assigned_n
    ):
        return "trial_integrity_authority_provenance_invalid"
    if bool(metrics.get("breached")) or metrics.get("harm_state") == "breached":
        return "harm_gate_breached"
    harm_is_underpowered = (
        metrics.get("harm_state") == "indeterminate"
        and metrics.get("harm_indeterminate_reason") == "insufficient_samples"
    )
    if metrics.get("harm_state") != "clear" and not harm_is_underpowered:
        return "trial_integrity_harm_state_invalid"
    return None
