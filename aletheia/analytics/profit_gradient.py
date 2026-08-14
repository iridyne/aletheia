"""Seven-layer profit attribution with fixed evidence and unique-winner gates.

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


PROFIT_GRADIENT_CONTRACT_VERSION = "profit_gradient/v1"
LAYERS = ("Selection", "Decision", "Risk", "Execution", "Holding", "Exit", "Costs")
_CANDIDATE_LAYERS = frozenset({"Selection", "Decision", "Risk"})


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


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
        f"{key}:{','.join(f'{value:.12g}' for value in values)}" for key, values in sorted(clusters.items())
    )
    seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    bootstraps = []
    for _ in range(iterations):
        selected = [rng.choice(cluster_values) for _ in cluster_values]
        bootstraps.append(sum(selected) / len(selected))
    return estimate, _percentile(bootstraps, 0.025), _percentile(bootstraps, 0.975)


def _expected_units(layer: str) -> tuple[str, str]:
    if layer in _CANDIDATE_LAYERS:
        return "candidate_fact", "fixed_horizon_net_return"
    return "position_fact", "verified_settled_net_economics"


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
        if not unit_id or not cluster_id or delta_bp is None or delta_quote is None or occurred_at is None:
            exclusions.append("incomplete_paired_sample")
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
            }
        )
    return samples, exclusions, raw_count


def _same_sign_halves(samples: list[dict[str, Any]]) -> bool:
    ordered = sorted(samples, key=lambda sample: (sample["occurred_at"], sample["unit_id"]))
    midpoint = len(ordered) // 2
    if midpoint == 0 or midpoint == len(ordered):
        return False
    means = [
        sum(sample["paired_net_delta_bp"] for sample in half) / len(half)
        for half in (ordered[:midpoint], ordered[midpoint:])
    ]
    return means[0] != 0 and means[1] != 0 and (means[0] > 0) == (means[1] > 0)


def _concentration(samples: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    contributions = sorted((abs(sample["paired_net_delta_quote"]) for sample in samples), reverse=True)
    total = sum(contributions)
    if total <= 0:
        return None, None
    return contributions[0] / total, sum(contributions[:5]) / total


def _epoch_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    for field in (
        "behavior_fingerprints",
        "policy_epochs",
        "market_data_epochs",
        "cost_model_epochs",
    ):
        values = context.get(field)
        if not isinstance(values, list) or len(set(str(value) for value in values if value)) != 1:
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
) -> dict[str, Any]:
    samples, exclusions, raw_count = _normalized_samples(layer, raw_samples)
    estimate, lower, upper = _cluster_interval(samples)
    unit_count = len(samples)
    arms = defaultdict(int)
    for sample in samples:
        if sample["arm"]:
            arms[sample["arm"]] += 1
    smaller_arm = min(arms.values()) if len(arms) >= 2 else 0
    top1, top5 = _concentration(samples)
    total_quote = sum(sample["paired_net_delta_quote"] for sample in samples)
    frequency = unit_count / candidate_count if candidate_count > 0 else None
    gradient = frequency * lower if frequency is not None and lower is not None else None
    reasons = list(global_reasons)
    if unit_count < 30:
        reasons.append("independent_units_below_30")
    if smaller_arm < 20:
        reasons.append("smaller_arm_below_20")
    if layer == "Risk" and risk_reject_count < 20:
        reasons.append("risk_rejects_below_20")
    if estimate is None or abs(estimate) < 5.0:
        reasons.append("absolute_effect_below_5bp")
    if lower is None or upper is None or lower <= 0 <= upper:
        reasons.append("cluster_interval_crosses_zero")
    if estimate is None or estimate <= 0:
        reasons.append("direction_does_not_reduce_harm")
    if not _same_sign_halves(samples):
        reasons.append("temporal_halves_disagree")
    if top1 is None or top5 is None:
        reasons.append("concentration_unavailable")
    else:
        if top1 > 0.25:
            reasons.append("top1_concentration_above_25pct")
        if top5 > 0.50:
            reasons.append("top5_concentration_above_50pct")
    reasons.extend(exclusions)
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
        "independent_unit_count": unit_count,
        "cluster_count": len({sample["cluster_id"] for sample in samples}),
        "smaller_arm_count": smaller_arm,
        "eligible_frequency": frequency,
        "paired_net_delta_bp": estimate,
        "lower_95_bp": lower,
        "upper_95_bp": upper,
        "gradient_lower_bp": gradient,
        "harm_quote": total_quote,
        "harm_quote_per_100_candidates": (total_quote / candidate_count * 100.0 if candidate_count > 0 else None),
        "harm_quote_per_capital_day": (
            total_quote / capital_days if capital_days is not None and capital_days > 0 else None
        ),
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
    """Return a winner only when one layer uniquely clears every fixed gate."""
    global_ok, global_reasons = _global_gate(evidence_context)
    candidate_count = int(_finite(evidence_context.get("candidate_count")) or 0)
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
        )
        for layer in LAYERS
    }
    eligible = [result for result in layers.values() if result["gate_status"] == "eligible"]
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
    return {
        "contract_version": PROFIT_GRADIENT_CONTRACT_VERSION,
        "verdict": verdict,
        "winner_layer": winner_layer,
        "runner_up_harm_upper_bp": runner_upper,
        "global_gate_status": "eligible" if global_ok else "ineligible",
        "global_gate_reasons": global_reasons,
        "layers": layers,
        "next_action": (
            "register_one_single_variable_forward_experiment" if separated else "close_largest_evidence_gap_only"
        ),
        "automatic_behavior_change_authorized": False,
    }
