"""Canonical Learning hypothesis identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

if TYPE_CHECKING:
    from aletheia.learning.models import LearningHypothesis


HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION = 1
HYPOTHESIS_RESEARCH_EVIDENCE_VERSION = "hypothesis-research-evidence/v1"

ResearchStatus = Literal["ready", "narrowed", "rejected", "insufficient_context"]
ResearchText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
ResearchIdentity = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class HypothesisResearchObservationV1(BaseModel):
    """Exact historical fact being explained, never an inferred market state."""

    model_config = ConfigDict(extra="forbid")

    statement: ResearchText
    source_artifact_ids: list[ResearchIdentity] = Field(min_length=1, max_length=20)
    outcome_identities: list[ResearchIdentity] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _identities_are_unique(self) -> HypothesisResearchObservationV1:
        if len(set(self.source_artifact_ids)) != len(self.source_artifact_ids):
            raise ValueError("research source artifact identities must be unique")
        if len(set(self.outcome_identities)) != len(self.outcome_identities):
            raise ValueError("research outcome identities must be unique")
        return self


class HypothesisRelevantContextV1(BaseModel):
    """Only decision-time context plus explicit unavailable dimensions."""

    model_config = ConfigDict(extra="forbid")

    recorded_at_t0: dict[str, Any] = Field(default_factory=dict)
    missing: list[ResearchText] = Field(default_factory=list, max_length=20)
    material_scope: dict[str, Any] = Field(default_factory=dict, max_length=3)

    @model_validator(mode="after")
    def _context_is_explicit(self) -> HypothesisRelevantContextV1:
        if not self.recorded_at_t0 and not self.missing:
            raise ValueError("research context must be recorded or explicitly missing")
        if not set(self.material_scope).issubset(self.recorded_at_t0):
            raise ValueError("material scope must use recorded T0 context keys")
        return self


class HypothesisCounterfactualCheckV1(BaseModel):
    """One bounded check linked to existing exact durable identities."""

    model_config = ConfigDict(extra="forbid")

    check_type: ResearchText
    source_identity: ResearchIdentity
    comparison_identity: ResearchIdentity | None = None
    evidence: dict[str, Any] = Field(min_length=1)


class HypothesisRedTeamSummaryV1(BaseModel):
    """The four answers required from the single bounded causal review."""

    model_config = ConfigDict(extra="forbid")

    strongest_simpler_competing_explanation: ResearchText | None = None
    material_confounders_or_missing: list[ResearchText] = Field(
        default_factory=list, max_length=10,
    )
    smallest_falsification_test: ResearchText
    decision: ResearchStatus
    reasoning: ResearchText


class HypothesisResearchEvidenceV1(BaseModel):
    """Canonical research evidence for an explicit Learning nomination."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal[HYPOTHESIS_RESEARCH_EVIDENCE_VERSION] = (
        HYPOTHESIS_RESEARCH_EVIDENCE_VERSION
    )
    observation: HypothesisResearchObservationV1
    original_candidate_explanation: ResearchText | None = None
    candidate_explanation: ResearchText
    competing_explanations: list[ResearchText] = Field(default_factory=list, max_length=3)
    no_material_alternative_reason: ResearchText | None = None
    relevant_context: HypothesisRelevantContextV1
    falsification_condition: ResearchText
    supporting_evidence: list[ResearchText] = Field(default_factory=list, max_length=20)
    opposing_evidence: list[ResearchText] = Field(default_factory=list, max_length=20)
    missing_evidence: list[ResearchText] = Field(default_factory=list, max_length=20)
    counterfactual_checks: list[HypothesisCounterfactualCheckV1] = Field(
        default_factory=list, max_length=3,
    )
    red_team_summary: HypothesisRedTeamSummaryV1
    research_status: ResearchStatus

    @model_validator(mode="after")
    def _research_contract_is_coherent(self) -> HypothesisResearchEvidenceV1:
        if not self.competing_explanations and not self.no_material_alternative_reason:
            raise ValueError(
                "research evidence requires competing explanations or an explicit "
                "no-material-alternative reason"
            )
        if self.red_team_summary.decision != self.research_status:
            raise ValueError("red-team decision and research status disagree")
        if self.red_team_summary.smallest_falsification_test != self.falsification_condition:
            raise ValueError("red-team falsification test must be the stored condition")
        if self.research_status in {"ready", "narrowed"} and not self.observation.outcome_identities:
            raise ValueError("research-ready evidence requires exact outcome identities")
        if self.research_status == "narrowed":
            if (
                not self.original_candidate_explanation
                or self.original_candidate_explanation == self.candidate_explanation
                or not self.relevant_context.material_scope
            ):
                raise ValueError(
                    "narrowed research requires a changed explanation and material scope"
                )
        return self

    def canonical_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def hypothesis_fingerprint(
    *, path_id: str, scope: dict[str, Any], action: dict[str, Any],
    target_horizon: str, source_cohort: str,
    behavior_contract_version: str | None = None,
    schema_version: int = HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
) -> str:
    payload = {
        "path_id": path_id.lower(),
        "scope": _normalize(scope),
        "action": _normalize(action),
        "target_horizon": target_horizon.lower(),
        "source_cohort": source_cohort.lower(),
        "schema_version": schema_version,
    }
    if behavior_contract_version is not None:
        payload["behavior_contract_version"] = behavior_contract_version.lower()
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def nominate_learning_hypothesis(
    *,
    path_id: str,
    scope: dict[str, Any],
    action: dict[str, Any],
    source_type: str,
    source_artifact_ids: Iterable[str],
    source_cohort: str,
    consumer: str,
    target_horizon: str,
    evidence_summary: HypothesisResearchEvidenceV1 | dict[str, Any] | None = None,
    require_research_evidence: bool = False,
) -> LearningHypothesis:
    """Persist one scoped hypothesis candidate without opening authority state."""
    from aletheia.learning.ledger import (
        get_hypothesis_by_fingerprint,
        insert_hypothesis,
    )
    from aletheia.learning.models import LearningHypothesis
    from aletheia.learning.registry import get_learning_path

    path_id = str(path_id).strip()
    source_type = str(source_type).strip()
    source_cohort = str(source_cohort).strip()
    consumer = str(consumer).strip()
    target_horizon = str(target_horizon).strip()
    if not path_id or not source_type or not source_cohort or not consumer or not target_horizon:
        raise ValueError("hypothesis nomination identity is incomplete")
    if not isinstance(scope, dict) or not scope:
        raise ValueError("hypothesis nomination scope is required")
    if not isinstance(action, dict) or not action:
        raise ValueError("hypothesis nomination action is required")
    artifact_ids = list(dict.fromkeys(
        str(value).strip()
        for value in source_artifact_ids
        if str(value).strip()
    ))
    if not artifact_ids:
        raise ValueError("hypothesis nomination source artifact is required")

    if require_research_evidence:
        try:
            research_evidence = HypothesisResearchEvidenceV1.model_validate(
                evidence_summary or {},
            )
        except Exception as exc:
            raise ValueError("hypothesis research evidence v1 is required") from exc
        if research_evidence.research_status not in {"ready", "narrowed"}:
            raise ValueError(
                "hypothesis research is not ready for nomination: "
                f"{research_evidence.research_status}"
            )
        evidence_payload = research_evidence.canonical_payload()
    else:
        evidence_payload = (
            evidence_summary.canonical_payload()
            if isinstance(evidence_summary, HypothesisResearchEvidenceV1)
            else _normalize(evidence_summary or {})
        )

    path = get_learning_path(path_id)
    if not path.is_learning_treatment:
        raise ValueError(f"hypothesis path is not a Learning treatment: {path_id}")
    fingerprint = hypothesis_fingerprint(
        path_id=path_id,
        scope=scope,
        action=action,
        target_horizon=target_horizon,
        source_cohort=source_cohort,
        behavior_contract_version=path.behavior_contract_version,
    )
    existing = get_hypothesis_by_fingerprint(fingerprint)
    if existing is not None:
        return LearningHypothesis.model_validate(existing)

    hypothesis = LearningHypothesis(
        fingerprint=fingerprint,
        behavior_contract_version=path.behavior_contract_version,
        fingerprint_schema_version=HYPOTHESIS_FINGERPRINT_SCHEMA_VERSION,
        cohort_id=hashlib.sha256(source_cohort.encode("utf-8")).hexdigest()[:16],
        path_id=path_id,
        overlap_group=path.overlap_group,
        source_type=source_type,
        source_artifact_ids_json=json.dumps(artifact_ids, ensure_ascii=True),
        scope_json=json.dumps(_normalize(scope), sort_keys=True, separators=(",", ":")),
        action_json=json.dumps(_normalize(action), sort_keys=True, separators=(",", ":")),
        consumer=consumer,
        target_horizon=target_horizon,
        status="proposed",
        evidence_summary_json=json.dumps(
            evidence_payload,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    insert_hypothesis(hypothesis)
    persisted = get_hypothesis_by_fingerprint(fingerprint)
    if persisted is None:
        raise RuntimeError("learning hypothesis nomination was not persisted")
    return LearningHypothesis.model_validate(persisted)


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).lower(): _normalize(v) for k, v in sorted(value.items()) if str(k).lower() not in {"summary", "title", "created_at", "updated_at"}}
    if isinstance(value, list):
        return sorted((_normalize(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    if isinstance(value, str):
        return value.strip().lower()
    return value
