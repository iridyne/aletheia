"""Deterministic advisory research attention from Knowledge Yield."""

from __future__ import annotations

from math import sqrt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aletheia.learning.knowledge_yield import KnowledgeYieldReportV1

ResearchAction = Literal[
    "EXTEND", "REPLICATE", "DIFFERENT_TEST", "RETIRE",
    "HOLD_INSUFFICIENT_EVIDENCE",
]


class MetaEvolutionRecommendationV1(BaseModel):
    """#1093-compatible research advice with no scheduler or trading authority."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = "meta-evolution-recommendation/v1"
    research_action: ResearchAction
    path_id: str
    consumer: str
    behavior_contract_version: str
    metric_version: str
    outcome_contract_version: str
    target_horizon: str
    mutation_method: Literal["single_parameter"] = "single_parameter"
    admitted_real_intervention_n: int = Field(ge=0)
    qualified_knowledge_n: int = Field(ge=0)
    posterior_mean_yield: float = Field(ge=0, le=1)
    conservative_yield_bound: float = Field(ge=0, le=1)
    harm_refreeze_rate: float = Field(ge=0, le=1)
    rationale: list[str]
    advisory_only: Literal[True] = True
    may_launch_agent: Literal[False] = False
    may_open_experiment: Literal[False] = False
    may_mutate_trading: Literal[False] = False


def recommend_meta_evolution(
    report: KnowledgeYieldReportV1,
    *, minimum_real_interventions: int = 10,
    minimum_confirmatory_outcomes: int = 5,
) -> list[MetaEvolutionRecommendationV1]:
    """Map compatible yield slices to bounded research actions, never execution."""
    recommendations: list[MetaEvolutionRecommendationV1] = []
    for item in report.slices:
        n = item.real_intervention_hypothesis_n
        qualified = item.qualified_economic_knowledge_n
        posterior = (qualified + 1.0) / (n + 2.0)
        standard_error = sqrt(posterior * (1.0 - posterior) / (n + 3.0))
        conservative = max(0.0, posterior - 1.64 * standard_error)
        harm_rate = (
            item.operational_drag_ratio
            if item.operational_drag_ratio is not None
            else 0.0
        )
        reasons: list[str] = []
        if (
            n < minimum_real_interventions
            or item.confirmatory_outcome_n < minimum_confirmatory_outcomes
        ):
            action: ResearchAction = "HOLD_INSUFFICIENT_EVIDENCE"
            reasons.append("minimum_real_intervention_or_confirmatory_sample_not_met")
        elif harm_rate >= 0.2:
            action = "RETIRE"
            reasons.append("harm_or_refreeze_rate_exceeds_conservative_limit")
        elif qualified == 0:
            action = "DIFFERENT_TEST"
            reasons.append("adequately_tested_without_qualified_knowledge")
        elif qualified < 2 or conservative < 0.1:
            action = "REPLICATE"
            reasons.append("positive_signal_requires_independent_replication")
        else:
            action = "EXTEND"
            reasons.append("replicated_conservative_yield_supports_adjacent_research")
        if item.unavailable_reasons:
            reasons.extend(f"coverage:{reason}" for reason in item.unavailable_reasons)
        recommendations.append(MetaEvolutionRecommendationV1(
            research_action=action,
            path_id=item.path_id,
            consumer=item.consumers[0] if len(item.consumers) == 1 else "mixed_or_unavailable",
            behavior_contract_version=item.behavior_contract_version,
            metric_version=item.metric_version,
            outcome_contract_version=item.outcome_contract_version,
            target_horizon=item.target_horizon,
            admitted_real_intervention_n=n,
            qualified_knowledge_n=qualified,
            posterior_mean_yield=posterior,
            conservative_yield_bound=conservative,
            harm_refreeze_rate=harm_rate,
            rationale=reasons,
        ))
    return recommendations
