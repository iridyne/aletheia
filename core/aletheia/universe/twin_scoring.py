"""Step-authoritative, non-persistent Universe twin-scoring adapter.

This module compares the frozen step scorer with the continuous scorer in
memory.  It has no storage, Learning, Risk, Execution, signal-emission, or
allocation imports.  A twin result is diagnostic only and never supplies an
authority score.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from numbers import Real
from typing import Mapping, Sequence

from aletheia.universe.continuous_scoring import (
    CONTINUOUS_SCORER_BEHAVIOR_VERSION,
    CandidateScore,
    ContinuousScoringPolicy,
    evaluate_continuous_candidate,
)
from aletheia.universe.scanners import UNIVERSE_SCORER_BEHAVIOR_VERSION


TWIN_SHADOW_RECORD_VERSION = "universe-twin-shadow-record-v1"

_SCORE_THRESHOLDS = (
    ("explore_admission", 20.0),
    ("opportunity_emission", 45.0),
    ("high_label", 60.0),
)


@dataclass(frozen=True)
class ScoreThresholdComparison:
    threshold_id: str
    threshold: float
    authority_passed: bool
    shadow_point_passed: bool
    shadow_pessimistic_passed: bool

    @property
    def point_crossed(self) -> bool:
        return self.authority_passed != self.shadow_point_passed

    @property
    def pessimistic_crossed(self) -> bool:
        return self.authority_passed != self.shadow_pessimistic_passed


@dataclass(frozen=True)
class TwinShadowInput:
    feature: Mapping[str, object]
    authority_scanner_result: Mapping[str, object]
    authority_total_score: float
    authority_score_audit: Mapping[str, object]


@dataclass(frozen=True)
class TwinShadowRecord:
    record_version: str
    scanner_run_id: str
    candidate_key: str
    feature_snapshot_id: str | None
    asset_id: str | None
    symbol: str
    market_type: str
    authority_behavior_version: str
    shadow_behavior_version: str
    authority_family: str | None
    authority_direction: str | None
    authority_opportunity_score: float
    authority_total_score: float
    external_opportunity_multiplier: float
    external_multiplier_contract: str
    calibration_authority_applied: bool
    shadow: CandidateScore
    threshold_comparisons: tuple[ScoreThresholdComparison, ...]
    authority_rank: int | None = None
    shadow_point_rank: int | None = None
    shadow_pessimistic_rank: int | None = None
    fine_filter_passed: bool | None = None
    authority_selected: bool | None = None
    shadow_point_selected: bool | None = None
    shadow_pessimistic_selected: bool | None = None
    shadow_authority_applied: bool = False
    promotion_eligible: bool = False
    unavailable_promotion_identities: tuple[str, ...] = (
        "policy_epoch",
        "feature_source_fingerprint",
        "availability_profile",
        "outcome_contract",
        "cost_contract",
        "metric_version",
        "assignment",
        "propensity",
    )
    blocking_reasons: tuple[str, ...] = ("task4_diagnostic_only",)

    @property
    def family_changed(self) -> bool:
        return self.shadow.valid and self.shadow.point_family != self.authority_family

    @property
    def direction_changed(self) -> bool:
        return self.shadow.valid and self.shadow.point_direction != self.authority_direction

    @property
    def point_score_delta(self) -> float | None:
        if not self.shadow.valid:
            return None
        return self.shadow.point_score - self.authority_total_score

    @property
    def pessimistic_score_delta(self) -> float | None:
        if not self.shadow.valid:
            return None
        return self.shadow.pessimistic_score - self.authority_total_score

    @property
    def point_rank_delta(self) -> int | None:
        if self.authority_rank is None or self.shadow_point_rank is None:
            return None
        return self.shadow_point_rank - self.authority_rank

    @property
    def pessimistic_rank_delta(self) -> int | None:
        if self.authority_rank is None or self.shadow_pessimistic_rank is None:
            return None
        return self.shadow_pessimistic_rank - self.authority_rank

    @property
    def point_selection_changed(self) -> bool | None:
        if self.authority_selected is None or self.shadow_point_selected is None:
            return None
        return self.authority_selected != self.shadow_point_selected

    @property
    def pessimistic_selection_changed(self) -> bool | None:
        if self.authority_selected is None or self.shadow_pessimistic_selected is None:
            return None
        return self.authority_selected != self.shadow_pessimistic_selected


@dataclass(frozen=True)
class TwinShadowSummary:
    record_version: str
    candidate_count: int
    valid_shadow_count: int
    invalid_shadow_count: int
    family_change_count: int
    direction_change_count: int
    point_threshold_crossing_count: int
    pessimistic_threshold_crossing_count: int
    budget_exceeded_count: int
    concentrated_evidence_count: int
    single_source_evidence_count: int
    missingness_count: int
    interaction_residual_unavailable_count: int
    authority_selected_count: int
    shadow_point_selected_count: int
    shadow_pessimistic_selected_count: int
    point_selection_change_count: int
    pessimistic_selection_change_count: int


def _invalid_candidate(reason: str) -> CandidateScore:
    return CandidateScore(
        CONTINUOUS_SCORER_BEHAVIOR_VERSION,
        None,
        None,
        None,
        None,
        0.0,
        0.0,
        False,
        reason,
        (),
        (),
    )


def _finite_score(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{field}_not_finite")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError(f"{field}_not_finite")
    return parsed


def _source_identity(feature: Mapping[str, object]) -> tuple[str | None, str | None, str, str]:
    feature_snapshot_id = feature.get("feature_snapshot_id")
    asset_id = feature.get("asset_id")
    return (
        str(feature_snapshot_id) if feature_snapshot_id else None,
        str(asset_id) if asset_id else None,
        str(feature.get("symbol") or ""),
        str(feature.get("market_type") or "spot"),
    )


def _candidate_key(scanner_run_id: str, feature: Mapping[str, object]) -> str:
    feature_id, asset_id, symbol, market_type = _source_identity(feature)
    source_id = feature_id or asset_id or "not_available"
    return f"universe-twin:{scanner_run_id or 'not_available'}:{source_id}:{market_type}:{symbol or 'not_available'}"


def _threshold_comparisons(
    authority_score: float,
    shadow: CandidateScore,
) -> tuple[ScoreThresholdComparison, ...]:
    return tuple(
        ScoreThresholdComparison(
            threshold_id,
            threshold,
            authority_score >= threshold,
            shadow.valid and shadow.point_score >= threshold,
            shadow.valid and shadow.pessimistic_score >= threshold,
        )
        for threshold_id, threshold in _SCORE_THRESHOLDS
    )


def _authority_scores(
    item: TwinShadowInput,
    step: Mapping[str, object],
) -> tuple[float, float, str | None]:
    try:
        total = _finite_score(item.authority_total_score, "authority_total_score")
        opportunity = _finite_score(step.get("opportunity_score", 0.0), "authority_opportunity_score")
    except ValueError as exc:
        return 0.0, 0.0, str(exc)
    return total, opportunity, None


def _external_multiplier(
    audit: Mapping[str, object],
) -> tuple[float, str, tuple[str, ...]]:
    contract = str(audit.get("contract") or "not_available")
    blockers: set[str] = set()
    if contract != "universe-authority-score-audit/v1":
        blockers.add("authority_score_audit_not_available")
        return 1.0, contract, tuple(sorted(blockers))
    try:
        multiplier = _finite_score(
            audit.get("effective_opportunity_multiplier", 1.0),
            "external_opportunity_multiplier",
        )
        if multiplier < 0:
            raise ValueError("external_opportunity_multiplier_negative")
    except ValueError:
        multiplier = 1.0
        blockers.add("external_multiplier_invalid")
    return multiplier, contract, tuple(sorted(blockers))


def _evaluate_shadow(
    feature: Mapping[str, object],
    base_policy: ContinuousScoringPolicy,
    external_multiplier: float,
    authority_error: str | None,
) -> CandidateScore:
    if authority_error is not None:
        return _invalid_candidate(authority_error)
    try:
        policy = replace(
            base_policy,
            external_opportunity_multiplier=external_multiplier,
        )
        return evaluate_continuous_candidate(feature, policy)
    except Exception as exc:  # Shadow must never fail the authority pipeline.
        return _invalid_candidate(f"shadow_evaluation_failed:{type(exc).__name__}")


def _record_blockers(
    *,
    scanner_run_id: str,
    feature_snapshot_id: str | None,
    asset_id: str | None,
    symbol: str,
    step: Mapping[str, object],
    shadow: CandidateScore,
    multiplier_blockers: Sequence[str],
) -> tuple[str, ...]:
    blockers = {"task4_diagnostic_only", *multiplier_blockers, *shadow.flags}
    if not scanner_run_id or not (feature_snapshot_id or asset_id) or not symbol:
        blockers.add("candidate_identity_not_available")
    if not shadow.valid:
        blockers.add("shadow_invalid")
    reasons = step.get("reasons")
    if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
        if any("no chase" in str(reason).lower() for reason in reasons):
            blockers.add("authority_no_chase_gate")
    return tuple(sorted(blockers))


def build_twin_shadow_record(
    item: TwinShadowInput,
    *,
    scanner_run_id: str,
    base_policy: ContinuousScoringPolicy,
) -> TwinShadowRecord:
    """Build one fail-isolated diagnostic record without mutating inputs."""

    feature = dict(item.feature)
    step = dict(item.authority_scanner_result)
    audit = dict(item.authority_score_audit)
    authority_total, authority_opportunity, authority_error = _authority_scores(item, step)
    external_multiplier, audit_contract, multiplier_blockers = _external_multiplier(audit)
    shadow = _evaluate_shadow(
        feature,
        base_policy,
        external_multiplier,
        authority_error,
    )
    feature_snapshot_id, asset_id, symbol, market_type = _source_identity(feature)
    authority_family = step.get("scanner_family")
    authority_direction = (
        step.get("scanner_direction")
        or step.get("direction")
        or step.get("proposed_direction")
    )
    return TwinShadowRecord(
        record_version=TWIN_SHADOW_RECORD_VERSION,
        scanner_run_id=scanner_run_id,
        candidate_key=_candidate_key(scanner_run_id, feature),
        feature_snapshot_id=feature_snapshot_id,
        asset_id=asset_id,
        symbol=symbol,
        market_type=market_type,
        authority_behavior_version=UNIVERSE_SCORER_BEHAVIOR_VERSION,
        shadow_behavior_version=CONTINUOUS_SCORER_BEHAVIOR_VERSION,
        authority_family=str(authority_family) if authority_family else None,
        authority_direction=str(authority_direction) if authority_direction else None,
        authority_opportunity_score=authority_opportunity,
        authority_total_score=authority_total,
        external_opportunity_multiplier=external_multiplier,
        external_multiplier_contract=audit_contract,
        calibration_authority_applied=(
            audit_contract == "universe-authority-score-audit/v1" and audit.get("calibration_authority_applied") is True
        ),
        shadow=shadow,
        threshold_comparisons=_threshold_comparisons(authority_total, shadow),
        blocking_reasons=_record_blockers(
            scanner_run_id=scanner_run_id,
            feature_snapshot_id=feature_snapshot_id,
            asset_id=asset_id,
            symbol=symbol,
            step=step,
            shadow=shadow,
            multiplier_blockers=multiplier_blockers,
        ),
    )


def _score_ranks(
    records: Sequence[TwinShadowRecord],
    score_kind: str,
) -> dict[int, int]:
    ranked: list[tuple[int, float, str]] = []
    for index, record in enumerate(records):
        if score_kind == "authority":
            score = record.authority_total_score
        elif score_kind == "point" and record.shadow.valid:
            score = record.shadow.point_score
        elif score_kind == "pessimistic" and record.shadow.valid:
            score = record.shadow.pessimistic_score
        else:
            continue
        ranked.append((index, score, record.candidate_key))
    ranked.sort(key=lambda item: (-item[1], item[2]))
    return {index: rank for rank, (index, _score, _key) in enumerate(ranked, 1)}


def rank_twin_shadow_records(
    records: Sequence[TwinShadowRecord],
) -> tuple[TwinShadowRecord, ...]:
    authority_ranks = _score_ranks(records, "authority")
    point_ranks = _score_ranks(records, "point")
    pessimistic_ranks = _score_ranks(records, "pessimistic")
    return tuple(
        replace(
            record,
            authority_rank=authority_ranks.get(index),
            shadow_point_rank=point_ranks.get(index),
            shadow_pessimistic_rank=pessimistic_ranks.get(index),
        )
        for index, record in enumerate(records)
    )


def build_twin_shadow_batch(
    items: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    base_policy: ContinuousScoringPolicy,
) -> tuple[TwinShadowRecord, ...]:
    records = tuple(
        build_twin_shadow_record(
            item,
            scanner_run_id=scanner_run_id,
            base_policy=base_policy,
        )
        for item in items
    )
    return rank_twin_shadow_records(records)


def annotate_authority_selection(
    records: Sequence[TwinShadowRecord],
    *,
    fine_filtered: Sequence[Mapping[str, object]],
    allocated: Sequence[Mapping[str, object]],
) -> tuple[TwinShadowRecord, ...]:
    """Attach only step-v1 filter/selection facts; no Shadow allocation is run."""

    fine_identities = {_source_identity(item) for item in fine_filtered}
    selected_identities = {_source_identity(item) for item in allocated}
    return tuple(
        replace(
            record,
            fine_filter_passed=(
                record.feature_snapshot_id,
                record.asset_id,
                record.symbol,
                record.market_type,
            )
            in fine_identities,
            authority_selected=(
                record.feature_snapshot_id,
                record.asset_id,
                record.symbol,
                record.market_type,
            )
            in selected_identities,
        )
        for record in records
    )


def build_shadow_allocation_candidates(
    records: Sequence[TwinShadowRecord],
    inputs: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    score_kind: str,
) -> list[dict[str, object]]:
    """Build isolated allocator inputs without mutating authority collections."""

    if score_kind not in {"point", "pessimistic"}:
        raise ValueError("unsupported_shadow_score_kind")
    by_key = {record.candidate_key: record for record in records}
    candidates: list[dict[str, object]] = []
    for item in inputs:
        feature = dict(item.feature)
        record = by_key.get(_candidate_key(scanner_run_id, feature))
        if record is None or not record.shadow.valid:
            continue
        if "authority_no_chase_gate" in record.blocking_reasons:
            continue
        if score_kind == "point":
            family = record.shadow.point_family
            direction = record.shadow.point_direction
            score = record.shadow.point_score
        else:
            family = record.shadow.pessimistic_family
            direction = record.shadow.pessimistic_direction
            score = record.shadow.pessimistic_score
        if not family or direction not in {"long", "short"}:
            continue
        candidates.append(
            {
                **feature,
                "scanner_family": family,
                "scanner_direction": direction,
                "direction": direction,
                "total_score": score,
            }
        )
    return candidates


def annotate_shadow_selection(
    records: Sequence[TwinShadowRecord],
    *,
    point_allocated: Sequence[Mapping[str, object]],
    pessimistic_allocated: Sequence[Mapping[str, object]],
) -> tuple[TwinShadowRecord, ...]:
    point_identities = {_source_identity(item) for item in point_allocated}
    pessimistic_identities = {_source_identity(item) for item in pessimistic_allocated}
    return tuple(
        replace(
            record,
            shadow_point_selected=(
                record.feature_snapshot_id,
                record.asset_id,
                record.symbol,
                record.market_type,
            )
            in point_identities,
            shadow_pessimistic_selected=(
                record.feature_snapshot_id,
                record.asset_id,
                record.symbol,
                record.market_type,
            )
            in pessimistic_identities,
        )
        for record in records
    )


def mark_shadow_allocation_unavailable(
    records: Sequence[TwinShadowRecord],
    reason: str,
) -> tuple[TwinShadowRecord, ...]:
    return tuple(
        replace(
            record,
            blocking_reasons=tuple(sorted({*record.blocking_reasons, reason})),
        )
        for record in records
    )


def summarize_twin_shadow_records(
    records: Sequence[TwinShadowRecord],
) -> TwinShadowSummary:
    return TwinShadowSummary(
        record_version=TWIN_SHADOW_RECORD_VERSION,
        candidate_count=len(records),
        valid_shadow_count=sum(record.shadow.valid for record in records),
        invalid_shadow_count=sum(not record.shadow.valid for record in records),
        family_change_count=sum(record.family_changed for record in records),
        direction_change_count=sum(record.direction_changed for record in records),
        point_threshold_crossing_count=sum(
            comparison.point_crossed for record in records for comparison in record.threshold_comparisons
        ),
        pessimistic_threshold_crossing_count=sum(
            comparison.pessimistic_crossed for record in records for comparison in record.threshold_comparisons
        ),
        budget_exceeded_count=sum("raw_feature_budget_exceeded" in record.shadow.flags for record in records),
        concentrated_evidence_count=sum("evidence_concentrated" in record.shadow.flags for record in records),
        single_source_evidence_count=sum("single_source_evidence" in record.shadow.flags for record in records),
        missingness_count=sum(record.shadow.valid and record.shadow.missingness_delta > 1e-12 for record in records),
        interaction_residual_unavailable_count=sum(
            "interaction_residual_unavailable" in record.shadow.flags for record in records
        ),
        authority_selected_count=sum(record.authority_selected is True for record in records),
        shadow_point_selected_count=sum(record.shadow_point_selected is True for record in records),
        shadow_pessimistic_selected_count=sum(record.shadow_pessimistic_selected is True for record in records),
        point_selection_change_count=sum(record.point_selection_changed is True for record in records),
        pessimistic_selection_change_count=sum(record.pessimistic_selection_changed is True for record in records),
    )


__all__ = [
    "TWIN_SHADOW_RECORD_VERSION",
    "ScoreThresholdComparison",
    "TwinShadowInput",
    "TwinShadowRecord",
    "TwinShadowSummary",
    "annotate_authority_selection",
    "annotate_shadow_selection",
    "build_shadow_allocation_candidates",
    "build_twin_shadow_batch",
    "build_twin_shadow_record",
    "rank_twin_shadow_records",
    "mark_shadow_allocation_unavailable",
    "summarize_twin_shadow_records",
]
