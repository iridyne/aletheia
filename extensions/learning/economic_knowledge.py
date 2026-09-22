"""Read-only Economic Knowledge projected from canonical Learning verdict truth."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field



def _lab_authority() -> bool:
    try:
        from extensions.observation_lab.canonical import get_authority_source

        return get_authority_source().source == "lab"
    except Exception:
        return False


class EconomicKnowledgeFactV1(BaseModel):
    """One reproducible qualification; never a second lifecycle authority."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["economic-knowledge/v1"] = "economic-knowledge/v1"
    source_cohort: str
    knowledge_id: str
    status: Literal["qualified"] = "qualified"
    operational_actionability: Literal["separate_authority_resolution"] = (
        "separate_authority_resolution"
    )
    hypothesis_id: str
    experiment_id: str
    verdict_id: str
    inference_plan_hash: str
    inference_cutoff_fingerprint: str
    path_id: str
    consumer: str
    behavior_fingerprint: str
    behavior_contract_version: str
    policy_epoch: str
    stage: str
    scope: dict
    treatment_action: dict
    target_horizon: str
    outcome_contract_version: str
    metric_version: str
    cost_model_version: str
    unique_treatment_n: int = Field(gt=0)
    actual_intervention_treatment_n: int = Field(gt=0)
    unique_control_n: int = Field(gt=0)
    baseline: float
    observed: float
    delta: float
    confidence_low: float
    confidence_high: float
    window_start: str
    window_end: str
    source_artifact_ids: list[str]


class EconomicKnowledgeAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    status: Literal["qualified", "not_qualified", "unavailable"]
    reasons: list[str] = Field(default_factory=list)
    fact: EconomicKnowledgeFactV1 | None = None


def assess_economic_knowledge(verdict_id: str) -> EconomicKnowledgeAssessmentV1:
    """Do not upgrade retired verdicts into current Lab qualifications."""
    return EconomicKnowledgeAssessmentV1(
        verdict_id=verdict_id,
        status="unavailable",
        reasons=["canonical_lab_result_projection_unavailable" if _lab_authority() else "authority_source_unavailable"],
    )


def project_current_economic_knowledge() -> list[EconomicKnowledgeFactV1]:
    """No Lab qualification projection is available; legacy verdicts are not current."""
    return []


def project_economic_knowledge_as_of(as_of: str) -> list[EconomicKnowledgeFactV1]:
    """Historical verdicts remain archival evidence, not current qualifications."""
    from aletheia.storage.sqlite import iso_instant_us

    if iso_instant_us(as_of) is None:
        raise ValueError("Economic Knowledge cutoff must be a valid instant")
    return []


def current_disqualification_reasons(
    verdict_id: str,
    *,
    as_of: str | None = None,
) -> list[str]:
    """Report the missing Lab qualification instead of consulting retired authority."""
    return ["canonical_lab_result_projection_unavailable" if _lab_authority() else "authority_source_unavailable"]


class CognitiveKnowledgeContextV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Literal["economic_knowledge_evidence"] = "economic_knowledge_evidence"
    path_id: str
    consumer: str
    requested_scope: dict
    facts: list[EconomicKnowledgeFactV1]
    behavior_authority: Literal[False] = False
    skill_activation: Literal[False] = False


def retrieve_cognitive_economic_knowledge(
    *, path_id: str, consumer: str, scope: dict,
) -> CognitiveKnowledgeContextV1:
    """Retrieve labeled same-Consumer evidence; never broadcast or activate it."""
    facts = [
        fact
        for fact in project_current_economic_knowledge()
        if fact.path_id == path_id
        and fact.consumer == consumer
        and all(fact.scope.get(key) == value for key, value in scope.items())
    ]
    return CognitiveKnowledgeContextV1(
        path_id=path_id,
        consumer=consumer,
        requested_scope=scope,
        facts=facts,
    )


def inherit_behavioral_economic_knowledge(
    *,
    verdict_id: str,
    candidate_key: str | None,
    baseline: float,
    treatment: float,
    consumer_stage: str,
):
    """Delegate qualified behavior exclusively to the existing path authority seam."""
    from extensions.learning.experiments import InfluenceDecision, apply_numeric_treatment

    assessment = assess_economic_knowledge(verdict_id)
    if assessment.fact is None:
        return InfluenceDecision(
            float(baseline), False, "disabled", "economic_knowledge_not_qualified",
        )
    disqualified = current_disqualification_reasons(verdict_id)
    if disqualified:
        return InfluenceDecision(
            float(baseline), False, "disabled", disqualified[0],
            hypothesis_id=assessment.fact.hypothesis_id,
            experiment_id=assessment.fact.experiment_id,
        )
    fact = assessment.fact
    return apply_numeric_treatment(
        path_id=fact.path_id,
        scope=fact.scope,
        action=fact.treatment_action,
        source_cohort=fact.source_cohort,
        target_horizon=fact.target_horizon,
        candidate_key=candidate_key,
        baseline=baseline,
        treatment=treatment,
        consumer=fact.consumer,
        consumer_stage=consumer_stage,
        expected_hypothesis_fingerprint=fact.behavior_fingerprint,
        expected_experiment_id=fact.experiment_id,
    )
