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
from aletheia.learning.cohorts import SUPPORTED_PATH_ID
from aletheia.learning.control import global_learning_refreeze_reason
from aletheia.learning.ledger import (
    get_latest_measurement_verdict,
    get_trial_admission_event,
    get_latest_verdict_for_cohort,
    get_record,
    linked_settled_exposure_counts,
)
from aletheia.learning.models import LearningExperiment
from aletheia.learning.verdicts import METRIC_VERSION


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
        cfg = load_config()
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
    # A harmful current-stage result must fail closed even when an older
    # activation record is malformed or a controller has not run yet.
    current = get_latest_measurement_verdict(experiment.experiment_id)
    if current is not None and str(current.get("verdict") or "") in {"negative", "invalid"}:
        return blocked(f"current_measurement_{current['verdict']}", current)
    if path_id != SUPPORTED_PATH_ID:
        return _legacy_authority_adapter(experiment, current_policy_epoch, blocked)
    expected_horizon = str(hypothesis.get("target_horizon") or "")
    if not expected_horizon:
        return blocked("hypothesis_horizon_missing")
    if current is not None and str(current.get("hypothesis_id") or "") != experiment.hypothesis_id:
        return blocked("measurement_hypothesis_mismatch", current)
    if not experiment.activation_verdict_id:
        return _legacy_authority_adapter(experiment, current_policy_epoch, blocked)
    activation = get_record("learning_contribution_verdicts", experiment.activation_verdict_id)
    trial_admission = None
    if experiment.stage == "paper_10":
        trial_admission = get_trial_admission_event(experiment.experiment_id)
    activation_is_positive = activation is not None and activation.get("verdict") == "positive"
    activation_is_admitted_trial = (
        activation is not None
        and activation.get("verdict") == "insufficient_data"
        and activation.get("reason") == "no_actual_intervention"
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

    if current is None:
        # A fresh successor may use its validated predecessor result only while
        # the effective policy is unchanged.
        activation_reason = measurement_identity_reason(
            activation, predecessor_stage, current_policy_epoch,
            expected_horizon=expected_horizon,
        )
        return blocked(activation_reason) if activation_reason else AuthorityDecision(
            True,
            "admitted_shadow_trial" if activation_is_admitted_trial else "activated_by_predecessor",
            experiment.stage, allocation,
            experiment.activation_verdict_id, None,
        )
    verdict = str(current.get("verdict") or "")
    identity_reason = measurement_identity_reason(
        current, experiment.stage, current_policy_epoch, expected_horizon=expected_horizon,
    )
    if identity_reason:
        return blocked(identity_reason, current)
    return AuthorityDecision(
        True, "current_stage_measurement_healthy", experiment.stage, allocation,
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


def measurement_identity_reason(
    verdict: dict[str, Any],
    expected_stage: str | int,
    current_policy_epoch: str,
    *,
    expected_horizon: str | None,
) -> str | None:
    try:
        metrics = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return "measurement_metadata_invalid"
    if not isinstance(metrics, dict):
        return "measurement_metadata_invalid"
    if str(verdict.get("stage") or "") != str(expected_stage):
        return "stage_mismatch"
    policy_epoch = verdict.get("policy_epoch") or metrics.get("policy_epoch")
    if str(policy_epoch or "") != str(current_policy_epoch or ""):
        return "stale_policy_epoch"
    target_horizon = verdict.get("target_horizon") or metrics.get("horizon")
    if expected_horizon is not None and str(target_horizon or "") != expected_horizon:
        return "horizon_mismatch"
    outcome_contract_version = (
        verdict.get("outcome_contract_version") or metrics.get("outcome_contract_version")
    )
    if str(outcome_contract_version or "") != "v1":
        return "outcome_contract_mismatch"
    metric_version = verdict.get("metric_version") or metrics.get("metric_version")
    if str(metric_version or "") != METRIC_VERSION:
        return "metric_version_mismatch"
    if bool(metrics.get("breached")):
        return "harm_gate_breached"
    return None
