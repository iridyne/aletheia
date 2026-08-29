"""Read-only Economic Knowledge projected from canonical Learning verdict truth."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aletheia.universe.outcome_economics import COST_MODEL_VERSION


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
    """Qualify one consumed confirmatory verdict without writing projected truth."""
    from aletheia.storage.sqlite import get_db

    row = get_db().execute(
        "SELECT v.*, h.path_id, h.consumer, h.fingerprint, "
        "h.behavior_contract_version, h.cohort_id, h.scope_json, h.action_json, "
        "h.source_artifact_ids_json, h.effect_threshold, h.status AS hypothesis_status, "
        "e.stage AS experiment_stage, e.status AS experiment_status, "
        "e.primary_metric AS experiment_metric "
        "FROM learning_contribution_verdicts v "
        "JOIN learning_hypotheses h ON h.hypothesis_id=v.hypothesis_id "
        "JOIN learning_experiments e ON e.experiment_id=v.experiment_id "
        "WHERE v.verdict_id=?",
        (verdict_id,),
    ).fetchone()
    if row is None:
        return EconomicKnowledgeAssessmentV1(
            verdict_id=verdict_id,
            status="unavailable",
            reasons=["verdict_unavailable"],
        )
    data = dict(row)
    reasons: list[str] = []
    if data.get("verdict") != "positive":
        reasons.append("verdict_not_positive")
    if data.get("inference_role") != "confirmatory" or not data.get("authority_eligible"):
        reasons.append("not_authority_eligible_confirmatory_inference")
    consumed = get_db().execute(
        "SELECT plan_hash, cutoff_fingerprint FROM learning_inference_decisions "
        "WHERE experiment_id=? AND consumed_verdict_id=? AND status='consumed' LIMIT 1",
        (data["experiment_id"], verdict_id),
    ).fetchone()
    if consumed is None:
        reasons.append("confirmatory_verdict_not_consumed")
    elif (
        consumed["plan_hash"] != data.get("inference_plan_hash")
        or consumed["cutoff_fingerprint"] != data.get("inference_cutoff_fingerprint")
    ):
        reasons.append("fixed_look_identity_mismatch")

    try:
        metrics = json.loads(data.get("harm_metrics_json") or "{}")
        scope = json.loads(data.get("scope_json") or "{}")
        action = json.loads(data.get("action_json") or "{}")
        sources = json.loads(data.get("source_artifact_ids_json") or "[]")
    except (TypeError, ValueError):
        metrics, scope, action, sources = {}, {}, {}, []
        reasons.append("projection_metadata_invalid")
    if not all(isinstance(value, expected) for value, expected in (
        (metrics, dict), (scope, dict), (action, dict), (sources, list),
    )):
        reasons.append("projection_metadata_invalid")

    actual_n = metrics.get("actual_intervention_treatment_n") if isinstance(metrics, dict) else None
    if isinstance(actual_n, bool) or not isinstance(actual_n, int) or actual_n <= 0:
        reasons.append("no_actual_intervention")
    if isinstance(actual_n, int) and actual_n > int(data.get("unique_treatment_n") or 0):
        reasons.append("actual_intervention_count_invalid")
    if not str(data.get("experiment_stage") or "").startswith("paper_"):
        reasons.append("experiment_stage_not_paper")
    if data.get("stage") != data.get("experiment_stage"):
        reasons.append("stage_identity_mismatch")
    if (
        not data.get("policy_epoch")
        or not data.get("behavior_contract_hash")
        or not data.get("behavior_contract_version")
        or not data.get("target_horizon")
        or not data.get("outcome_contract_version")
    ):
        reasons.append("measurement_identity_incomplete")
    if data.get("metric_version") != data.get("experiment_metric"):
        reasons.append("metric_version_mismatch")
    if not data.get("inference_plan_hash") or not data.get("inference_cutoff_fingerprint"):
        reasons.append("fixed_look_identity_incomplete")
    if not isinstance(metrics, dict) or metrics.get("expected_cost_model_version") != COST_MODEL_VERSION:
        reasons.append("cost_model_version_mismatch")
    numeric = ("baseline", "observed", "delta", "confidence_low", "confidence_high")
    if any(data.get(key) is None for key in numeric):
        reasons.append("economic_estimate_incomplete")
    elif float(data["delta"]) <= float(data.get("effect_threshold") or 0.0):
        reasons.append("practical_effect_threshold_not_met")
    if int(data.get("unique_control_n") or 0) <= 0:
        reasons.append("control_arm_unavailable")

    if reasons:
        return EconomicKnowledgeAssessmentV1(
            verdict_id=verdict_id,
            status="not_qualified",
            reasons=list(dict.fromkeys(reasons)),
        )
    fact = EconomicKnowledgeFactV1(
        knowledge_id=f"economic-knowledge:{verdict_id}",
        source_cohort=str(data["cohort_id"]),
        hypothesis_id=str(data["hypothesis_id"]),
        experiment_id=str(data["experiment_id"]),
        verdict_id=verdict_id,
        inference_plan_hash=str(data["inference_plan_hash"]),
        inference_cutoff_fingerprint=str(data["inference_cutoff_fingerprint"]),
        path_id=str(data["path_id"]),
        consumer=str(data["consumer"]),
        behavior_fingerprint=str(data["fingerprint"]),
        behavior_contract_version=str(data["behavior_contract_version"]),
        policy_epoch=str(data["policy_epoch"]),
        stage=str(data["stage"]),
        scope=scope,
        treatment_action=action,
        target_horizon=str(data["target_horizon"]),
        outcome_contract_version=str(data["outcome_contract_version"]),
        metric_version=str(data["metric_version"]),
        cost_model_version=COST_MODEL_VERSION,
        unique_treatment_n=int(data["unique_treatment_n"]),
        actual_intervention_treatment_n=(
            actual_n if isinstance(actual_n, int) and not isinstance(actual_n, bool) else 0
        ),
        unique_control_n=int(data["unique_control_n"]),
        baseline=float(data["baseline"]),
        observed=float(data["observed"]),
        delta=float(data["delta"]),
        confidence_low=float(data["confidence_low"]),
        confidence_high=float(data["confidence_high"]),
        window_start=str(data["window_start"]),
        window_end=str(data["window_end"]),
        source_artifact_ids=[str(value) for value in sources],
    )
    return EconomicKnowledgeAssessmentV1(
        verdict_id=verdict_id,
        status="qualified",
        fact=fact,
    )


def project_current_economic_knowledge() -> list[EconomicKnowledgeFactV1]:
    """Return all currently reproducible qualifications, or an honest empty list."""
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        "SELECT verdict_id FROM learning_contribution_verdicts "
        "WHERE verdict='positive' AND inference_role='confirmatory' "
        "ORDER BY created_at, verdict_id"
    ).fetchall()
    facts: list[EconomicKnowledgeFactV1] = []
    for row in rows:
        verdict_id = str(row["verdict_id"])
        assessment = assess_economic_knowledge(verdict_id)
        if (
            assessment.fact is not None
            and not current_disqualification_reasons(verdict_id)
        ):
            facts.append(assessment.fact)
    return facts


def current_disqualification_reasons(verdict_id: str) -> list[str]:
    """Return later canonical death/refreeze facts without rewriting history."""
    from aletheia.storage.sqlite import get_db

    verdict = get_db().execute(
        "SELECT experiment_id, hypothesis_id, created_at "
        "FROM learning_contribution_verdicts WHERE verdict_id=?",
        (verdict_id,),
    ).fetchone()
    if verdict is None:
        return ["verdict_unavailable"]
    reasons: list[str] = []
    later = get_db().execute(
        "SELECT verdict, harm_metrics_json FROM learning_contribution_verdicts "
        "WHERE hypothesis_id=? AND created_at>? ORDER BY created_at, verdict_id",
        (verdict["hypothesis_id"], verdict["created_at"]),
    ).fetchall()
    for row in later:
        if row["verdict"] in {"negative", "invalid"}:
            reasons.append(f"later_{row['verdict']}_verdict")
        try:
            metrics = json.loads(row["harm_metrics_json"] or "{}")
        except (TypeError, ValueError):
            metrics = {}
        if isinstance(metrics, dict) and metrics.get("breached") is True:
            reasons.append("later_determinate_harm")
    lifecycle = get_db().execute(
        "SELECT action FROM learning_lifecycle_events "
        "WHERE hypothesis_id=? AND created_at>? ORDER BY created_at, event_id",
        (verdict["hypothesis_id"], verdict["created_at"]),
    ).fetchall()
    if any(str(row["action"]) in {"refreeze", "idle_expired", "terminate"} for row in lifecycle):
        reasons.append("later_lifecycle_refreeze")
    hypothesis = get_db().execute(
        "SELECT status FROM learning_hypotheses WHERE hypothesis_id=?",
        (verdict["hypothesis_id"],),
    ).fetchone()
    if hypothesis is None or str(hypothesis["status"]) in {
        "refrozen", "idle_expired", "rejected",
    }:
        reasons.append("hypothesis_not_currently_qualified")
    return list(dict.fromkeys(reasons))


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
    from aletheia.learning.experiments import InfluenceDecision, apply_numeric_treatment

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
