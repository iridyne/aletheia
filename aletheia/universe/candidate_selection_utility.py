"""Candidate Selection Utility orchestration and bounded admission."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, Mapping, Sequence

from aletheia.universe.candidate_eligibility import HardGateDecisionV1, canonical_fingerprint
from aletheia.universe.economic_utility import EconomicUtilityEstimateV2
from aletheia.universe.opportunity_evidence import OpportunityEvidenceBundleV1


CANDIDATE_SELECTION_UTILITY_VERSION = "candidate-selection-utility/v1"


@dataclass(frozen=True)
class CandidateSelectionUtilityV1:
    version: Literal["candidate-selection-utility/v1"]
    candidate_key: str
    hard_gate: HardGateDecisionV1
    opportunity_evidence: OpportunityEvidenceBundleV1
    primary_mechanism: str | None
    primary_direction: Literal["long", "short"] | None
    economic_estimates: tuple[EconomicUtilityEstimateV2, ...]
    economic_utility_pct: float | None
    utility_floor_pct: float
    admission_status: Literal[
        "admitted", "below_floor", "unavailable", "ineligible", "observe_only", "direction_conflict"
    ]
    legacy_control: Mapping[str, object]
    record_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_candidate_selection_utility(
    *,
    hard_gate: HardGateDecisionV1,
    opportunity_evidence: OpportunityEvidenceBundleV1,
    economic_estimates: Sequence[EconomicUtilityEstimateV2],
    legacy_control: Mapping[str, object],
) -> CandidateSelectionUtilityV1:
    if hard_gate.candidate_key != opportunity_evidence.candidate_key:
        raise ValueError("candidate_selection_identity_mismatch")
    available = [
        item for item in economic_estimates
        if item.estimate_status == "available"
        and item.economic_utility_pct is not None
        and item.tail_evidence.status == "clear"
        and item.min_tradable_edge_pct is not None
    ]
    best_by_direction = {
        direction: max(
            (item for item in available if item.direction == direction),
            key=lambda item: (item.economic_utility_pct, item.mechanism_id),
            default=None,
        )
        for direction in ("long", "short")
    }
    candidates = [item for item in best_by_direction.values() if item is not None]
    primary = max(candidates, key=lambda item: (item.economic_utility_pct, item.mechanism_id), default=None)
    floor = max(0.05, primary.min_tradable_edge_pct) if primary else 0.05
    long = best_by_direction["long"]
    short = best_by_direction["short"]
    direction_conflict = bool(
        long and short
        and long.economic_utility_pct >= max(0.05, long.min_tradable_edge_pct)
        and short.economic_utility_pct >= max(0.05, short.min_tradable_edge_pct)
        and abs(long.economic_utility_pct - short.economic_utility_pct)
        < max(long.min_tradable_edge_pct, short.min_tradable_edge_pct)
    )
    if hard_gate.status == "ineligible":
        status = "ineligible"
    elif hard_gate.status == "unavailable":
        status = "unavailable"
    elif hard_gate.actionability == "observe_only":
        status = "observe_only"
    elif direction_conflict:
        status = "direction_conflict"
        primary = None
    elif primary is None:
        status = "unavailable"
    elif primary.economic_utility_pct < floor:
        status = "below_floor"
    else:
        status = "admitted"
    material = {
        "version": CANDIDATE_SELECTION_UTILITY_VERSION,
        "candidate_key": hard_gate.candidate_key,
        "hard_gate": hard_gate.as_dict(),
        "opportunity_evidence": opportunity_evidence.as_dict(),
        "economic_estimates": [item.as_dict() for item in economic_estimates],
        "primary_mechanism": primary.mechanism_id if primary else None,
        "primary_direction": primary.direction if primary else None,
        "economic_utility_pct": primary.economic_utility_pct if primary else None,
        "utility_floor_pct": floor,
        "admission_status": status,
        "legacy_control": dict(legacy_control),
    }
    return CandidateSelectionUtilityV1(
        CANDIDATE_SELECTION_UTILITY_VERSION,
        hard_gate.candidate_key,
        hard_gate,
        opportunity_evidence,
        primary.mechanism_id if primary else None,
        primary.direction if primary else None,
        tuple(economic_estimates),
        primary.economic_utility_pct if primary else None,
        floor,
        status,
        dict(legacy_control),
        canonical_fingerprint(material),
    )


def select_admitted_candidates(
    decisions: Sequence[CandidateSelectionUtilityV1],
    *,
    max_candidates: int,
) -> tuple[CandidateSelectionUtilityV1, ...]:
    if max_candidates < 0:
        raise ValueError("max_candidates_must_be_non_negative")
    admitted = [item for item in decisions if item.admission_status == "admitted"]
    admitted.sort(key=lambda item: (-float(item.economic_utility_pct), item.candidate_key))
    return tuple(admitted[:max_candidates])


__all__ = [
    "CANDIDATE_SELECTION_UTILITY_VERSION", "CandidateSelectionUtilityV1",
    "build_candidate_selection_utility", "select_admitted_candidates",
]
