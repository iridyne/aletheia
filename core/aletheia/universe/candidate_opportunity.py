"""Typed, replayable Opportunity authority carried by a Candidate."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia.universe.economic_utility import reason_proves_evidence_absent
from aletheia.utils.json_utils import canonical_json_strict


CANDIDATE_OPPORTUNITY_CONTEXT_VERSION = "candidate-opportunity-context/v1"
OPPORTUNITY_HORIZON = "4h"
LEGACY_MECHANISM_MAP = {
    "momentum": "continuation",
    "short_bias": "continuation",
    "mean_reversion": "reversal",
    "derivatives": "positioning_forced_flow",
}


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json_strict(value).encode()).hexdigest()


class CandidateOpportunityContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["candidate-opportunity-context/v1"] = CANDIDATE_OPPORTUNITY_CONTEXT_VERSION
    candidate_id: str = Field(min_length=1)
    candidate_selection: dict[str, Any]
    source: dict[str, Any]
    opportunity: dict[str, Any]
    context: dict[str, Any]
    context_hash: str = ""

    @model_validator(mode="after")
    def _validate_and_hash(self) -> "CandidateOpportunityContext":
        selection = self.candidate_selection
        source = self.source
        if any(key in source for key in ("kind", "id", "trade_school")) or selection.get("policy") == "serenity_research_v1":
            school = {"universe": "short_term", "serenity": "serenity"}.get(source.get("kind"))
            if school is None or source.get("trade_school") != school or not isinstance(source.get("id"), str) or not source["id"].strip():
                raise ValueError("candidate_opportunity_source_identity_invalid")
            if source["kind"] == "universe" and source["id"] != self.candidate_id:
                raise ValueError("candidate_opportunity_source_identity_invalid")
            if (source["kind"] == "serenity") != (selection.get("policy") == "serenity_research_v1"):
                raise ValueError("candidate_opportunity_source_policy_invalid")
        opportunity = self.opportunity
        if selection.get("policy") not in {"legacy_total_score_v1", "candidate_selection_utility_v1", "candidate_selection_utility", "serenity_research_v1"}:
            raise ValueError("candidate_opportunity_policy_invalid")
        if selection.get("policy") in {"candidate_selection_utility_v1", "candidate_selection_utility"} and not selection.get("policy_epoch"):
            raise ValueError("candidate_opportunity_policy_epoch_missing")
        source_record_hash = str(selection.get("source_record_hash") or "")
        if len(source_record_hash) != 64 or any(
            character not in "0123456789abcdef" for character in source_record_hash
        ):
            raise ValueError("candidate_opportunity_source_record_hash_missing")
        if not source.get("detected_at") or not source.get("source_cutoff_at"):
            raise ValueError("candidate_opportunity_source_clock_missing")
        try:
            detected_at = datetime.fromisoformat(
                str(source["detected_at"]).replace("Z", "+00:00")
            )
            source_cutoff_at = datetime.fromisoformat(
                str(source["source_cutoff_at"]).replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("candidate_opportunity_source_clock_invalid") from exc
        if (
            detected_at.tzinfo is None
            or source_cutoff_at.tzinfo is None
            or source_cutoff_at.astimezone(timezone.utc)
            > detected_at.astimezone(timezone.utc)
        ):
            raise ValueError("candidate_opportunity_source_clock_invalid")
        if opportunity.get("primary_mechanism") not in {
            "continuation", "reversal", "positioning_forced_flow", "information_catalyst",
        }:
            raise ValueError("candidate_opportunity_mechanism_invalid")
        if opportunity.get("direction") not in {"long", "short"}:
            raise ValueError("candidate_opportunity_direction_invalid")
        if opportunity.get("horizon") != OPPORTUNITY_HORIZON:
            raise ValueError("candidate_opportunity_horizon_invalid")
        if opportunity.get("mechanism_source") not in {
            "target_authority", "legacy_projection", "research_authority"
        } or not str(opportunity.get("mechanism_version") or "").strip():
            raise ValueError("candidate_opportunity_mechanism_identity_invalid")
        if not str(opportunity.get("source_component_id") or "").strip():
            raise ValueError("candidate_opportunity_source_component_missing")
        if opportunity.get("capability_status") not in {"supported", "observe_only", "unavailable"}:
            raise ValueError("candidate_opportunity_capability_invalid")
        utility = opportunity.get("economic_utility_pct")
        basis = opportunity.get("admission_basis")
        startup_reason = opportunity.get("startup_reason")
        if basis not in {None, "measured", "startup"}:
            raise ValueError("candidate_opportunity_admission_basis_invalid")
        if basis == "startup":
            strength = opportunity.get("pessimistic_strength")
            if (
                selection.get("policy") not in {"candidate_selection_utility_v1", "candidate_selection_utility"}
                or selection.get("decision_version") != "candidate-selection-utility/v2"
                or not reason_proves_evidence_absent(startup_reason)
                or utility is not None
                or opportunity.get("mechanism_source") != "target_authority"
                or opportunity.get("capability_status") != "supported"
                or opportunity.get("capability_gaps")
                or isinstance(strength, bool)
                or not isinstance(strength, (int, float))
                or not math.isfinite(strength)
                or not 0 <= strength <= 100
            ):
                raise ValueError("candidate_opportunity_startup_invalid")
        else:
            if startup_reason is not None:
                raise ValueError("candidate_opportunity_startup_reason_invalid")
            if selection.get("policy") in {"candidate_selection_utility_v1", "candidate_selection_utility"} and (
                isinstance(utility, bool)
                or not isinstance(utility, (int, float))
                or not math.isfinite(float(utility))
            ):
                raise ValueError("candidate_opportunity_utility_invalid")
        if (selection.get("decision_version") == "candidate-selection-utility/v2"
                and basis is None):
            raise ValueError("candidate_opportunity_admission_basis_missing")
        for field, limit in (
            ("capability_gaps", 16), ("primary_evidence_refs", 32),
            ("secondary_mechanisms", 4),
        ):
            value = opportunity.get(field)
            if not isinstance(value, (list, tuple)) or len(value) > limit:
                raise ValueError(f"candidate_opportunity_{field}_invalid")
        expected = fingerprint(self.model_dump(mode="json", exclude={"context_hash"}))
        if self.context_hash and self.context_hash != expected:
            raise ValueError("candidate_opportunity_context_hash_mismatch")
        object.__setattr__(self, "context_hash", expected)
        return self


def target_candidate_opportunity_context(
    *,
    candidate_id: str,
    decision: Mapping[str, Any],
    policy_epoch: str,
    source_record_hash: str,
    detected_at: str,
) -> CandidateOpportunityContext:
    evidence = decision["opportunity_evidence"]
    mechanisms = tuple(evidence["mechanisms"])
    primary = next(
        item for item in mechanisms if item["mechanism_id"] == decision["primary_mechanism"]
    )
    gaps = tuple(str(value) for value in primary.get("capability_gaps") or ())
    capability = "observe_only" if gaps else "supported"
    return CandidateOpportunityContext(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": "candidate_selection_utility",
            "policy_epoch": policy_epoch,
            "source_record_hash": source_record_hash,
            "decision_version": decision["version"],
        },
        source={
            "kind": "universe", "id": candidate_id, "trade_school": "short_term",
            "detected_at": detected_at,
            "source_cutoff_at": evidence["source_cutoff_at"],
            "feature_snapshot_id": evidence.get("feature_snapshot_id"),
            "feature_snapshot_unavailable_reason": (
                None if evidence.get("feature_snapshot_id") else "feature_snapshot_id_unavailable"
            ),
        },
        opportunity={
            "primary_mechanism": decision["primary_mechanism"],
            "mechanism_version": primary["mechanism_version"],
            "mechanism_source": "target_authority",
            "source_component_id": primary.get("source_component_id"),
            "direction": decision["primary_direction"],
            "horizon": OPPORTUNITY_HORIZON,
            "economic_utility_pct": decision["economic_utility_pct"],
            "admission_basis": decision.get("admission_basis"),
            "startup_reason": decision.get("startup_reason"),
            "pessimistic_strength": primary.get("pessimistic_strength"),
            "capability_status": capability,
            "capability_gaps": gaps,
            "primary_evidence_refs": tuple(primary.get("evidence_refs") or ()),
            "secondary_mechanisms": tuple(
                item for item in mechanisms if item["mechanism_id"] != decision["primary_mechanism"]
            ),
        },
        context=dict(evidence.get("context") or {}),
    )


def legacy_candidate_opportunity_context(
    *,
    candidate_id: str,
    candidate: Mapping[str, Any],
    detected_at: str,
) -> CandidateOpportunityContext | None:
    family = str(candidate.get("scanner_family") or "")
    mechanism = LEGACY_MECHANISM_MAP.get(family)
    direction = str(candidate.get("scanner_direction") or candidate.get("direction") or "")
    if mechanism is None or direction not in {"long", "short"}:
        return None
    gaps = (
        ("liquidation_flow_unavailable", "basis_unavailable", "cross_venue_positioning_unavailable")
        if mechanism == "positioning_forced_flow" else ()
    )
    source_material = {
        "venue": candidate.get("venue"),
        "symbol": candidate.get("symbol"),
        "market_type": candidate.get("market_type"),
        "scanner_family": family,
        "direction": direction,
        "feature_snapshot_id": candidate.get("feature_snapshot_id"),
        "detected_at": detected_at,
    }
    return CandidateOpportunityContext(
        candidate_id=candidate_id,
        candidate_selection={
            "policy": "legacy_total_score_v1",
            "policy_epoch": None,
            "source_record_hash": fingerprint(source_material),
        },
        source={
            "kind": "universe", "id": candidate_id, "trade_school": "short_term",
            "detected_at": detected_at,
            "source_cutoff_at": detected_at,
            "feature_snapshot_id": candidate.get("feature_snapshot_id"),
            "feature_snapshot_unavailable_reason": (
                None if candidate.get("feature_snapshot_id") else "feature_snapshot_id_unavailable"
            ),
        },
        opportunity={
            "primary_mechanism": mechanism,
            "mechanism_version": f"legacy-{family}/v1",
            "mechanism_source": "legacy_projection",
            "source_component_id": family,
            "direction": direction,
            "horizon": OPPORTUNITY_HORIZON,
            "economic_utility_pct": None,
            "capability_status": "observe_only" if gaps else "supported",
            "capability_gaps": gaps,
            "primary_evidence_refs": (),
            "secondary_mechanisms": (),
        },
        context={
            "volatility_state": candidate.get("volatility_state"),
            "regime": candidate.get("regime_tag"),
            "shock_state": candidate.get("shock_state"),
        },
    )

def serenity_candidate_opportunity_context(
    *, candidate_id: str, opportunity: Mapping[str, Any], feature: Mapping[str, Any],
    source_record_hash: str, detected_at: str,
) -> CandidateOpportunityContext:
    """Research proves provenance; the shared PA evaluator proves timing."""
    from aletheia.trading.entry_profiles import compatible_entry_profiles
    from aletheia.universe.opportunity_evidence import build_opportunity_evidence

    evidence = build_opportunity_evidence(feature, candidate_key=candidate_id, source_cutoff_at=detected_at)
    supported = [item for item in evidence.mechanisms
                 if item.applicability == "applicable" and not item.capability_gaps
                 and compatible_entry_profiles(item.mechanism_id)]
    if not supported:
        raise ValueError("serenity_pa_unavailable")
    primary = max(supported, key=lambda item: item.pessimistic_strength)
    if primary.direction != opportunity["direction"]:
        raise ValueError("serenity_pa_direction_conflict")
    return CandidateOpportunityContext(
        candidate_id=candidate_id,
        candidate_selection={"policy": "serenity_research_v1", "policy_epoch": "serenity_research_v1",
                             "source_record_hash": source_record_hash},
        source={"kind": "serenity", "id": opportunity["id"], "trade_school": "serenity",
                "detected_at": detected_at, "source_cutoff_at": detected_at,
                "feature_snapshot_id": feature["feature_snapshot_id"]},
        opportunity={"primary_mechanism": primary.mechanism_id, "direction": primary.direction,
                     "horizon": OPPORTUNITY_HORIZON, "mechanism_version": primary.mechanism_version,
                     "mechanism_source": "research_authority", "source_component_id": primary.source_component_id,
                     "economic_utility_pct": None, "capability_status": "supported", "capability_gaps": [],
                     "primary_evidence_refs": list(primary.evidence_refs), "secondary_mechanisms": [],
                     "pessimistic_strength": primary.pessimistic_strength},
        context=dict(evidence.context),
    )


__all__ = [
    "CANDIDATE_OPPORTUNITY_CONTEXT_VERSION", "CandidateOpportunityContext",
    "LEGACY_MECHANISM_MAP", "legacy_candidate_opportunity_context",
    "target_candidate_opportunity_context",
]
