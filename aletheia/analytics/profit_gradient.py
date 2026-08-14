"""Seven-layer profit attribution with explicit comparison-method gates.

This module intentionally accepts structured cohort samples only.  It does not
import or consume Position Lifecycle Story text; narrative rendering remains a
human-review surface, not a Learning/statistical input.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import math
import random
from typing import Any, Iterable, Mapping


PROFIT_GRADIENT_CONTRACT_VERSION = "profit_gradient/v3"
LAYERS = ("Selection", "Decision", "Risk", "Execution", "Holding", "Exit", "Costs")
COMPARISON_METHODS = (
    "prospective_randomized",
    "paired_same_unit",
    "deterministic_decomposition",
    "directional_observational",
)
_CANDIDATE_LAYERS = frozenset({"Selection", "Decision", "Risk"})
_COMPARISON_PROFILES = {
    "prospective_randomized": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": 20,
        "requires_two_arms": True,
        "allows_unique_winner": True,
        "evidence_role": "prospective_confirmatory",
        "estimand": "treatment_minus_control_cluster_mean_bp",
    },
    "paired_same_unit": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": True,
        "evidence_role": "paired_confirmatory",
        "estimand": "paired_same_unit_mean_bp",
    },
    "deterministic_decomposition": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "measured_only",
        "estimand": "deterministic_decomposition_bp",
    },
    "directional_observational": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "directional_only",
        "estimand": "directional_observational_bp",
    },
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_integer(value: Any) -> int | None:
    number = _finite(value)
    if number is None or number <= 0 or not number.is_integer():
        return None
    return int(number)


def _timestamp(value: Any) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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


def _cluster_interval(
    samples: list[dict[str, Any]], *, iterations: int = 2_000
) -> tuple[float | None, float | None, float | None]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        value = _finite(sample.get("paired_net_delta_bp"))
        cluster = sample.get("cluster_id")
        if value is None or not cluster:
            continue
        clusters[str(cluster)].append(value)
    if not clusters:
        return None, None, None
    cluster_values = [sum(values) / len(values) for values in clusters.values()]
    estimate = sum(cluster_values) / len(cluster_values)
    if len(cluster_values) < 2:
        return estimate, None, None
    seed_material = "|".join(
        f"{key}:{','.join(f'{value:.12g}' for value in sorted(values))}"
        for key, values in sorted(clusters.items())
    )
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    bootstraps = []
    for _ in range(iterations):
        selected = [rng.choice(cluster_values) for _ in cluster_values]
        bootstraps.append(sum(selected) / len(selected))
    return estimate, _percentile(bootstraps, 0.025), _percentile(bootstraps, 0.975)


def _paired_unit_interval(
    samples: list[dict[str, Any]], *, iterations: int = 2_000
) -> tuple[float | None, float | None, float | None]:
    clusters: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        value = _finite(sample.get("paired_net_delta_bp"))
        cluster = sample.get("cluster_id")
        if value is not None and cluster:
            clusters[str(cluster)].append(value)
    if not clusters:
        return None, None, None
    all_values = [value for values in clusters.values() for value in values]
    estimate = sum(all_values) / len(all_values)
    if len(clusters) < 2:
        return estimate, None, None
    seed_material = "|".join(
        f"{key}:{','.join(f'{value:.12g}' for value in sorted(values))}"
        for key, values in sorted(clusters.items())
    )
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    cluster_keys = sorted(clusters)
    bootstraps = []
    for _ in range(iterations):
        selected = [rng.choice(cluster_keys) for _ in cluster_keys]
        values = [value for key in selected for value in clusters[key]]
        bootstraps.append(sum(values) / len(values))
    return estimate, _percentile(bootstraps, 0.025), _percentile(bootstraps, 0.975)


def _randomized_cluster_arm_conflicts(
    samples: list[dict[str, Any]],
) -> set[str]:
    arms_by_cluster: dict[str, set[str]] = defaultdict(set)
    for sample in samples:
        if sample["arm"] in {"control", "treatment"}:
            arms_by_cluster[sample["cluster_id"]].add(sample["arm"])
    return {
        cluster_id for cluster_id, arms in arms_by_cluster.items() if len(arms) > 1
    }


def _randomized_cluster_rows(
    samples: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_cluster: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for sample in samples:
        if sample["arm"] in {"control", "treatment"}:
            by_cluster[sample["cluster_id"]][sample["arm"]].append(sample)

    conflicting_clusters = _randomized_cluster_arm_conflicts(samples)
    arm_clusters: dict[str, list[dict[str, Any]]] = {"control": [], "treatment": []}
    for cluster_id, arms in sorted(by_cluster.items()):
        if cluster_id in conflicting_clusters:
            continue
        for arm in arm_clusters:
            rows = arms.get(arm, [])
            if not rows:
                continue
            arm_clusters[arm].append(
                {
                    "cluster_id": str(cluster_id),
                    "arm": arm,
                    "exposure_count": len(rows),
                    "occurred_at": sum(row["occurred_at"] for row in rows) / len(rows),
                    "paired_net_delta_bp": sum(row["paired_net_delta_bp"] for row in rows) / len(rows),
                    "paired_net_delta_quote": sum(row["paired_net_delta_quote"] for row in rows) / len(rows),
                }
            )
    return arm_clusters


def _randomized_cluster_interval(
    samples: list[dict[str, Any]], *, iterations: int = 2_000
) -> tuple[float | None, float | None, float | None, dict[str, int], bool]:
    arm_clusters = _randomized_cluster_rows(samples)

    cluster_counts = {arm: len(rows) for arm, rows in arm_clusters.items()}
    treatment = arm_clusters["treatment"]
    control = arm_clusters["control"]
    if not treatment or not control:
        return None, None, None, cluster_counts, False

    treatment_mean = sum(row["paired_net_delta_bp"] for row in treatment) / len(treatment)
    control_mean = sum(row["paired_net_delta_bp"] for row in control) / len(control)
    estimate = treatment_mean - control_mean
    if min(cluster_counts.values()) < 2:
        return estimate, None, None, cluster_counts, False

    seed_material = "|".join(
        f"{arm}:{row['cluster_id']}:{row['paired_net_delta_bp']:.12g}"
        for arm in ("control", "treatment")
        for row in sorted(arm_clusters[arm], key=lambda item: item["cluster_id"])
    )
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    bootstraps = []
    for _ in range(iterations):
        treatment_selected = [rng.choice(treatment)["paired_net_delta_bp"] for _ in treatment]
        control_selected = [rng.choice(control)["paired_net_delta_bp"] for _ in control]
        bootstraps.append(
            sum(treatment_selected) / len(treatment_selected)
            - sum(control_selected) / len(control_selected)
        )

    ordered = sorted(
        treatment + control,
        key=lambda row: (row["occurred_at"], row["cluster_id"]),
    )
    midpoint = len(ordered) // 2
    temporal_effects = []
    if midpoint and ordered[midpoint - 1]["occurred_at"] != ordered[midpoint]["occurred_at"]:
        for half in (ordered[:midpoint], ordered[midpoint:]):
            treatment_values = [
                row["paired_net_delta_bp"] for row in half if row["arm"] == "treatment"
            ]
            control_values = [
                row["paired_net_delta_bp"] for row in half if row["arm"] == "control"
            ]
            if not treatment_values or not control_values:
                temporal_effects = []
                break
            temporal_effects.append(
                sum(treatment_values) / len(treatment_values)
                - sum(control_values) / len(control_values)
            )
    temporal_stable = (
        len(temporal_effects) == 2
        and all(effect != 0 for effect in temporal_effects)
        and (temporal_effects[0] > 0) == (temporal_effects[1] > 0)
        and all((effect > 0) == (estimate > 0) for effect in temporal_effects)
    )
    return estimate, _percentile(bootstraps, 0.025), _percentile(bootstraps, 0.975), cluster_counts, temporal_stable


def _randomized_quote_metrics(
    samples: list[dict[str, Any]],
) -> tuple[float | None, float | None, float | None, float | None]:
    arm_clusters = _randomized_cluster_rows(samples)
    treatment = arm_clusters["treatment"]
    control = arm_clusters["control"]
    if not treatment or not control:
        return None, None, None, None
    treatment_mean = sum(row["paired_net_delta_quote"] for row in treatment) / len(treatment)
    control_mean = sum(row["paired_net_delta_quote"] for row in control) / len(control)
    treatment_exposure_count = sum(row["exposure_count"] for row in treatment)
    treatment_quote_total = sum(
        row["paired_net_delta_quote"] * row["exposure_count"] for row in treatment
    )
    total_quote = treatment_quote_total - control_mean * treatment_exposure_count
    center = (treatment_mean + control_mean) / 2.0
    treatment_contributions = [
        (row["paired_net_delta_quote"] - center) / len(treatment)
        for row in treatment
    ]
    control_contributions = [
        -(row["paired_net_delta_quote"] - center) / len(control)
        for row in control
    ]
    contributions = treatment_contributions + control_contributions
    quote_contrast = treatment_mean - control_mean
    top1, top5 = _concentration_values(contributions)
    return quote_contrast, total_quote, top1, top5


def _expected_units(layer: str) -> tuple[str, str]:
    if layer in _CANDIDATE_LAYERS:
        return "candidate_fact", "fixed_horizon_net_return"
    return "position_fact", "verified_settled_net_economics"


_RANDOMIZED_COHORT_FIELDS = (
    ("assignment_salts", "assignment_salt"),
    ("horizons", "horizon"),
    ("outcome_contract_versions", "outcome_contract_version"),
    ("metric_versions", "metric_version"),
    ("statistical_unit_versions", "statistical_unit_version"),
    ("stages", "stage"),
)


def _randomized_cohort_identity(
    context: Mapping[str, Any], method: str | None
) -> tuple[dict[str, str] | None, list[str]]:
    if method == "prospective_randomized":
        required_fields = _RANDOMIZED_COHORT_FIELDS
    elif method == "paired_same_unit":
        required_fields = tuple(
            item for item in _RANDOMIZED_COHORT_FIELDS if item[0] != "assignment_salts"
        )
    else:
        return None, []
    identity: dict[str, str] = {}
    reasons = []
    for field, output_field in required_fields:
        values = context.get(field)
        valid_strings = (
            isinstance(values, list)
            and bool(values)
            and all(isinstance(value, str) and value.strip() for value in values)
        )
        normalized = [value.strip() for value in values] if valid_strings else []
        if not valid_strings or len(set(normalized)) != 1:
            reasons.append(f"mixed_or_missing_{field}")
            continue
        identity[output_field] = normalized[0]
    return (identity if not reasons else None), reasons


def _comparison_method(context: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    value = context.get("comparison_method")
    if not isinstance(value, str) or not value:
        return None, ["missing_comparison_method"]
    if value not in COMPARISON_METHODS:
        return value, ["unsupported_comparison_method"]
    return value, []


def _thresholds_for(method: str | None) -> dict[str, Any]:
    profile = _COMPARISON_PROFILES.get(method or "")
    if profile is None:
        return {}
    return {
        "minimum_independent_units": profile["minimum_independent_units"],
        "minimum_smaller_arm": profile["minimum_smaller_arm"],
        "requires_two_arms": profile["requires_two_arms"],
        "estimand": profile["estimand"],
        "minimum_risk_rejects": 20,
        "minimum_absolute_effect_bp": 5.0,
        "cluster_interval": "95%_cluster_bootstrap_excluding_zero",
        "temporal_stability": "chronological_halves_same_sign",
        "top1_absolute_contribution_share_max": 0.25,
        "top5_absolute_contribution_share_max": 0.50,
    }


def _normalized_samples(
    layer: str, raw_samples: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], int]:
    fact_unit, economic_unit = _expected_units(layer)
    samples = []
    exclusions = []
    raw_count = 0
    seen_units: set[str] = set()
    for raw in raw_samples:
        raw_count += 1
        if raw.get("fact_unit") != fact_unit or raw.get("economic_unit") != economic_unit:
            exclusions.append("denominator_or_economic_unit_mismatch")
            continue
        unit_id = str(raw.get("unit_id") or "")
        cluster_id = str(raw.get("cluster_id") or "")
        delta_bp = _finite(raw.get("paired_net_delta_bp"))
        delta_quote = _finite(raw.get("paired_net_delta_quote"))
        occurred_at = _timestamp(raw.get("occurred_at"))
        arm_counts = raw.get("arm_counts")
        normalized_arm_counts = None
        if arm_counts is not None:
            if not isinstance(arm_counts, Mapping):
                exclusions.append("incomplete_paired_sample")
                continue
            try:
                normalized_arm_counts = {
                    str(arm): int(float(count))
                    for arm, count in arm_counts.items()
                    if int(float(count)) > 0
                }
            except (TypeError, ValueError, OverflowError):
                exclusions.append("incomplete_paired_sample")
                continue
            if not {"treatment", "control"} <= set(normalized_arm_counts):
                exclusions.append("incomplete_paired_sample")
                continue
        if not unit_id or not cluster_id or delta_bp is None or delta_quote is None or occurred_at is None:
            exclusions.append("incomplete_paired_sample")
            continue
        if (delta_bp == 0.0) != (delta_quote == 0.0):
            exclusions.append("paired_delta_zero_mismatch")
            continue
        if delta_bp * delta_quote < 0.0:
            exclusions.append("paired_delta_sign_mismatch")
            continue
        if unit_id in seen_units:
            exclusions.append("duplicate_assignment_unit")
            continue
        seen_units.add(unit_id)
        samples.append(
            {
                "unit_id": unit_id,
                "cluster_id": cluster_id,
                "paired_net_delta_bp": delta_bp,
                "paired_net_delta_quote": delta_quote,
                "occurred_at": occurred_at,
                "arm": str(raw.get("arm") or ""),
                "arm_counts": normalized_arm_counts,
            }
        )
    return samples, exclusions, raw_count


def _same_sign_halves(
    samples: list[dict[str, Any]], *, expected_effect: float | None = None
) -> bool:
    ordered = sorted(samples, key=lambda sample: (sample["occurred_at"], sample["unit_id"]))
    midpoint = len(ordered) // 2
    if midpoint == 0 or midpoint == len(ordered):
        return False
    if ordered[midpoint - 1]["occurred_at"] == ordered[midpoint]["occurred_at"]:
        return False
    means = [
        sum(sample["paired_net_delta_bp"] for sample in half) / len(half)
        for half in (ordered[:midpoint], ordered[midpoint:])
    ]
    return (
        means[0] != 0
        and means[1] != 0
        and (means[0] > 0) == (means[1] > 0)
        and (
            expected_effect is None
            or expected_effect == 0
            or all((mean > 0) == (expected_effect > 0) for mean in means)
        )
    )


def _concentration_values(values: Iterable[float]) -> tuple[float | None, float | None]:
    contributions = sorted((abs(value) for value in values), reverse=True)
    total = sum(contributions)
    if not math.isfinite(total) or total <= 0:
        return None, None
    top1 = contributions[0] / total
    top5 = sum(contributions[:5]) / total
    if not math.isfinite(top1) or not math.isfinite(top5):
        return None, None
    return top1, top5


def _concentration(samples: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    return _concentration_values(
        sample["paired_net_delta_quote"] for sample in samples
    )


def _epoch_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    for field in (
        "behavior_fingerprints",
        "policy_epochs",
        "market_data_epochs",
        "cost_model_epochs",
    ):
        values = context.get(field)
        if (
            not isinstance(values, list)
            or not values
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            reasons.append(f"mixed_or_missing_{field}")
            continue
        normalized = [value.strip() for value in values]
        if len(set(normalized)) != 1:
            reasons.append(f"mixed_or_missing_{field}")
    return not reasons, reasons


def _global_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    thresholds = (
        ("candidate_outcome_coverage", 0.80),
        ("position_causal_link_completeness", 0.95),
        ("verified_settled_economics_coverage", 0.95),
        ("ordered_path_coverage", 0.80),
    )
    for field, threshold in thresholds:
        value = _finite(context.get(field))
        if value is None or value < threshold:
            reasons.append(f"{field}_below_{threshold:.2f}")
    candidate_count = _finite(context.get("candidate_count"))
    if candidate_count is None or candidate_count <= 0:
        reasons.append("candidate_count_not_positive")
    elif not candidate_count.is_integer():
        reasons.append("candidate_count_not_integral")
    epoch_ok, epoch_reasons = _epoch_gate(context)
    reasons.extend(epoch_reasons)
    return bool(epoch_ok and not reasons), reasons


def _layer_result(
    layer: str,
    raw_samples: Iterable[Mapping[str, Any]],
    *,
    candidate_count: int,
    capital_days: float | None,
    risk_reject_count: int,
    global_ok: bool,
    global_reasons: list[str],
    comparison_method: str | None,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    samples, exclusions, raw_count = _normalized_samples(layer, raw_samples)
    unit_count = len(samples)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    arms = defaultdict(int)
    for sample in samples:
        if isinstance(sample.get("arm_counts"), Mapping):
            for arm, count in sample["arm_counts"].items():
                arms[str(arm)] += int(count)
        elif sample["arm"]:
            arms[sample["arm"]] += 1
    requires_two_arms = bool(profile and profile["requires_two_arms"])
    unassigned_arm_count = (
        sum(sample["arm"] not in {"control", "treatment"} for sample in samples)
        if requires_two_arms
        else None
    )
    pair_level_clusters = {
        sample["cluster_id"]
        for sample in samples
        if isinstance(sample.get("arm_counts"), Mapping)
    }
    if comparison_method == "prospective_randomized":
        estimate, lower, upper, cluster_count_by_arm, temporal_stable = _randomized_cluster_interval(samples)
        quote_contrast, total_quote, top1, top5 = _randomized_quote_metrics(samples)
        independent_unit_count = sum(cluster_count_by_arm.values())
        smaller_arm = (
            min(cluster_count_by_arm.values())
            if set(cluster_count_by_arm) == {"control", "treatment"}
            else None
        )
        if not any(sample["arm"] in {"control", "treatment"} for sample in samples):
            independent_unit_count = len(pair_level_clusters)
            smaller_arm = min(arms.get("control", 0), arms.get("treatment", 0)) or None
    elif comparison_method == "paired_same_unit":
        estimate, lower, upper = _paired_unit_interval(samples)
        cluster_count_by_arm = None
        quote_contrast = None
        total_quote = sum(sample["paired_net_delta_quote"] for sample in samples)
        top1, top5 = _concentration(samples)
        temporal_stable = _same_sign_halves(samples, expected_effect=estimate)
        independent_unit_count = len({sample["cluster_id"] for sample in samples})
        smaller_arm = None
    else:
        estimate, lower, upper = _cluster_interval(samples)
        cluster_count_by_arm = None
        quote_contrast = None
        total_quote = sum(sample["paired_net_delta_quote"] for sample in samples)
        top1, top5 = _concentration(samples)
        temporal_stable = _same_sign_halves(samples, expected_effect=estimate)
        independent_unit_count = len({sample["cluster_id"] for sample in samples})
        smaller_arm = None
    aggregate_values = (
        estimate,
        lower,
        upper,
        quote_contrast,
        total_quote,
        top1,
        top5,
    )
    sanitized_values = tuple(
        _finite(value) if value is not None else None for value in aggregate_values
    )
    non_finite_aggregate = any(
        original is not None and sanitized is None
        for original, sanitized in zip(aggregate_values, sanitized_values)
    )
    estimate, lower, upper, quote_contrast, total_quote, top1, top5 = sanitized_values
    frequency = unit_count / candidate_count if candidate_count > 0 else None
    raw_gradient = frequency * lower if frequency is not None and lower is not None else None
    gradient = _finite(raw_gradient) if raw_gradient is not None else None
    if raw_gradient is not None and gradient is None:
        non_finite_aggregate = True
    reasons = list(global_reasons)
    if non_finite_aggregate:
        reasons.append("non_finite_aggregate_metric")
    minimum_units = int(thresholds.get("minimum_independent_units") or 30)
    minimum_smaller_arm = _finite(thresholds.get("minimum_smaller_arm"))
    if independent_unit_count < minimum_units:
        reasons.append(f"independent_units_below_{minimum_units}")
    if requires_two_arms:
        if set(arms) != {"control", "treatment"}:
            reasons.append("randomized_arm_labels_required")
        if _randomized_cluster_arm_conflicts(samples):
            reasons.append("randomized_cluster_multiple_arms")
        if len(arms) != 2:
            reasons.append("randomized_two_arms_required")
        if unassigned_arm_count:
            reasons.append("randomized_arm_missing")
        if minimum_smaller_arm is None or smaller_arm is None or smaller_arm < minimum_smaller_arm:
            reasons.append("smaller_arm_below_20")
    if layer == "Risk" and risk_reject_count < int(thresholds.get("minimum_risk_rejects") or 20):
        reasons.append("risk_rejects_below_20")
    if estimate is None or abs(estimate) < float(thresholds.get("minimum_absolute_effect_bp") or 5.0):
        reasons.append("absolute_effect_below_5bp")
    if lower is None or upper is None or lower <= 0 <= upper:
        reasons.append("cluster_interval_crosses_zero")
    if estimate is None or estimate <= 0:
        reasons.append("direction_does_not_reduce_harm")
    if comparison_method == "prospective_randomized" and (
        quote_contrast is None or quote_contrast <= 0
    ):
        reasons.append("randomized_quote_contrast_not_positive")
    if comparison_method == "prospective_randomized" and (
        total_quote is None or total_quote <= 0
    ):
        reasons.append("randomized_quote_harm_not_positive")
    if comparison_method == "paired_same_unit" and (
        total_quote is None or total_quote <= 0
    ):
        reasons.append("paired_quote_harm_not_positive")
    if not temporal_stable:
        reasons.append("temporal_halves_disagree")
    if top1 is None or top5 is None:
        reasons.append("concentration_unavailable")
    else:
        if top1 > float(thresholds.get("top1_absolute_contribution_share_max") or 0.25):
            reasons.append("top1_concentration_above_25pct")
        if top5 > float(thresholds.get("top5_absolute_contribution_share_max") or 0.50):
            reasons.append("top5_concentration_above_50pct")
    reasons.extend(exclusions)
    harm_per_100 = (
        total_quote / candidate_count * 100.0
        if total_quote is not None and candidate_count > 0
        else None
    )
    harm_per_day = (
        total_quote / capital_days
        if total_quote is not None and capital_days is not None and capital_days > 0
        else None
    )
    raw_normalized_metrics = (harm_per_100, harm_per_day)
    normalized_metrics = tuple(
        _finite(value) if value is not None else None for value in raw_normalized_metrics
    )
    if any(
        original is not None and normalized is None
        for original, normalized in zip(raw_normalized_metrics, normalized_metrics)
    ):
        non_finite_aggregate = True
        reasons.append("non_finite_aggregate_metric")
    harm_per_100, harm_per_day = normalized_metrics
    eligible = bool(global_ok and not reasons)
    fact_unit, economic_unit = _expected_units(layer)
    return {
        "layer": layer,
        "fact_unit": fact_unit,
        "economic_unit": economic_unit,
        "raw_sample_count": raw_count,
        "eligible_sample_count": unit_count,
        "excluded_sample_count": raw_count - unit_count,
        "exclusion_waterfall": dict(sorted(Counter(exclusions).items())),
        "independent_unit_count": independent_unit_count,
        "cluster_count": len({sample["cluster_id"] for sample in samples}),
        "cluster_count_by_arm": cluster_count_by_arm,
        "comparison_method": comparison_method,
        "estimand": profile["estimand"] if profile else None,
        "evidence_role": profile["evidence_role"] if profile else None,
        "thresholds": dict(thresholds),
        "arm_counts": dict(sorted(arms.items())),
        "unassigned_arm_count": unassigned_arm_count,
        "smaller_arm_count": smaller_arm,
        "eligible_frequency": frequency,
        "paired_net_delta_bp": estimate,
        "lower_95_bp": lower,
        "upper_95_bp": upper,
        "gradient_lower_bp": gradient,
        "harm_quote": total_quote,
        "harm_quote_per_100_candidates": harm_per_100,
        "harm_quote_per_capital_day": harm_per_day,
        "top1_absolute_contribution_share": top1,
        "top5_absolute_contribution_share": top5,
        "gate_status": "eligible" if eligible else "ineligible",
        "gate_reasons": sorted(set(reasons)),
    }


def select_unique_max_direction(
    layer_samples: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    evidence_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a winner only when the declared method permits one."""
    global_ok, global_reasons = _global_gate(evidence_context)
    comparison_method, method_reasons = _comparison_method(evidence_context)
    cohort_identity, identity_reasons = _randomized_cohort_identity(
        evidence_context, comparison_method
    )
    global_reasons.extend(method_reasons)
    global_reasons.extend(identity_reasons)
    global_ok = bool(global_ok and not method_reasons and not identity_reasons)
    thresholds = _thresholds_for(comparison_method)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    unique_winner_eligible = bool(profile and profile["allows_unique_winner"])
    candidate_count = _positive_integer(evidence_context.get("candidate_count")) or 0
    capital_days = _finite(evidence_context.get("capital_days"))
    risk_reject_count = int(_finite(evidence_context.get("risk_reject_count")) or 0)
    layers = {
        layer: _layer_result(
            layer,
            layer_samples.get(layer, ()),
            candidate_count=candidate_count,
            capital_days=capital_days,
            risk_reject_count=risk_reject_count,
            global_ok=global_ok,
            global_reasons=global_reasons,
            comparison_method=comparison_method,
            thresholds=thresholds,
        )
        for layer in LAYERS
    }
    eligible = [
        result
        for result in layers.values()
        if result["gate_status"] == "eligible" and unique_winner_eligible
    ]
    eligible.sort(key=lambda result: result["gradient_lower_bp"], reverse=True)
    winner = eligible[0] if len(eligible) == 1 else None
    runner_upper = max(
        (
            result["upper_95_bp"]
            for result in layers.values()
            if result is not winner and result["upper_95_bp"] is not None
        ),
        default=0.0,
    )
    separated = bool(winner is not None and winner["lower_95_bp"] is not None and winner["lower_95_bp"] > runner_upper)
    winner_layer = winner["layer"] if separated and winner is not None else None
    verdict = "unique_max_direction" if separated else "no_unique_direction"
    next_action = (
        "register_one_single_variable_forward_experiment"
        if separated
        else "rank_next_question_only"
        if comparison_method == "directional_observational"
        else "retain_measured_fact_no_behavior_promotion"
        if comparison_method == "deterministic_decomposition"
        else "close_largest_evidence_gap_only"
    )
    return {
        "contract_version": PROFIT_GRADIENT_CONTRACT_VERSION,
        "comparison_method": comparison_method,
        "estimand": profile["estimand"] if profile else None,
        "evidence_role": profile["evidence_role"] if profile else None,
        "cohort_identity": cohort_identity,
        "thresholds": thresholds,
        "unique_winner_eligible": unique_winner_eligible,
        "verdict": verdict,
        "winner_layer": winner_layer,
        "runner_up_harm_upper_bp": runner_upper,
        "global_gate_status": "eligible" if global_ok else "ineligible",
        "global_gate_reasons": global_reasons,
        "layers": layers,
        "next_action": next_action,
        "automatic_behavior_change_authorized": False,
    }
