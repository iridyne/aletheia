"""Pure contribution aggregation with outcome deduplication and overlap honesty."""

from __future__ import annotations

from dataclasses import dataclass

from aletheia.learning.evidence import EvidenceClass, quantitative_sample_weight


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
    grouped: dict[tuple[str, str, str, str], list[ContributionObservation]] = {}
    for row in eligible:
        key = (row.overlap_group, row.outcome_key, row.horizon, row.assignment)
        grouped.setdefault(key, []).append(row)

    overlap_n = 0
    values: list[float] = []
    for rows in grouped.values():
        identities = {(row.path_id, row.hypothesis_id) for row in rows}
        if len(identities) > 1:
            overlap_n += 1
        values.append(float(rows[0].net_return_pct))

    raw_n = len(observations)
    eligible_n = len(eligible)
    coverage = eligible_n / raw_n if raw_n else 0.0
    data_quality_status = (
        "no_data" if raw_n == 0
        else "invalid" if eligible_n == 0
        else "incomplete" if coverage < max(0.0, min(1.0, min_coverage))
        else "green"
    )
    return ContributionAggregation(
        raw_n=raw_n,
        eligible_n=eligible_n,
        excluded_n=max(0, raw_n - eligible_n),
        unique_n=len(grouped),
        duplicate_n=max(0, len(eligible) - len(grouped)),
        overlap_n=overlap_n,
        attribution_status="not_identifiable" if overlap_n else "identifiable",
        data_quality_status=data_quality_status,
        authority_action="refreeze" if data_quality_status in {"invalid", "incomplete"} else "hold",
        unique_values=tuple(values),
    )
