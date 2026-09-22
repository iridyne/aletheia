"""Version-compatible Knowledge Yield and Learning cost accounting."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field



class KnowledgeYieldSliceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_id: str
    behavior_contract_version: str
    metric_version: str
    outcome_contract_version: str
    target_horizon: str
    consumers: list[str] = Field(default_factory=list)
    generation_methods: list[str] = Field(default_factory=list)
    producers: list[str] = Field(default_factory=list)
    producer_versions: list[str] = Field(default_factory=list)
    source_bundle_hashes: list[str] = Field(default_factory=list)
    source_artifact_ids: list[str] = Field(default_factory=list)
    root_outcome_identities: list[str] = Field(default_factory=list)
    derivation_parent_hypothesis_ids: list[str] = Field(default_factory=list)
    generation_attempt_n: int = Field(default=0, ge=0)
    research_status_counts: dict[str, int] = Field(default_factory=dict)
    compilable_nomination_n: int = Field(default=0, ge=0)
    advisory_nomination_n: int = Field(default=0, ge=0)
    unique_hypothesis_n: int = Field(default=0, ge=0)
    shadow_admission_n: int = Field(default=0, ge=0)
    real_intervention_hypothesis_n: int = Field(default=0, ge=0)
    confirmatory_outcome_n: int = Field(default=0, ge=0)
    redundancy_replay_ratio: float | None = Field(default=None, ge=0, le=1)
    exclusions: list[str] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
    hypotheses_entering_prospective_test_n: int = Field(ge=0)
    qualified_economic_knowledge_n: int = Field(ge=0)
    knowledge_yield: float | None = Field(default=None, ge=0, le=1)
    invalid_verdict_n: int = Field(ge=0)
    refrozen_hypothesis_n: int = Field(ge=0)
    median_days_to_evidence: float | None = Field(default=None, ge=0)
    estimated_generation_cost_usd: float | None = Field(default=None, ge=0)
    operational_drag_ratio: float | None = Field(default=None, ge=0, le=1)
    availability: str
    unavailable_reasons: list[str] = Field(default_factory=list)


class KnowledgeYieldReportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: str = "knowledge-yield/v1"
    window_start: str
    window_end: str
    generation_attempt_n: int = Field(default=0, ge=0)
    published_nomination_n: int = Field(default=0, ge=0)
    unpublished_nomination_n: int = Field(default=0, ge=0)
    rejected_or_advisory_nomination_n: int = Field(default=0, ge=0)
    unique_nominated_hypothesis_n: int = Field(default=0, ge=0)
    generation_to_hypothesis_conversion: float | None = Field(
        default=None, ge=0, le=1,
    )
    total_recorded_generation_cost_usd: float | None = Field(default=None, ge=0)
    generation_cost_availability: str = "unavailable"
    current_economic_knowledge_n: int = Field(ge=0)
    slices: list[KnowledgeYieldSliceV1]
    cross_slice_aggregate: None = None
    # Report-level availability is distinct from an empty/zero measurement.
    # A Lab-authority process cannot safely derive the legacy economic view
    # from app.db, so it must expose that proof gap explicitly.
    availability: str = "available"
    unavailable_reasons: list[str] = Field(default_factory=list)


def build_knowledge_yield_report(
    *, window_start: str, window_end: str, path_id: str | None = None,
) -> KnowledgeYieldReportV1:
    """Measure conversion by compatible contract identity; never pool unlike metrics."""
    start = _instant(window_start)
    end = _instant(window_end)
    if start is None or end is None or end <= start:
        raise ValueError("Knowledge Yield requires a valid increasing UTC window")
    from extensions.observation_lab.canonical import get_authority_source

    try:
        reason = (
            "canonical_lab_result_projection_unavailable"
            if get_authority_source().source == "lab"
            else "canonical_lab_authority_unavailable"
        )
    except Exception:
        reason = "canonical_lab_authority_unavailable"
    return KnowledgeYieldReportV1(
        window_start=window_start,
        window_end=window_end,
        generation_cost_availability="unavailable",
        current_economic_knowledge_n=0,
        slices=[],
        availability="unavailable",
        unavailable_reasons=[reason],
    )


def _instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed
