"""Pure statistical estimators for ``profit_gradient/v3``."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import math
import random
from typing import Any, Iterable, Mapping, Sequence


_RANDOMIZED_ARMS = ("control", "treatment")
_MINIMUM_RANDOMIZED_SMALLER_ARM = 20


@dataclass(frozen=True, slots=True)
class _EstimatorResult:
    paired_net_delta_bp: float | None
    lower_95_bp: float | None
    upper_95_bp: float | None
    quote_contrast: float | None
    harm_quote: float | None
    top1_absolute_contribution_share: float | None
    top5_absolute_contribution_share: float | None
    temporal_stable: bool
    independent_unit_count: int
    cluster_count_by_arm: tuple[tuple[str, int], ...] | None
    arm_counts: tuple[tuple[str, int], ...]
    unassigned_arm_count: int | None
    smaller_arm_count: int | None
    method_reasons: tuple[str, ...]


def _mean(values: Sequence[float]) -> float:
    return sum(sorted(values)) / len(values)


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * probability
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _seed(material: str) -> int:
    return int(hashlib.sha256(material.encode()).hexdigest()[:16], 16)


def _arm_counts(samples: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int], ...]:
    arms: dict[str, int] = defaultdict(int)
    for sample in samples:
        arm_counts = sample.get("arm_counts")
        if isinstance(arm_counts, Mapping):
            for arm, count in arm_counts.items():
                arms[str(arm)] += int(count)
        elif sample["arm"]:
            arms[str(sample["arm"])] += 1
    return tuple(sorted(arms.items()))


def _concentration_values(values: Iterable[float]) -> tuple[float | None, float | None]:
    contributions = sorted((abs(value) for value in values), reverse=True)
    total = sum(contributions)
    if not math.isfinite(total):
        return math.nan, math.nan
    if total <= 0:
        return None, None
    return contributions[0] / total, sum(contributions[:5]) / total


def _same_sign_halves(
    samples: Sequence[Mapping[str, Any]], *, expected_effect: float | None
) -> tuple[bool, bool]:
    ordered = sorted(samples, key=lambda sample: (sample["occurred_at"], sample["unit_id"]))
    midpoint = len(ordered) // 2
    if midpoint == 0 or midpoint == len(ordered):
        return False, False
    if ordered[midpoint - 1]["occurred_at"] == ordered[midpoint]["occurred_at"]:
        return False, False
    means = [
        _mean([sample["paired_net_delta_bp"] for sample in half])
        for half in (ordered[:midpoint], ordered[midpoint:])
    ]
    non_finite = any(not math.isfinite(mean) for mean in means)
    return (
        not non_finite
        and means[0] != 0
        and means[1] != 0
        and (means[0] > 0) == (means[1] > 0)
        and (
            expected_effect is None
            or expected_effect == 0
            or all((mean > 0) == (expected_effect > 0) for mean in means)
        ),
        non_finite,
    )


def _cluster_interval(
    samples: Sequence[Mapping[str, Any]], *, iterations: int
) -> tuple[float | None, float | None, float | None, bool]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        clusters[str(sample["cluster_id"])].append(sample["paired_net_delta_bp"])
    if not clusters:
        return None, None, None, False
    cluster_values = tuple(sorted(_mean(values) for values in clusters.values()))
    estimate = _mean(cluster_values)
    if not math.isfinite(estimate):
        return estimate, None, None, True
    if len(cluster_values) < 2:
        return estimate, None, None, False
    seed_material = ",".join(f"{value:.12g}" for value in cluster_values)
    rng = random.Random(_seed(seed_material))
    bootstraps = [
        _mean([rng.choice(cluster_values) for _ in cluster_values])
        for _ in range(iterations)
    ]
    lower = _percentile(bootstraps, 0.025)
    upper = _percentile(bootstraps, 0.975)
    return estimate, lower, upper, not all(math.isfinite(value) for value in (lower, upper))


def _paired_interval(
    samples: Sequence[Mapping[str, Any]], *, iterations: int
) -> tuple[float | None, float | None, float | None, bool]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        clusters[str(sample["cluster_id"])].append(sample["paired_net_delta_bp"])
    if not clusters:
        return None, None, None, False
    cluster_observations = sorted(tuple(sorted(values)) for values in clusters.values())
    estimate = _mean([value for values in cluster_observations for value in values])
    if not math.isfinite(estimate):
        return estimate, None, None, True
    if len(cluster_observations) < 2:
        return estimate, None, None, False
    seed_material = "|".join(
        ",".join(f"{value:.12g}" for value in values)
        for values in cluster_observations
    )
    rng = random.Random(_seed(seed_material))
    bootstraps = []
    for _ in range(iterations):
        selected = [rng.choice(cluster_observations) for _ in cluster_observations]
        bootstraps.append(_mean([value for cluster in selected for value in cluster]))
    lower = _percentile(bootstraps, 0.025)
    upper = _percentile(bootstraps, 0.975)
    return estimate, lower, upper, not all(math.isfinite(value) for value in (lower, upper))


def _randomized_cluster_arm_conflicts(samples: Sequence[Mapping[str, Any]]) -> set[str]:
    arms_by_cluster: dict[str, set[str]] = defaultdict(set)
    for sample in samples:
        if sample["arm"] in _RANDOMIZED_ARMS:
            arms_by_cluster[str(sample["cluster_id"])].add(str(sample["arm"]))
    return {
        cluster_id for cluster_id, arms in arms_by_cluster.items() if len(arms) > 1
    }


def _randomized_cluster_rows(
    samples: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], set[str], bool]:
    by_cluster: dict[str, dict[str, list[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for sample in samples:
        if sample["arm"] in _RANDOMIZED_ARMS:
            by_cluster[str(sample["cluster_id"])][str(sample["arm"])].append(sample)

    conflicts = _randomized_cluster_arm_conflicts(samples)
    arm_clusters: dict[str, list[dict[str, Any]]] = {
        "control": [],
        "treatment": [],
    }
    non_finite = False
    for cluster_id, arms in sorted(by_cluster.items()):
        if cluster_id in conflicts:
            continue
        for arm in _RANDOMIZED_ARMS:
            rows = arms.get(arm, [])
            if not rows:
                continue
            aggregate = {
                "cluster_id": cluster_id,
                "arm": arm,
                "exposure_count": len(rows),
                "occurred_at": _mean([row["occurred_at"] for row in rows]),
                "paired_net_delta_bp": _mean(
                    [row["paired_net_delta_bp"] for row in rows]
                ),
                "paired_net_delta_quote": _mean(
                    [row["paired_net_delta_quote"] for row in rows]
                ),
            }
            non_finite = non_finite or any(
                not math.isfinite(aggregate[field])
                for field in (
                    "occurred_at",
                    "paired_net_delta_bp",
                    "paired_net_delta_quote",
                )
            )
            arm_clusters[arm].append(aggregate)
    return arm_clusters, conflicts, non_finite


def _randomized_bp_metrics(
    arm_clusters: Mapping[str, list[dict[str, Any]]], *, iterations: int
) -> tuple[float | None, float | None, float | None, bool, bool]:
    treatment = sorted(
        arm_clusters["treatment"], key=lambda row: row["paired_net_delta_bp"]
    )
    control = sorted(
        arm_clusters["control"], key=lambda row: row["paired_net_delta_bp"]
    )
    if not treatment or not control:
        return None, None, None, False, False

    treatment_mean = _mean([row["paired_net_delta_bp"] for row in treatment])
    control_mean = _mean([row["paired_net_delta_bp"] for row in control])
    estimate = treatment_mean - control_mean
    if not all(math.isfinite(value) for value in (treatment_mean, control_mean, estimate)):
        return estimate, None, None, False, True
    if min(len(treatment), len(control)) < 2:
        return estimate, None, None, False, False

    canonical_observations = {
        arm: tuple(sorted(row["paired_net_delta_bp"] for row in arm_clusters[arm]))
        for arm in _RANDOMIZED_ARMS
    }
    seed_material = "|".join(
        f"{arm}:{','.join(f'{value:.12g}' for value in canonical_observations[arm])}"
        for arm in _RANDOMIZED_ARMS
    )
    rng = random.Random(_seed(seed_material))
    bootstraps = []
    for _ in range(iterations):
        treatment_selected = [
            rng.choice(canonical_observations["treatment"]) for _ in treatment
        ]
        control_selected = [
            rng.choice(canonical_observations["control"]) for _ in control
        ]
        bootstraps.append(_mean(treatment_selected) - _mean(control_selected))
    lower = _percentile(bootstraps, 0.025)
    upper = _percentile(bootstraps, 0.975)

    ordered = sorted(
        treatment + control,
        key=lambda row: (row["occurred_at"], row["cluster_id"]),
    )
    midpoint = len(ordered) // 2
    temporal_effects = []
    if midpoint and ordered[midpoint - 1]["occurred_at"] != ordered[midpoint]["occurred_at"]:
        for half in (ordered[:midpoint], ordered[midpoint:]):
            treatment_values = [
                row["paired_net_delta_bp"]
                for row in half
                if row["arm"] == "treatment"
            ]
            control_values = [
                row["paired_net_delta_bp"]
                for row in half
                if row["arm"] == "control"
            ]
            if not treatment_values or not control_values:
                temporal_effects = []
                break
            temporal_effects.append(_mean(treatment_values) - _mean(control_values))
    temporal_stable = (
        len(temporal_effects) == 2
        and all(math.isfinite(effect) and effect != 0 for effect in temporal_effects)
        and (temporal_effects[0] > 0) == (temporal_effects[1] > 0)
        and all((effect > 0) == (estimate > 0) for effect in temporal_effects)
    )
    non_finite = not all(
        math.isfinite(value) for value in (lower, upper, *temporal_effects)
    )
    return estimate, lower, upper, temporal_stable, non_finite


def _randomized_quote_metrics(
    arm_clusters: Mapping[str, list[dict[str, Any]]],
) -> tuple[float | None, float | None, float | None, float | None, bool]:
    treatment = arm_clusters["treatment"]
    control = arm_clusters["control"]
    if not treatment or not control:
        return None, None, None, None, False
    treatment_exposure_count = sum(row["exposure_count"] for row in treatment)
    treatment_quote_total = sum(
        sorted(
            row["paired_net_delta_quote"] * row["exposure_count"]
            for row in treatment
        )
    )
    treatment_mean = treatment_quote_total / treatment_exposure_count
    control_exposure_count = sum(row["exposure_count"] for row in control)
    control_quote_total = sum(
        sorted(
            row["paired_net_delta_quote"] * row["exposure_count"]
            for row in control
        )
    )
    control_mean = control_quote_total / control_exposure_count
    harm_quote = treatment_quote_total - control_mean * treatment_exposure_count
    center = (treatment_mean + control_mean) / 2.0
    contributions = [
        (row["paired_net_delta_quote"] - center) * row["exposure_count"]
        for row in treatment
    ] + [
        -(row["paired_net_delta_quote"] - center)
        * treatment_exposure_count
        * row["exposure_count"]
        / control_exposure_count
        for row in control
    ]
    quote_contrast = treatment_mean - control_mean
    top1, top5 = _concentration_values(contributions)
    non_finite = any(
        value is not None and not math.isfinite(value)
        for value in (quote_contrast, harm_quote, top1, top5)
    )
    return quote_contrast, harm_quote, top1, top5, non_finite


def _result(
    *,
    effect: float | None,
    lower: float | None,
    upper: float | None,
    quote_contrast: float | None,
    harm_quote: float | None,
    top1: float | None,
    top5: float | None,
    temporal_stable: bool,
    independent_unit_count: int,
    cluster_count_by_arm: tuple[tuple[str, int], ...] | None,
    arm_counts: tuple[tuple[str, int], ...],
    unassigned_arm_count: int | None,
    smaller_arm_count: int | None,
    method_reasons: Iterable[str] = (),
    non_finite: bool = False,
) -> _EstimatorResult:
    values = (effect, lower, upper, quote_contrast, harm_quote, top1, top5)
    non_finite = non_finite or any(
        value is not None and not math.isfinite(value) for value in values
    )
    sanitized = tuple(
        value if value is None or math.isfinite(value) else None for value in values
    )
    reasons = set(method_reasons)
    if non_finite:
        reasons.add("non_finite_aggregate_metric")
    return _EstimatorResult(
        paired_net_delta_bp=sanitized[0],
        lower_95_bp=sanitized[1],
        upper_95_bp=sanitized[2],
        quote_contrast=sanitized[3],
        harm_quote=sanitized[4],
        top1_absolute_contribution_share=sanitized[5],
        top5_absolute_contribution_share=sanitized[6],
        temporal_stable=temporal_stable,
        independent_unit_count=independent_unit_count,
        cluster_count_by_arm=cluster_count_by_arm,
        arm_counts=arm_counts,
        unassigned_arm_count=unassigned_arm_count,
        smaller_arm_count=smaller_arm_count,
        method_reasons=tuple(sorted(reasons)),
    )


def estimate_prospective_randomized(
    samples: Sequence[Mapping[str, Any]], *, iterations: int = 2_000
) -> _EstimatorResult:
    """Estimate the equal-weight per-cluster treatment minus control effect."""
    arms = _arm_counts(samples)
    arm_clusters, conflicts, aggregate_non_finite = _randomized_cluster_rows(samples)
    cluster_count_by_arm = tuple(
        (arm, len(arm_clusters[arm])) for arm in _RANDOMIZED_ARMS
    )
    cluster_counts = dict(cluster_count_by_arm)
    independent_unit_count = sum(cluster_counts.values())
    smaller_arm_count: int | None = min(cluster_counts.values())
    if not any(sample["arm"] in _RANDOMIZED_ARMS for sample in samples):
        pair_level_clusters = {
            sample["cluster_id"]
            for sample in samples
            if isinstance(sample.get("arm_counts"), Mapping)
        }
        independent_unit_count = len(pair_level_clusters)
        reported_arms = dict(arms)
        smaller_arm_count = (
            min(reported_arms.get("control", 0), reported_arms.get("treatment", 0))
            or None
        )

    effect, lower, upper, temporal_stable, bp_non_finite = _randomized_bp_metrics(
        arm_clusters, iterations=iterations
    )
    quote_contrast, harm_quote, top1, top5, quote_non_finite = (
        _randomized_quote_metrics(arm_clusters)
    )
    unassigned_arm_count = sum(
        sample["arm"] not in _RANDOMIZED_ARMS for sample in samples
    )
    reasons = []
    reported_arms = dict(arms)
    if set(reported_arms) != set(_RANDOMIZED_ARMS):
        reasons.append("randomized_arm_labels_required")
    if conflicts:
        reasons.append("randomized_cluster_multiple_arms")
    if len(reported_arms) != 2:
        reasons.append("randomized_two_arms_required")
    if unassigned_arm_count:
        reasons.append("randomized_arm_missing")
    if smaller_arm_count is None or smaller_arm_count < _MINIMUM_RANDOMIZED_SMALLER_ARM:
        reasons.append("smaller_arm_below_20")
    if quote_contrast is None or not math.isfinite(quote_contrast) or quote_contrast <= 0:
        reasons.append("randomized_quote_contrast_not_positive")
    if harm_quote is None or not math.isfinite(harm_quote) or harm_quote <= 0:
        reasons.append("randomized_quote_harm_not_positive")
    return _result(
        effect=effect,
        lower=lower,
        upper=upper,
        quote_contrast=quote_contrast,
        harm_quote=harm_quote,
        top1=top1,
        top5=top5,
        temporal_stable=temporal_stable,
        independent_unit_count=independent_unit_count,
        cluster_count_by_arm=cluster_count_by_arm,
        arm_counts=arms,
        unassigned_arm_count=unassigned_arm_count,
        smaller_arm_count=smaller_arm_count,
        method_reasons=reasons,
        non_finite=aggregate_non_finite or bp_non_finite or quote_non_finite,
    )


def estimate_paired_same_unit(
    samples: Sequence[Mapping[str, Any]], *, iterations: int = 2_000
) -> _EstimatorResult:
    """Estimate the unit-weighted paired effect with a cluster bootstrap."""
    effect, lower, upper, interval_non_finite = _paired_interval(
        samples, iterations=iterations
    )
    harm_quote = sum(sorted(sample["paired_net_delta_quote"] for sample in samples))
    top1, top5 = _concentration_values(
        sample["paired_net_delta_quote"] for sample in samples
    )
    temporal_stable, temporal_non_finite = _same_sign_halves(
        samples, expected_effect=effect
    )
    reasons = []
    if not math.isfinite(harm_quote) or harm_quote <= 0:
        reasons.append("paired_quote_harm_not_positive")
    return _result(
        effect=effect,
        lower=lower,
        upper=upper,
        quote_contrast=None,
        harm_quote=harm_quote,
        top1=top1,
        top5=top5,
        temporal_stable=temporal_stable,
        independent_unit_count=len({sample["cluster_id"] for sample in samples}),
        cluster_count_by_arm=None,
        arm_counts=_arm_counts(samples),
        unassigned_arm_count=None,
        smaller_arm_count=None,
        method_reasons=reasons,
        non_finite=interval_non_finite or temporal_non_finite,
    )


def estimate_deterministic_decomposition(
    samples: Sequence[Mapping[str, Any]], *, iterations: int = 2_000
) -> _EstimatorResult:
    """Estimate a descriptive equal-weight cluster-mean effect."""
    effect, lower, upper, interval_non_finite = _cluster_interval(
        samples, iterations=iterations
    )
    harm_quote = sum(sorted(sample["paired_net_delta_quote"] for sample in samples))
    top1, top5 = _concentration_values(
        sample["paired_net_delta_quote"] for sample in samples
    )
    temporal_stable, temporal_non_finite = _same_sign_halves(
        samples, expected_effect=effect
    )
    return _result(
        effect=effect,
        lower=lower,
        upper=upper,
        quote_contrast=None,
        harm_quote=harm_quote,
        top1=top1,
        top5=top5,
        temporal_stable=temporal_stable,
        independent_unit_count=len({sample["cluster_id"] for sample in samples}),
        cluster_count_by_arm=None,
        arm_counts=_arm_counts(samples),
        unassigned_arm_count=None,
        smaller_arm_count=None,
        non_finite=interval_non_finite or temporal_non_finite,
    )


__all__ = [
    "estimate_deterministic_decomposition",
    "estimate_paired_same_unit",
    "estimate_prospective_randomized",
]
