"""Pure policy-delta compiler, feasibility preflight, and Shadow handoff."""

from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from aletheia.learning.hypotheses import (
    HypothesisDraftV1,
    HypothesisResearchEvidenceV1,
    hypothesis_fingerprint,
)
from aletheia.learning.models import LearningExperiment
from aletheia.learning.registry import get_learning_path
from aletheia.learning.t0_context import T0FoundationAssessmentV1


@dataclass(frozen=True)
class ActionTemplateV1:
    action_type: str
    parameter: str
    minimum: float
    maximum: float

ACTION_TEMPLATES: dict[str, ActionTemplateV1] = {
    "universe_family_calibration": ActionTemplateV1(
        "opportunity_multiplier", "multiplier", 0.9, 1.1,
    ),
    "ranker_case_score": ActionTemplateV1(
        "score_adjustment", "delta", -10.0, 10.0,
    ),
    "ranker_learned_guard": ActionTemplateV1(
        "candidate_guard", "score_penalty", -10.0, 0.0,
    ),
}


class ExperimentabilityPreflightV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_id: str
    hypothesis_id: str
    historical_eligible_n: int = Field(ge=0)
    distinct_market_days: int = Field(ge=0)
    eligible_event_rate_per_day: float | None = Field(default=None, ge=0)
    outcome_adapter_available: bool
    outcome_settlement_rate: float | None = Field(default=None, ge=0, le=1)
    expected_treatment_assignment_rate: float | None = Field(default=None, ge=0, le=1)
    expected_actual_intervention_rate: float | None = Field(default=None, ge=0, le=1)
    required_samples_per_arm: int = Field(gt=0)
    required_market_days: int = Field(gt=0)
    estimated_days_to_minimum_sample: float | None = Field(default=None, ge=0)
    active_overlap_conflict: bool
    data_quality_state: str
    status: str
    reasons: list[str] = Field(default_factory=list)


def compile_hypothesis_draft(
    *,
    path_id: str,
    research_evidence: HypothesisResearchEvidenceV1,
    t0_foundation: T0FoundationAssessmentV1,
    scope: dict,
    baseline_action: dict,
    treatment_action: dict,
    source_type: str,
    source_artifact_ids: list[str],
    source_cohort: str,
    target_horizon: str,
    effect_threshold: float,
    harm_budget: dict,
) -> HypothesisDraftV1:
    """Compile only one declared, finite action parameter for one registry Consumer."""
    path = get_learning_path(path_id)
    reasons: list[str] = []
    template = ACTION_TEMPLATES.get(path_id)
    if template is None:
        reasons.append("action_template_unsupported")
    if research_evidence.research_status not in {"ready", "narrowed"}:
        reasons.append(f"research_{research_evidence.research_status}")
    if t0_foundation.status != "eligible_for_hypothesis_research":
        reasons.append("t0_foundation_not_eligible")
    if not isfinite(effect_threshold) or effect_threshold < 0:
        reasons.append("effect_threshold_invalid")
    if not harm_budget:
        reasons.append("harm_budget_missing")

    changed = {
        key for key in set(baseline_action) | set(treatment_action)
        if baseline_action.get(key) != treatment_action.get(key)
        and key != "type"
    }
    if template is not None:
        if baseline_action.get("type") != template.action_type:
            reasons.append("baseline_action_type_invalid")
        if treatment_action.get("type") != template.action_type:
            reasons.append("treatment_action_type_invalid")
        if changed != {template.parameter}:
            reasons.append("treatment_must_change_exactly_one_parameter")
        value = treatment_action.get(template.parameter)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or not template.minimum <= float(value) <= template.maximum
        ):
            reasons.append("treatment_parameter_out_of_bounds")

    extra_scope = set(scope) - {"symbol", "direction", "scanner_family", "market_type"}
    if len(extra_scope) > 3:
        reasons.append("research_too_complex")
    status = "advisory_only" if reasons else "compilable"
    fingerprint = hypothesis_fingerprint(
        path_id=path_id,
        scope=scope,
        action=treatment_action,
        target_horizon=target_horizon,
        source_cohort=source_cohort,
        behavior_contract_version=path.behavior_contract_version,
    )
    return HypothesisDraftV1(
        path_id=path_id,
        consumer=path.consumer,
        source_type=source_type,
        source_artifact_ids=source_artifact_ids,
        source_cohort=source_cohort,
        scope=scope,
        baseline_action=baseline_action,
        treatment_action=treatment_action,
        primary_metric=path.metric_version,
        outcome_contract_version=path.outcome_contract_version,
        target_horizon=target_horizon,
        effect_threshold=effect_threshold,
        harm_budget=harm_budget,
        falsifier=research_evidence.falsification_condition,
        overlap_group=path.overlap_group,
        behavior_fingerprint=fingerprint,
        status=status,
        reasons=list(dict.fromkeys(reasons)),
    )


