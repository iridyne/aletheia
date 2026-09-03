"""Four-mechanism Candidate opportunity-evidence projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal, Mapping, Sequence

from aletheia.universe.candidate_eligibility import canonical_fingerprint
from aletheia.universe.continuous_scoring import ComponentScore, score_all_continuous_families


OPPORTUNITY_EVIDENCE_VERSION = "universe-opportunity-evidence/v2"
_FAMILY_TO_ROLE = {
    "momentum": "continuation",
    "short_bias": "continuation",
    "mean_reversion": "reversal",
    "derivatives": "positioning_forced_flow",
}


@dataclass(frozen=True)
class MechanismEvidenceV1:
    mechanism_id: Literal["continuation", "reversal", "positioning_forced_flow", "information_catalyst"]
    mechanism_version: str
    source_component_id: str | None
    applicability: Literal["applicable", "not_applicable", "unavailable"]
    direction: Literal["long", "short"] | None
    point_strength: float | None
    pessimistic_strength: float | None
    within_run_percentile: float | None
    evidence_refs: tuple[str, ...]
    evidence_group_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    capability_gaps: tuple[str, ...]


@dataclass(frozen=True)
class OpportunityEvidenceBundleV1:
    version: Literal["universe-opportunity-evidence/v2"]
    candidate_key: str
    feature_snapshot_id: str | None
    source_cutoff_at: str
    instrument: Mapping[str, str]
    mechanisms: tuple[MechanismEvidenceV1, ...]
    context: Mapping[str, object]
    mechanism_set_fingerprint: str
    source_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _project(mechanism_id: str, components: Sequence[ComponentScore]) -> MechanismEvidenceV1:
    primary = (
        next((item for item in components if item.component_id == "momentum"), None)
        if mechanism_id == "continuation"
        else components[0] if components else None
    )
    usable = [item for item in components if item.valid and item.direction in {"long", "short"}]
    if primary not in usable:
        usable = []
    if not usable:
        reasons = tuple(sorted({item.invalid_reason or "direction_unavailable" for item in components}))
        return MechanismEvidenceV1(
            mechanism_id, f"{mechanism_id}/v1", primary.component_id if primary else None,
            "unavailable", None, None, None, None,
            (), (), reasons, reasons,
        )
    selected = primary
    assert selected is not None
    refs = tuple(sorted({e.raw_feature_id for e in selected.evidence}))
    groups = tuple(sorted({e.evidence_group_id for e in selected.evidence}))
    point = max(0.0, min(100.0, float(selected.point_score)))
    pessimistic = max(0.0, min(point, float(selected.pessimistic_score)))
    diagnostics = tuple(
        f"diagnostic_component:{item.component_id}:{item.direction}"
        for item in usable if item is not selected
    )
    return MechanismEvidenceV1(
        mechanism_id,
        f"{mechanism_id}/v1",
        selected.component_id,
        "applicable",
        selected.direction,
        point,
        pessimistic,
        None,
        refs,
        groups,
        (f"projected_from:{selected.component_id}", *diagnostics),
        (
            ("liquidation_flow_unavailable", "basis_unavailable", "cross_venue_positioning_unavailable")
            if mechanism_id == "positioning_forced_flow" else ()
        ),
    )


def build_opportunity_evidence(
    feature: Mapping[str, object],
    *,
    candidate_key: str,
    source_cutoff_at: str,
) -> OpportunityEvidenceBundleV1:
    components = score_all_continuous_families(feature)
    by_id = {item.component_id: item for item in components}
    grouped = {
        mechanism: [by_id[family] for family, role in _FAMILY_TO_ROLE.items() if role == mechanism]
        for mechanism in ("continuation", "reversal", "positioning_forced_flow")
    }
    mechanisms = (
        _project("continuation", grouped["continuation"]),
        _project("reversal", grouped["reversal"]),
        _project("positioning_forced_flow", grouped["positioning_forced_flow"]),
        MechanismEvidenceV1(
            "information_catalyst", "information_catalyst/v1", None, "unavailable", None,
            None, None, None, (), (), ("external_event_provenance_unavailable",),
            ("external_event_provenance",),
        ),
    )
    volatility = by_id["volatility"]
    event = by_id["event"]
    context = {
        "volatility_state": {
            "role": "context_only",
            "available": volatility.valid,
            "point_strength": volatility.point_score if volatility.valid else None,
            "pessimistic_strength": volatility.pessimistic_score if volatility.valid else None,
        },
        "regime": feature.get("regime_tag"),
        "shock_state": {
            "role": "context_only",
            "available": event.valid,
            "point_strength": event.point_score if event.valid else None,
        },
    }
    instrument = {
        "venue": str(feature.get("venue") or ""),
        "symbol": str(feature.get("symbol") or ""),
        "market_type": str(feature.get("market_type") or "spot"),
    }
    source_material = {
        "feature_snapshot_id": feature.get("feature_snapshot_id"),
        "source_cutoff_at": source_cutoff_at,
        "instrument": instrument,
    }
    return OpportunityEvidenceBundleV1(
        OPPORTUNITY_EVIDENCE_VERSION,
        candidate_key,
        str(feature.get("feature_snapshot_id")) if feature.get("feature_snapshot_id") else None,
        source_cutoff_at,
        instrument,
        mechanisms,
        context,
        canonical_fingerprint([asdict(item) for item in mechanisms]),
        canonical_fingerprint(source_material),
    )


def annotate_within_run_percentiles(
    bundles: Sequence[OpportunityEvidenceBundleV1],
    *,
    minimum_bucket_size: int = 20,
) -> tuple[OpportunityEvidenceBundleV1, ...]:
    """Average-rank ties inside mechanism + direction + market buckets."""

    buckets: dict[tuple[str, str, str], list[tuple[int, int, float]]] = {}
    for bundle_index, bundle in enumerate(bundles):
        market_type = str(bundle.instrument.get("market_type") or "spot")
        for mechanism_index, mechanism in enumerate(bundle.mechanisms):
            if mechanism.applicability != "applicable" or mechanism.direction is None or mechanism.pessimistic_strength is None:
                continue
            buckets.setdefault((mechanism.mechanism_id, mechanism.direction, market_type), []).append(
                (bundle_index, mechanism_index, mechanism.pessimistic_strength)
            )
    percentiles: dict[tuple[int, int], float] = {}
    for rows in buckets.values():
        if len(rows) < minimum_bucket_size:
            continue
        values = sorted(score for _, _, score in rows)
        for bundle_index, mechanism_index, score in rows:
            indices = [index for index, value in enumerate(values) if value == score]
            average_rank = (indices[0] + indices[-1]) / 2.0
            percentiles[(bundle_index, mechanism_index)] = average_rank / (len(values) - 1)
    result = []
    for bundle_index, bundle in enumerate(bundles):
        mechanisms = tuple(
            replace(item, within_run_percentile=percentiles.get((bundle_index, mechanism_index)))
            for mechanism_index, item in enumerate(bundle.mechanisms)
        )
        result.append(replace(
            bundle,
            mechanisms=mechanisms,
            mechanism_set_fingerprint=canonical_fingerprint([asdict(item) for item in mechanisms]),
        ))
    return tuple(result)


__all__ = [
    "OPPORTUNITY_EVIDENCE_VERSION", "MechanismEvidenceV1", "OpportunityEvidenceBundleV1",
    "annotate_within_run_percentiles", "build_opportunity_evidence",
]
