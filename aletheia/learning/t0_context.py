"""Typed, path-specific decision-time context for Learning research."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DomainState = Literal[
    "available", "not_applicable", "not_collected", "stale",
    "unavailable", "integrity_conflict", "pruned",
]
SourceClass = Literal[
    "observed_fact", "deterministic_derived", "model_belief",
    "human_or_policy_belief",
]
FoundationStatus = Literal[
    "eligible_for_reflection", "eligible_for_hypothesis_research",
    "advisory_only", "unavailable",
]


class T0ContextDomainV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(min_length=1, max_length=128)
    state: DomainState
    source_class: SourceClass
    values: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    source_hashes: list[str] = Field(default_factory=list, max_length=20)
    source_observed_at: str | None = None
    received_at: str | None = None
    calculation_version: str | None = None
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _state_is_honest(self) -> "T0ContextDomainV1":
        if self.state == "available" and (not self.values or not self.source_refs):
            raise ValueError("available T0 domains require values and source refs")
        if self.state not in {"available", "not_applicable"} and not self.unavailable_reason:
            raise ValueError("unavailable T0 domains require an explicit reason")
        return self


class T0ContextEnvelopeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["learning-t0-context/v1"] = "learning-t0-context/v1"
    path_id: str
    consumer: str
    consumer_stage: str
    t0_anchor_kind: str
    anchor_entity_id: str
    anchor_at: str
    capture_mode: Literal[
        "captured_at_t0", "reconstructed_exact", "reconstructed_partial",
    ]
    runtime_session_id: str | None = None
    producer_commit: str | None = None
    behavior_fingerprint: str | None = None
    policy_epoch: str | None = None
    required_domains: list[str]
    recommended_domains: list[str] = Field(default_factory=list)
    domains: list[T0ContextDomainV1]
    root_causal_identity: str
    population_identity: str | None = None
    observed_world_hash: str
    completeness_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _domain_identities_are_unique(self) -> "T0ContextEnvelopeV1":
        names = [domain.domain for domain in self.domains]
        if len(names) != len(set(names)):
            raise ValueError("T0 domain identities must be unique")
        if len(self.required_domains) != len(set(self.required_domains)):
            raise ValueError("required T0 domains must be unique")
        return self


class T0FoundationAssessmentV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temporal_integrity: bool
    provenance_integrity: bool
    required_domain_coverage: float = Field(ge=0.0, le=1.0)
    cohort_required_domain_coverage: float = Field(ge=0.0, le=1.0)
    root_outcome_dedup_ok: bool
    population_context_ok: bool
    challenge_separation_state: str
    model_hindsight_risk: str
    status: FoundationStatus
    reasons: list[str] = Field(default_factory=list)


def assess_t0_foundation(
    envelope: T0ContextEnvelopeV1,
    *,
    cohort_required_domain_coverage: float | None = None,
    root_outcome_dedup_ok: bool = True,
    challenge_separation_state: str = "not_assessed",
    material_context_keys: list[str] | None = None,
    max_material_scope_dimensions: int = 3,
) -> T0FoundationAssessmentV1:
    """Deterministically assess one bounded T0 envelope; never infer missing facts."""
    reasons: list[str] = []
    anchor = _instant(envelope.anchor_at)
    temporal_integrity = anchor is not None
    provenance_integrity = bool(envelope.anchor_entity_id and envelope.root_causal_identity)
    if envelope.t0_anchor_kind in {"knowledge_item", "markdown", "trade_case_study"}:
        provenance_integrity = False
        reasons.append("knowledge_artifact_cannot_anchor_t0")
    required = set(envelope.required_domains)
    usable: set[str] = set()
    for domain in envelope.domains:
        if domain.state not in {"available", "not_applicable"}:
            continue
        if (
            domain.domain in required
            and domain.state == "available"
            and domain.source_class in {"model_belief", "human_or_policy_belief"}
        ):
            reasons.append(f"belief_cannot_satisfy_required_fact:{domain.domain}")
            continue
        usable.add(domain.domain)
    coverage = len(required & usable) / len(required) if required else 1.0

    if envelope.capture_mode == "reconstructed_partial":
        provenance_integrity = False
        reasons.append("reconstructed_partial")
    for domain in envelope.domains:
        received = _instant(domain.received_at)
        if received is not None and anchor is not None and received > anchor:
            temporal_integrity = False
            reasons.append(f"future_information:{domain.domain}")
        if domain.state == "integrity_conflict":
            provenance_integrity = False
            reasons.append(f"integrity_conflict:{domain.domain}")
        if envelope.consumer_stage == "ranker_evaluation" and domain.domain.startswith("operator_"):
            temporal_integrity = False
            reasons.append(f"downstream_stage_leakage:{domain.domain}")
    missing = sorted(required - usable)
    reasons.extend(f"required_domain_unavailable:{name}" for name in missing)

    population_required = envelope.path_id.startswith("ranker_") or envelope.path_id in {
        "universe_family_calibration", "filter_ttl_shadow", "filter_parameter_tune",
    }
    population_ok = not population_required or bool(envelope.population_identity)
    if not population_ok:
        reasons.append("population_context_missing")
    if not root_outcome_dedup_ok:
        reasons.append("root_outcome_duplicate")
    if len(set(material_context_keys or [])) > max_material_scope_dimensions:
        reasons.append("research_too_complex")

    cohort_coverage = (
        coverage
        if cohort_required_domain_coverage is None
        else cohort_required_domain_coverage
    )
    eligible = (
        temporal_integrity
        and provenance_integrity
        and coverage == 1.0
        and cohort_coverage == 1.0
        and root_outcome_dedup_ok
        and population_ok
        and "research_too_complex" not in reasons
    )
    status: FoundationStatus = (
        "eligible_for_hypothesis_research" if eligible else "advisory_only"
    )
    return T0FoundationAssessmentV1(
        temporal_integrity=temporal_integrity,
        provenance_integrity=provenance_integrity,
        required_domain_coverage=coverage,
        cohort_required_domain_coverage=cohort_coverage,
        root_outcome_dedup_ok=root_outcome_dedup_ok,
        population_context_ok=population_ok,
        challenge_separation_state=challenge_separation_state,
        model_hindsight_risk="low" if eligible else "material",
        status=status,
        reasons=list(dict.fromkeys(reasons)),
    )


def replay_t0_envelope(
    envelope: T0ContextEnvelopeV1,
    *,
    unavailable_source_refs: set[str],
) -> T0ContextEnvelopeV1:
    """Replay captured projections; exact reconstruction fails honestly after pruning."""
    if envelope.capture_mode == "captured_at_t0":
        return envelope
    changed = False
    domains = []
    for domain in envelope.domains:
        if unavailable_source_refs.intersection(domain.source_refs):
            domains.append(domain.model_copy(update={
                "state": "pruned",
                "values": {},
                "unavailable_reason": "referenced source was pruned",
            }))
            changed = True
        else:
            domains.append(domain)
    if not changed:
        return envelope
    return envelope.model_copy(update={
        "capture_mode": "reconstructed_partial",
        "domains": domains,
        "completeness_reasons": [
            *envelope.completeness_reasons,
            "referenced_source_pruned",
        ],
    })


def _instant(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
