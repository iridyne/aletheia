"""Seven-layer profit attribution with explicit comparison-method gates.

This module intentionally accepts structured cohort samples only.  It does not
import or consume Position Lifecycle Story text; narrative rendering remains a
human-review surface, not a Learning/statistical input.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from aletheia.analytics.profit_gradient_estimators import (
    estimate_deterministic_decomposition,
    estimate_paired_same_unit,
    estimate_prospective_randomized,
)


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
    elif method in {
        "paired_same_unit",
        "deterministic_decomposition",
        "directional_observational",
    }:
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
    estimator = {
        "prospective_randomized": estimate_prospective_randomized,
        "paired_same_unit": estimate_paired_same_unit,
        "deterministic_decomposition": estimate_deterministic_decomposition,
        "directional_observational": estimate_deterministic_decomposition,
    }.get(comparison_method, estimate_deterministic_decomposition)
    estimate_result = estimator(samples)
    estimate = estimate_result.paired_net_delta_bp
    lower = estimate_result.lower_95_bp
    upper = estimate_result.upper_95_bp
    total_quote = estimate_result.harm_quote
    top1 = estimate_result.top1_absolute_contribution_share
    top5 = estimate_result.top5_absolute_contribution_share
    independent_unit_count = estimate_result.independent_unit_count
    temporal_stable = estimate_result.temporal_stable
    frequency = unit_count / candidate_count if candidate_count > 0 else None
    raw_gradient = frequency * lower if frequency is not None and lower is not None else None
    gradient = _finite(raw_gradient) if raw_gradient is not None else None
    reasons = list(global_reasons)
    reasons.extend(estimate_result.method_reasons)
    if raw_gradient is not None and gradient is None:
        reasons.append("non_finite_aggregate_metric")
    minimum_units = int(thresholds.get("minimum_independent_units") or 30)
    if independent_unit_count < minimum_units:
        reasons.append(f"independent_units_below_{minimum_units}")
    if layer == "Risk" and risk_reject_count < int(thresholds.get("minimum_risk_rejects") or 20):
        reasons.append("risk_rejects_below_20")
    if estimate is None or abs(estimate) < float(thresholds.get("minimum_absolute_effect_bp") or 5.0):
        reasons.append("absolute_effect_below_5bp")
    if lower is None or upper is None or lower <= 0 <= upper:
        reasons.append("cluster_interval_crosses_zero")
    if estimate is None or estimate <= 0:
        reasons.append("direction_does_not_reduce_harm")
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
        "cluster_count_by_arm": (
            dict(estimate_result.cluster_count_by_arm)
            if estimate_result.cluster_count_by_arm is not None
            else None
        ),
        "comparison_method": comparison_method,
        "estimand": profile["estimand"] if profile else None,
        "evidence_role": profile["evidence_role"] if profile else None,
        "thresholds": dict(thresholds),
        "arm_counts": dict(estimate_result.arm_counts),
        "unassigned_arm_count": estimate_result.unassigned_arm_count,
        "smaller_arm_count": estimate_result.smaller_arm_count,
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
