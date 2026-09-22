"""Bounded contrastive evidence over canonical Memory references."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from extensions.learning.memory_assets import MemoryAssetRefV1


ContrastRole = Literal[
    "anchor", "matched_success", "matched_failure", "nearest_counterexample",
    "accepted_false_positive", "missed_alpha", "contradiction",
]


class ContrastiveEvidenceItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ContrastRole
    asset: MemoryAssetRefV1


class ContrastiveEvidenceBundleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["learning-contrastive-evidence/v1"] = (
        "learning-contrastive-evidence/v1"
    )
    path_id: str
    discovery_items: list[ContrastiveEvidenceItemV1] = Field(max_length=20)
    challenge_items: list[ContrastiveEvidenceItemV1] = Field(default_factory=list, max_length=20)
    unavailable_dimensions: list[str] = Field(default_factory=list)
    contamination_checks: list[str] = Field(default_factory=list)
    root_outcome_identities: list[str] = Field(default_factory=list, max_length=20)
    challenge_separation_state: Literal[
        "disjoint", "overlap_detected", "challenge_unavailable",
    ]

    @model_validator(mode="after")
    def _bounded_and_identity_honest(self) -> "ContrastiveEvidenceBundleV1":
        if len(self.discovery_items) + len(self.challenge_items) > 20:
            raise ValueError("contrastive evidence is bounded to 20 source assets")
        discovery_roots = {
            item.asset.root_outcome_identity for item in self.discovery_items
        }
        challenge_roots = {
            item.asset.root_outcome_identity for item in self.challenge_items
        }
        overlap = discovery_roots & challenge_roots
        expected = (
            "challenge_unavailable" if not self.challenge_items
            else "overlap_detected" if overlap
            else "disjoint"
        )
        if self.challenge_separation_state != expected:
            raise ValueError("challenge separation state disagrees with root identities")
        unique_roots = list(dict.fromkeys(
            item.asset.root_outcome_identity
            for item in [*self.discovery_items, *self.challenge_items]
        ))
        if self.root_outcome_identities != unique_roots:
            raise ValueError("root outcome identities must be deduplicated in source order")
        return self


def build_contrastive_bundle(
    *,
    path_id: str,
    discovery_items: list[ContrastiveEvidenceItemV1],
    challenge_items: list[ContrastiveEvidenceItemV1],
    unavailable_dimensions: list[str] | None = None,
    contamination_checks: list[str] | None = None,
) -> ContrastiveEvidenceBundleV1:
    discovery_roots = {item.asset.root_outcome_identity for item in discovery_items}
    challenge_roots = {item.asset.root_outcome_identity for item in challenge_items}
    state = (
        "challenge_unavailable" if not challenge_items
        else "overlap_detected" if discovery_roots & challenge_roots
        else "disjoint"
    )
    roots = list(dict.fromkeys(
        item.asset.root_outcome_identity
        for item in [*discovery_items, *challenge_items]
    ))
    return ContrastiveEvidenceBundleV1(
        path_id=path_id,
        discovery_items=discovery_items,
        challenge_items=challenge_items,
        unavailable_dimensions=list(dict.fromkeys(unavailable_dimensions or [])),
        contamination_checks=list(dict.fromkeys(contamination_checks or [])),
        root_outcome_identities=roots,
        challenge_separation_state=state,
    )
