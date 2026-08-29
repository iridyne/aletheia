"""Read-only references to existing Memory evidence; no universal content store."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aletheia.learning.evidence import EvidenceClass, quantitative_sample_weight


class MemoryAssetRefV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    asset_type: str
    evidence_class: EvidenceClass
    canonical_source_ref: str
    root_outcome_identity: str
    observed_at: str | None = None
    source_hash: str
    quantitative_weight: int = Field(default=0, ge=0, le=1)

    @field_validator("evidence_class", mode="before")
    @classmethod
    def _normalize_evidence_class(cls, value):
        if isinstance(value, str):
            return value.lower()
        return value

    @field_validator("quantitative_weight", mode="after")
    @classmethod
    def _bounded_weight(cls, value: int) -> int:
        return value

    def independent_quantitative_weight(self) -> int:
        return quantitative_sample_weight(self.evidence_class)


def unique_root_observations(assets: list[MemoryAssetRefV1]) -> tuple[str, ...]:
    """Return stable root identities; artifact multiplicity never adds observations."""
    return tuple(dict.fromkeys(asset.root_outcome_identity for asset in assets))


def independent_quantitative_observation_count(assets: list[MemoryAssetRefV1]) -> int:
    roots = {
        asset.root_outcome_identity
        for asset in assets
        if asset.independent_quantitative_weight()
    }
    return len(roots)
