"""Pure contribution aggregation with outcome deduplication and overlap honesty."""

from __future__ import annotations

from dataclasses import dataclass

from aletheia.learning.evidence import EvidenceClass, quantitative_sample_weight

# P1-2: two outcome rows for the same identity may differ only by floating noise;
# beyond this they are a genuine conflict and the cohort fails closed.
_OUTCOME_VALUE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class ContributionObservation:
    path_id: str
    overlap_group: str
    outcome_key: str
    horizon: str
    assignment: str
    net_return_pct: float
    hypothesis_id: str | None = None
    evidence_class: EvidenceClass = EvidenceClass.SETTLED_QUANTITATIVE
    # Whether the treatment was ACTUALLY applied to this exposure (from the exposure
    # manifest's authority_applied / before!=after). In a shadow-stage experiment both
    # arms run at baseline, so authority_applied is False for every observation — the
    # treatment/control return difference is then noise, not a causal contribution.
    # Callers building observations from exposures MUST copy the manifest value here.
    authority_applied: bool = False


@dataclass(frozen=True)
class ContributionAggregation:
    raw_n: int
    eligible_n: int
    excluded_n: int
    unique_n: int
    duplicate_n: int
    overlap_n: int
    attribution_status: str
    data_quality_status: str
    authority_action: str
    unique_values: tuple[float, ...]
    # P0-2 (2026-07-12): how many treatment-arm observations actually had the
    # treatment applied, and whether a causal (positive/negative) contribution may be
    # derived at all. A shadow-only cohort (no real intervention) is measurable for
    # data completeness but MUST NOT yield a causal promote verdict.
    intervention_applied_n: int = 0
    causal_eligible: bool = False
    # P1-2: outcome-identity groups whose settled values disagreed beyond tolerance.
    # Any conflict makes the cohort invalid + refreeze (never silently pick one).
    conflict_n: int = 0


def aggregate_observations(
    observations: list[ContributionObservation] | tuple[ContributionObservation, ...],
    *,
    min_coverage: float = 1.0,
) -> ContributionAggregation:
    """Deduplicate by overlap group + outcome + horizon + assignment.

    Multiple artifacts may describe or touch one outcome. They remain visible in
    ``raw_n`` but contribute one quantitative sample. If different paths or
    hypotheses share that identity, component credit is explicitly unavailable.
    """
    eligible = [
        row for row in observations
        if quantitative_sample_weight(row.evidence_class) == 1
        and bool(row.outcome_key)
        and bool(row.horizon)
    ]
    # P0-2: a causal contribution requires that the treatment arm was actually
    # intervened on. Shadow-stage observations (authority_applied=False on both arms)
    # only demonstrate the cohort is measurable — they cannot be read as causal.
    intervention_applied_n = sum(
        1 for row in eligible if row.assignment == "treatment" and row.authority_applied
    )
    grouped: dict[tuple[str, str, str, str], list[ContributionObservation]] = {}
    for row in eligible:
        key = (row.overlap_group, row.outcome_key, row.horizon, row.assignment)
        grouped.setdefault(key, []).append(row)

    overlap_n = 0
    conflict_n = 0
    values: list[float] = []
    for rows in grouped.values():
        identities = {(row.path_id, row.hypothesis_id) for row in rows}
        if len(identities) > 1:
            overlap_n += 1
        # P1-2: rows sharing one outcome identity must agree on the net return. If the
        # same instrument+horizon+arm settled to different values (e.g. two cost-model
        # versions, a double resolver, market-type crossover), we must NOT silently
        # take the first row — that lets arbitrary DB ordering decide the verdict.
        nets = [float(r.net_return_pct) for r in rows]
        if max(nets) - min(nets) > _OUTCOME_VALUE_TOLERANCE:
            conflict_n += 1
            continue
        values.append(nets[0])

    raw_n = len(observations)
    eligible_n = len(eligible)
    coverage = eligible_n / raw_n if raw_n else 0.0
    data_quality_status = (
        "no_data" if raw_n == 0
        else "invalid" if eligible_n == 0
        else "conflicting_outcome_values" if conflict_n > 0
        else "incomplete" if coverage < max(0.0, min(1.0, min_coverage))
        else "green"
    )
    causal_eligible = intervention_applied_n > 0 and conflict_n == 0
    if data_quality_status in {"invalid", "incomplete", "conflicting_outcome_values"}:
        # A conflicting cohort fails closed: the whole verdict is invalid and refreezes
        # to the static baseline rather than trusting an arbitrarily-picked value.
        authority_action = "refreeze"
    elif not causal_eligible:
        # Green, measurable data — but nothing was actually applied. Trial-ready only;
        # never a causal promote signal (prevents fake shadow "positive contribution").
        authority_action = "shadow_trial_only"
    else:
        authority_action = "hold"
    return ContributionAggregation(
        raw_n=raw_n,
        eligible_n=eligible_n,
        excluded_n=max(0, raw_n - eligible_n),
        unique_n=len(grouped),
        duplicate_n=max(0, len(eligible) - len(grouped)),
        overlap_n=overlap_n,
        attribution_status="not_identifiable" if overlap_n else "identifiable",
        data_quality_status=data_quality_status,
        authority_action=authority_action,
        unique_values=tuple(values),
        intervention_applied_n=intervention_applied_n,
        causal_eligible=causal_eligible,
        conflict_n=conflict_n,
    )