def assess_experimentability(
    *,
    path_id: str,
    hypothesis_id: str,
    historical_eligible_n: int,
    distinct_market_days: int,
    eligible_event_rate_per_day: float | None,
    outcome_adapter_available: bool,
    outcome_settlement_rate: float | None,
    expected_treatment_assignment_rate: float | None,
    expected_actual_intervention_rate: float | None,
    required_samples_per_arm: int,
    required_market_days: int,
    active_overlap_conflict: bool = False,
    data_quality_state: str = "complete",
) -> ExperimentabilityPreflightV1:
    path = get_learning_path(path_id)
    reasons: list[str] = []
    if not path.measurement_supported:
        reasons.append("measurement_unsupported")
    if not outcome_adapter_available:
        reasons.append("outcome_adapter_unavailable")
    if active_overlap_conflict:
        reasons.append("active_overlap_conflict")
    if data_quality_state != "complete":
        reasons.append(f"data_quality_{data_quality_state}")

    estimated_days: float | None = None
    if (
        eligible_event_rate_per_day is not None
        and eligible_event_rate_per_day > 0
        and outcome_settlement_rate is not None
        and outcome_settlement_rate > 0
        and expected_treatment_assignment_rate is not None
        and 0 < expected_treatment_assignment_rate < 1
        and expected_actual_intervention_rate is not None
        and expected_actual_intervention_rate > 0
    ):
        daily_treatment = (
            eligible_event_rate_per_day
            * outcome_settlement_rate
            * expected_treatment_assignment_rate
            * expected_actual_intervention_rate
        )
        daily_control = (
            eligible_event_rate_per_day
            * outcome_settlement_rate
            * (1.0 - expected_treatment_assignment_rate)
        )
        daily_minimum_arm = min(daily_treatment, daily_control)
        if daily_minimum_arm > 0:
            estimated_days = required_samples_per_arm / daily_minimum_arm
    if historical_eligible_n == 0 or estimated_days is None:
        reasons.append("eligible_sample_rate_unavailable")

    if "measurement_unsupported" in reasons or "outcome_adapter_unavailable" in reasons:
        status = "unsupported"
    elif reasons:
        status = "not_experimentable_now"
    elif estimated_days is not None and estimated_days > max(required_market_days * 2, 30):
        status = "slow_but_viable"
    else:
        status = "ready"
    return ExperimentabilityPreflightV1(
        path_id=path_id,
        hypothesis_id=hypothesis_id,
        historical_eligible_n=historical_eligible_n,
        distinct_market_days=distinct_market_days,
        eligible_event_rate_per_day=eligible_event_rate_per_day,
        outcome_adapter_available=outcome_adapter_available,
        outcome_settlement_rate=outcome_settlement_rate,
        expected_treatment_assignment_rate=expected_treatment_assignment_rate,
        expected_actual_intervention_rate=expected_actual_intervention_rate,
        required_samples_per_arm=required_samples_per_arm,
        required_market_days=required_market_days,
        estimated_days_to_minimum_sample=estimated_days,
        active_overlap_conflict=active_overlap_conflict,
        data_quality_state=data_quality_state,
        status=status,
        reasons=list(dict.fromkeys(reasons)),
    )


def admit_nomination_to_shadow(
    nomination_id: str,
    preflight: ExperimentabilityPreflightV1,
) -> LearningExperiment | None:
    """Open one existing-ledger Shadow experiment only after viable preflight."""
    from aletheia.learning.ledger import (
        get_nomination,
        get_open_experiment,
        insert_experiment,
    )
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    if preflight.status not in {"ready", "slow_but_viable"}:
        return None
    nomination = get_nomination(nomination_id)
    if nomination is None or nomination.get("publication_status") != "published":
        return None
    hypothesis_id = str(nomination.get("hypothesis_id") or "")
    if not hypothesis_id or hypothesis_id != preflight.hypothesis_id:
        return None

    path = get_learning_path(preflight.path_id)
    if not path.measurement_supported:
        return None
    with transaction(immediate=True):
        hypothesis = get_db().execute(
            "SELECT * FROM learning_hypotheses WHERE hypothesis_id=?",
            (hypothesis_id,),
        ).fetchone()
        if hypothesis is None or str(hypothesis["path_id"]) != preflight.path_id:
            return None
        existing = get_open_experiment(hypothesis_id)
        if existing is not None:
            return LearningExperiment.model_validate(existing)
        overlap = get_db().execute(
            "SELECT 1 FROM learning_experiments e "
            "JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id "
            "WHERE e.ended_at IS NULL AND h.overlap_group=? AND h.hypothesis_id!=? LIMIT 1",
            (str(hypothesis["overlap_group"]), hypothesis_id),
        ).fetchone()
        if overlap is not None:
            return None
        experiment = LearningExperiment(
            hypothesis_id=hypothesis_id,
            stage="shadow",
            assignment_salt=uuid4().hex,
            allocation_pct=0.5,
            min_samples_per_arm=preflight.required_samples_per_arm,
            min_market_days=preflight.required_market_days,
            primary_metric=path.metric_version,
            promotion_gate_json=json.dumps({
                "prospective_only": True,
                "net_of_costs": True,
                "preflight_status": preflight.status,
            }, sort_keys=True),
            harm_gate_json=str(hypothesis["harm_budget_json"]),
            status="registered",
        )
        insert_experiment(experiment)
        get_db().execute(
            "UPDATE learning_hypotheses SET status='shadow', updated_at=? "
            "WHERE hypothesis_id=? AND status='proposed'",
            (experiment.created_at, hypothesis_id),
        )
        persisted = get_open_experiment(hypothesis_id)
    return LearningExperiment.model_validate(persisted) if persisted is not None else None
