"""Candidate Selection Utility orchestration and bounded admission."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Literal, Sequence

from aletheia.universe.candidate_eligibility import HardGateDecisionV1, canonical_fingerprint
from aletheia.universe.economic_utility import EconomicUtilityEstimateV2
from aletheia.universe.opportunity_evidence import OpportunityEvidenceBundleV1


CANDIDATE_SELECTION_UTILITY_VERSION = "candidate-selection-utility/v2"


@dataclass(frozen=True)
class CandidateSelectionUtilityV1:
    version: Literal["candidate-selection-utility/v2"]
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
    record_fingerprint: str
    admission_basis: Literal["measured", "startup"] | None = None
    startup_reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def build_candidate_selection_utility(
    *,
    hard_gate: HardGateDecisionV1,
    opportunity_evidence: OpportunityEvidenceBundleV1,
    economic_estimates: Sequence[EconomicUtilityEstimateV2],
) -> CandidateSelectionUtilityV1:
    if hard_gate.candidate_key != opportunity_evidence.candidate_key:
        raise ValueError("candidate_selection_identity_mismatch")
    if any(item.candidate_key != hard_gate.candidate_key
           or item.venue != opportunity_evidence.instrument["venue"]
           or item.market_type != opportunity_evidence.instrument["market_type"]
           for item in economic_estimates):
        raise ValueError("candidate_selection_economic_identity_mismatch")
    available = [
        item for item in economic_estimates
        if item.estimate_status == "available"
        and item.economic_utility_pct is not None and math.isfinite(item.economic_utility_pct)
        and item.tail_evidence.status == "clear"
        and item.min_tradable_edge_pct is not None and math.isfinite(item.min_tradable_edge_pct)
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
    basis = "measured" if status == "admitted" else None
    startup_reason = None
    startup = None
    scarcity = {"no_history", "parent_sample_insufficient", "child_sample_insufficient"}
    usable = [item for item in opportunity_evidence.mechanisms
              if item.applicability == "applicable" and item.direction in {"long", "short"}
              and not item.capability_gaps and item.pessimistic_strength is not None
              and math.isfinite(item.pessimistic_strength) and 0 < item.pessimistic_strength <= 100]
    estimates = {(item.mechanism_id, item.direction): item for item in economic_estimates}
    if status == "unavailable" and hard_gate.status == "eligible" and hard_gate.actionability != "observe_only":
        # Every usable route must be sparse: never evade a known economic rejection.
        sparse = bool(usable) and all(item.unavailable_reason in scarcity for item in economic_estimates) and all(
            (estimate := estimates.get((item.mechanism_id, item.direction))) is not None
            and estimate.unavailable_reason in scarcity for item in usable
        )
        if sparse and len({item.direction for item in usable}) > 1:
            status = "direction_conflict"
        elif sparse:
            startup = min(usable, key=lambda item: (-item.pessimistic_strength, item.mechanism_id))
            startup_reason = estimates[(startup.mechanism_id, startup.direction)].unavailable_reason
            status, basis = "admitted", "startup"
    primary_mechanism = primary.mechanism_id if primary else startup.mechanism_id if startup else None
    primary_direction = primary.direction if primary else startup.direction if startup else None
    material = {
        "version": CANDIDATE_SELECTION_UTILITY_VERSION,
        "candidate_key": hard_gate.candidate_key,
        "hard_gate": hard_gate.as_dict(),
        "opportunity_evidence": opportunity_evidence.as_dict(),
        "economic_estimates": [item.as_dict() for item in economic_estimates],
        "primary_mechanism": primary_mechanism,
        "primary_direction": primary_direction,
        "economic_utility_pct": primary.economic_utility_pct if primary else None,
        "utility_floor_pct": floor,
        "admission_status": status,
        "admission_basis": basis,
        "startup_reason": startup_reason,
    }
    return CandidateSelectionUtilityV1(
        CANDIDATE_SELECTION_UTILITY_VERSION,
        hard_gate.candidate_key,
        hard_gate,
        opportunity_evidence,
        primary_mechanism,
        primary_direction,
        tuple(economic_estimates),
        primary.economic_utility_pct if primary else None,
        floor,
        status,
        canonical_fingerprint(material),
        basis,
        startup_reason,
    )


def candidate_selection_rank(
    admission_basis: str | None, economic_utility_pct: float | None,
    pessimistic_strength: float | None, candidate_key: str,
) -> tuple[int, float, str]:
    """One measured-first order for admission, deduplication, and projection."""
    measured = admission_basis != "startup" and economic_utility_pct is not None
    value = economic_utility_pct if measured else pessimistic_strength
    return (0 if measured else 1, -value if value is not None else math.inf, candidate_key)


def decision_rank(item: CandidateSelectionUtilityV1) -> tuple[int, float, str]:
    strength = next((mechanism.pessimistic_strength for mechanism in item.opportunity_evidence.mechanisms
                     if mechanism.mechanism_id == item.primary_mechanism), None)
    return candidate_selection_rank(item.admission_basis, item.economic_utility_pct, strength, item.candidate_key)

def select_admitted_candidates(
    decisions: Sequence[CandidateSelectionUtilityV1],
    *,
    max_candidates: int,
) -> tuple[CandidateSelectionUtilityV1, ...]:
    if max_candidates < 0:
        raise ValueError("max_candidates_must_be_non_negative")
    admitted = [item for item in decisions if item.admission_status == "admitted"]
    admitted.sort(key=decision_rank)
    return tuple(admitted[:max_candidates])


__all__ = [
    "CANDIDATE_SELECTION_UTILITY_VERSION", "CandidateSelectionUtilityV1",
    "build_candidate_selection_utility", "select_admitted_candidates",
    "candidate_selection_rank", "decision_rank",
]
