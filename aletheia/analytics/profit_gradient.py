"""Seven-layer profit attribution with event ownership and fail-closed gates."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import math
import random
from typing import Any, Iterable, Mapping


PROFIT_GRADIENT_CONTRACT_VERSION = "profit_gradient/v2"
LAYERS = ("Selection", "Decision", "Risk", "Execution", "Holding", "Exit", "Costs")
_CANDIDATE_LAYERS = frozenset({"Selection", "Decision", "Risk"})
_POSITION_LAYERS = frozenset(LAYERS) - _CANDIDATE_LAYERS
COMPARISON_METHODS = (
    "prospective_randomized",
    "paired_same_unit",
    "deterministic_decomposition",
    "directional_observational",
)
_COMPARISON_PROFILES = {
    "prospective_randomized": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": 20,
        "requires_two_arms": True,
        "allows_unique_winner": True,
        "evidence_role": "prospective_confirmatory",
    },
    "paired_same_unit": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": True,
        "evidence_role": "paired_confirmatory",
    },
    "deterministic_decomposition": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "measured_only",
    },
    "directional_observational": {
        "minimum_independent_units": 30,
        "minimum_smaller_arm": None,
        "requires_two_arms": False,
        "allows_unique_winner": False,
        "evidence_role": "directional_only",
    },
}
_LAYER_COMPONENTS = {
    "Selection": "candidate_selection",
    "Decision": "operator_decision",
    "Risk": "risk_policy",
    "Execution": "execution_fill",
    "Holding": "holding_authorization",
    "Exit": "exit_geometry",
    "Costs": "avoidable_extra_cost",
}
_COST_BASELINE_KINDS = frozenset(
    {"realizable_limit", "spot_vs_perpetual", "turnover", "venue"}
)


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


def _comparison_method(context: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    value = context.get("comparison_method")
    if not isinstance(value, str) or not value:
        return None, ["missing_comparison_method"]
    if value not in COMPARISON_METHODS:
        return value, ["unsupported_comparison_method"]
    return value, []


def _contract_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    value = context.get("gradient_contract_version")
    if value != PROFIT_GRADIENT_CONTRACT_VERSION:
        return False, ["gradient_contract_version_mismatch"]
    return True, []


def _evidence_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    if context.get("evidence_integrity") != "verified":
        return False, ["evidence_integrity_unverified"]
    return True, []


def _thresholds_for(method: str | None) -> dict[str, Any]:
    profile = _COMPARISON_PROFILES.get(method or "")
    if profile is None:
        return {}
    return {
        "minimum_independent_units": profile["minimum_independent_units"],
        "minimum_smaller_arm": profile["minimum_smaller_arm"],
        "requires_two_arms": profile["requires_two_arms"],
        "minimum_risk_rejects": 20,
        "minimum_absolute_effect_bp": 5.0,
        "cluster_interval": "95%_cluster_bootstrap_excluding_zero",
        "temporal_stability": "chronological_halves_same_sign",
        "top1_absolute_contribution_share_max": 0.25,
        "top5_absolute_contribution_share_max": 0.50,
    }


def _valid_evidence_refs(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def _valid_stage_window(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    start = _timestamp(value.get("start_at"))
    end = _timestamp(value.get("end_at"))
    return start is not None and end is not None and end >= start


def _ownership_reason(layer: str, raw: Mapping[str, Any]) -> str | None:
    ownership = raw.get("ownership")
    if not isinstance(ownership, Mapping):
        return "ownership_evidence_missing"
    if ownership.get("stage") != layer.lower():
        return "ownership_stage_mismatch"
    if layer == "Holding":
        if ownership.get("state") not in {"hypothesis_invalidated", "horizon_expired"}:
            return "holding_invalidity_not_proven"
        if ownership.get("authorization") != "none":
            return "holding_authorization_present"
        if ownership.get("loss_component") != "post_boundary_hold":
            return "holding_loss_component_invalid"
    elif layer == "Exit":
        if ownership.get("thesis") != "valid":
            return "exit_thesis_not_valid"
        if ownership.get("continued_hold") != "reasonable":
            return "continued_hold_not_reasonable"
        if ownership.get("failure") != "protection_geometry":
            return "exit_protection_failure_not_proven"
        if ownership.get("loss_component") != "protection_or_target_geometry":
            return "exit_loss_component_invalid"
    elif layer == "Execution":
        if ownership.get("decision") != "exit_authorized":
            return "execution_exit_decision_missing"
        if ownership.get("loss_component") != "fill_or_execution":
            return "execution_loss_component_invalid"
    return None


def _execution_reason(raw: Mapping[str, Any]) -> str | None:
    evidence = raw.get("execution_evidence")
    if not isinstance(evidence, Mapping):
        return "execution_component_not_isolated"
    if evidence.get("fees_excluded") is not True:
        return "execution_fees_not_excluded"
    if evidence.get("funding_excluded") is not True:
        return "execution_funding_not_excluded"
    if evidence.get("cost_event_id"):
        return "execution_cost_event_overlap"
    if not str(evidence.get("baseline_id") or "").strip():
        return "execution_baseline_identity_missing"
    impact = _finite(evidence.get("incremental_fill_loss_quote"))
    if impact is None or impact < 0:
        return "execution_fill_loss_invalid"
    return None


def _cost_reason(raw: Mapping[str, Any], paired_delta_quote: float) -> str | None:
    evidence = raw.get("cost_evidence")
    if not isinstance(evidence, Mapping):
        return "cost_baseline_unavailable"
    if not str(evidence.get("cost_event_id") or "").strip():
        return "cost_event_identity_missing"
    if evidence.get("avoidable") is not True:
        return "cost_not_avoidable"
    if evidence.get("baseline_kind") not in _COST_BASELINE_KINDS:
        return "cost_baseline_not_realizable"
    if not str(evidence.get("baseline_id") or "").strip():
        return "cost_baseline_identity_missing"
    if not str(evidence.get("cost_model_id") or "").strip():
        return "cost_model_identity_missing"
    extra = _finite(evidence.get("avoidable_extra_cost_quote"))
    if extra is None or extra < 0:
        return "avoidable_cost_amount_invalid"
    if not math.isclose(extra, abs(paired_delta_quote), rel_tol=1e-9, abs_tol=1e-8):
        return "avoidable_cost_amount_mismatch"
    return None


def _normalized_samples(
    layer: str, raw_samples: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[str], int]:
    fact_unit, economic_unit = _expected_units(layer)
    samples = []
    exclusions = []
    raw_count = 0
    seen_units: set[str] = set()
    seen_harm_events: set[str] = set()
    seen_attribution_events: set[str] = set()
    seen_cost_events: set[str] = set()
    expected_component = _LAYER_COMPONENTS[layer]
    for raw in raw_samples:
        raw_count += 1
        if not isinstance(raw, Mapping):
            exclusions.append("sample_not_object")
            continue
        if raw.get("fact_unit") != fact_unit or raw.get("economic_unit") != economic_unit:
            exclusions.append("denominator_or_economic_unit_mismatch")
            continue
        if raw.get("component") != expected_component:
            exclusions.append("layer_component_mismatch")
            continue
        ownership_reason = _ownership_reason(layer, raw)
        if ownership_reason is not None:
            exclusions.append(ownership_reason)
            continue
        if not _valid_evidence_refs(raw.get("evidence_refs")):
            exclusions.append("evidence_reference_missing")
            continue
        if not _valid_stage_window(raw.get("stage_window")):
            exclusions.append("stage_window_invalid")
            continue
        unit_id = str(raw.get("unit_id") or "").strip()
        harm_event_id = str(raw.get("harm_event_id") or "").strip()
        attribution_event_id = str(raw.get("attribution_event_id") or "").strip()
        cluster_id = str(raw.get("cluster_id") or "").strip()
        delta_bp = _finite(raw.get("paired_net_delta_bp"))
        delta_quote = _finite(raw.get("paired_net_delta_quote"))
        notional_quote = _finite(raw.get("notional_quote"))
        occurred_at = _timestamp(raw.get("occurred_at"))
        if not unit_id or not harm_event_id or not attribution_event_id or not cluster_id:
            exclusions.append("attribution_identity_missing")
            continue
        if delta_bp is None or delta_quote is None or occurred_at is None:
            exclusions.append("incomplete_paired_sample")
            continue
        if notional_quote is None or notional_quote <= 0:
            exclusions.append("notional_basis_invalid")
            continue
        expected_delta_bp = delta_quote / notional_quote * 10_000.0
        if not math.isclose(delta_bp, expected_delta_bp, rel_tol=1e-9, abs_tol=1e-8):
            exclusions.append("paired_delta_units_mismatch")
            continue
        if layer in _POSITION_LAYERS and not str(raw.get("position_id") or "").strip():
            exclusions.append("position_identity_missing")
            continue
        if layer != "Costs" and isinstance(raw.get("cost_evidence"), Mapping):
            exclusions.append("cost_evidence_non_cost_layer")
            continue
        if layer == "Execution":
            execution_reason = _execution_reason(raw)
            if execution_reason is not None:
                exclusions.append(execution_reason)
                continue
        if layer == "Costs":
            cost_reason = _cost_reason(raw, delta_quote)
            if cost_reason is not None:
                exclusions.append(cost_reason)
                continue
        if unit_id in seen_units:
            exclusions.append("duplicate_assignment_unit")
            continue
        if harm_event_id in seen_harm_events:
            exclusions.append("duplicate_harm_event")
            continue
        if attribution_event_id in seen_attribution_events:
            exclusions.append("duplicate_attribution_event")
            continue
        cost_evidence = raw.get("cost_evidence")
        cost_event_id = (
            str(cost_evidence.get("cost_event_id") or "").strip()
            if isinstance(cost_evidence, Mapping)
            else ""
        )
        if layer == "Costs" and cost_event_id in seen_cost_events:
            exclusions.append("duplicate_cost_event")
            continue
        seen_units.add(unit_id)
        seen_harm_events.add(harm_event_id)
        seen_attribution_events.add(attribution_event_id)
        if cost_event_id:
            seen_cost_events.add(cost_event_id)
        samples.append(
            {
                "unit_id": unit_id,
                "harm_event_id": harm_event_id,
                "attribution_event_id": attribution_event_id,
                "position_id": str(raw.get("position_id") or "").strip(),
                "cluster_id": cluster_id,
                "paired_net_delta_bp": delta_bp,
                "paired_net_delta_quote": delta_quote,
                "notional_quote": notional_quote,
                "occurred_at": occurred_at,
                "arm": str(raw.get("arm") or ""),
                "stage_window": dict(raw["stage_window"]),
                "component": expected_component,
                "baseline_id": (
                    str(cost_evidence.get("baseline_id") or "")
                    if isinstance(cost_evidence, Mapping)
                    else None
                ),
                "cost_event_id": cost_event_id or None,
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
        normalized = (
            [value.strip() if isinstance(value, str) else "" for value in values]
            if isinstance(values, list)
            else []
        )
        if not normalized or any(not value for value in normalized) or len(set(normalized)) != 1:
            reasons.append(f"mixed_or_missing_{field}")
    return not reasons, reasons


def _global_gate(context: Mapping[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    candidate_count = _finite(context.get("candidate_count"))
    capital_days = _finite(context.get("capital_days"))
    if candidate_count is None or candidate_count <= 0:
        reasons.append("candidate_count_invalid")
    if capital_days is None or capital_days <= 0:
        reasons.append("capital_days_invalid")
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
        elif value > 1.0:
            reasons.append(f"{field}_above_1.00")
    epoch_ok, epoch_reasons = _epoch_gate(context)
    reasons.extend(epoch_reasons)
    return bool(epoch_ok and not reasons), reasons


def _coerce_raw_samples(value: Any) -> tuple[list[Any], bool]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return [], True
    try:
        return list(value), False
    except TypeError:
        return [], True


def _prepare_layers(
    layer_samples: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[
    dict[str, tuple[list[dict[str, Any]], list[str], int]],
    set[str],
    set[str],
]:
    prepared: dict[str, tuple[list[dict[str, Any]], list[str], int]] = {}
    declared_layers: dict[str, set[str]] = defaultdict(set)
    declared_cost_layers: dict[str, set[str]] = defaultdict(set)
    for layer in LAYERS:
        raw_items, malformed = _coerce_raw_samples(layer_samples.get(layer, ()))
        if malformed:
            prepared[layer] = ([], ["layer_samples_malformed"], 0)
            continue
        for raw in raw_items:
            if isinstance(raw, Mapping):
                harm_event_id = str(raw.get("harm_event_id") or "").strip()
                if harm_event_id:
                    declared_layers[harm_event_id].add(layer)
                cost_evidence = raw.get("cost_evidence")
                if isinstance(cost_evidence, Mapping):
                    cost_event_id = str(cost_evidence.get("cost_event_id") or "").strip()
                    if cost_event_id:
                        declared_cost_layers[cost_event_id].add(layer)
        prepared[layer] = _normalized_samples(layer, raw_items)

    conflicts = {
        harm_event_id
        for harm_event_id, layers in declared_layers.items()
        if len(layers) > 1
    }
    cost_conflicts = {
        cost_event_id
        for cost_event_id, layers in declared_cost_layers.items()
        if len(layers) > 1
    }
    if not conflicts and not cost_conflicts:
        return prepared, conflicts, cost_conflicts

    for layer, (samples, exclusions, raw_count) in prepared.items():
        declared_conflicts = sum(
            1
            for harm_event_id, layers in declared_layers.items()
            if harm_event_id in conflicts and layer in layers
        )
        exclusions.extend(["cross_layer_harm_event"] * declared_conflicts)
        declared_cost_conflicts = sum(
            1
            for cost_event_id, layers in declared_cost_layers.items()
            if cost_event_id in cost_conflicts and layer in layers
        )
        exclusions.extend(["cross_layer_cost_event"] * declared_cost_conflicts)
        kept = [
            sample
            for sample in samples
            if sample["harm_event_id"] not in conflicts
            and sample.get("cost_event_id") not in cost_conflicts
        ]
        prepared[layer] = kept, exclusions, raw_count
    return prepared, conflicts, cost_conflicts


def _layer_result(
    layer: str,
    raw_samples: Iterable[Mapping[str, Any]],
    *,
    prepared: tuple[list[dict[str, Any]], list[str], int] | None = None,
    candidate_count: int,
    capital_days: float | None,
    risk_reject_count: int,
    global_ok: bool,
    global_reasons: list[str],
    comparison_method: str | None,
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    samples, exclusions, raw_count = prepared or _normalized_samples(layer, raw_samples)
    estimate, lower, upper = _cluster_interval(samples)
    unit_count = len(samples)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    arms = defaultdict(int)
    for sample in samples:
        if sample["arm"]:
            arms[sample["arm"]] += 1
    requires_two_arms = bool(profile and profile["requires_two_arms"])
    smaller_arm = min(arms.values()) if requires_two_arms and len(arms) == 2 else None
    top1, top5 = _concentration(samples)
    total_quote = sum(sample["paired_net_delta_quote"] for sample in samples)
    frequency = unit_count / candidate_count if candidate_count > 0 else None
    gradient = frequency * lower if frequency is not None and lower is not None else None
    reasons = list(global_reasons)
    minimum_units = int(thresholds.get("minimum_independent_units") or 30)
    minimum_smaller_arm = _finite(thresholds.get("minimum_smaller_arm"))
    if unit_count < minimum_units:
        reasons.append(f"independent_units_below_{minimum_units}")
    if requires_two_arms:
        if len(arms) != 2:
            reasons.append("randomized_two_arms_required")
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
    if not _same_sign_halves(samples):
        reasons.append("temporal_halves_disagree")
    if top1 is None or top5 is None:
        reasons.append("concentration_unavailable")
    else:
        if top1 > float(thresholds.get("top1_absolute_contribution_share_max") or 0.25):
            reasons.append("top1_concentration_above_25pct")
        if top5 > float(thresholds.get("top5_absolute_contribution_share_max") or 0.50):
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
        "comparison_method": comparison_method,
        "evidence_role": profile["evidence_role"] if profile else None,
        "thresholds": dict(thresholds),
        "arm_counts": dict(sorted(arms.items())),
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
    """Return a winner only when the declared method permits one."""
    if not isinstance(layer_samples, Mapping):
        layer_samples = {}
    if not isinstance(evidence_context, Mapping):
        evidence_context = {}
    global_ok, global_reasons = _global_gate(evidence_context)
    contract_ok, contract_reasons = _contract_gate(evidence_context)
    evidence_ok, evidence_reasons = _evidence_gate(evidence_context)
    comparison_method, method_reasons = _comparison_method(evidence_context)
    global_reasons.extend(contract_reasons)
    global_reasons.extend(evidence_reasons)
    global_reasons.extend(method_reasons)
    thresholds = _thresholds_for(comparison_method)
    profile = _COMPARISON_PROFILES.get(comparison_method or "")
    unique_winner_eligible = bool(profile and profile["allows_unique_winner"])
    prepared, conflicts, cost_conflicts = _prepare_layers(layer_samples)
    malformed_layers = any(
        "layer_samples_malformed" in exclusions
        for _samples, exclusions, _raw_count in prepared.values()
    )
    incomplete_layers = [
        layer for layer, (samples, _exclusions, _raw_count) in prepared.items() if not samples
    ]
    if conflicts:
        global_reasons.append("cross_layer_harm_event")
    if cost_conflicts:
        global_reasons.append("cross_layer_cost_event")
    if malformed_layers:
        global_reasons.append("layer_samples_malformed")
    if incomplete_layers:
        global_reasons.append("layer_samples_incomplete")
    global_ok = bool(
        global_ok
        and contract_ok
        and evidence_ok
        and not method_reasons
        and not conflicts
        and not cost_conflicts
        and not malformed_layers
        and not incomplete_layers
    )
    candidate_count = int(_finite(evidence_context.get("candidate_count")) or 0)
    capital_days = _finite(evidence_context.get("capital_days"))
    risk_reject_count = int(_finite(evidence_context.get("risk_reject_count")) or 0)
    layers = {
        layer: _layer_result(
            layer,
            layer_samples.get(layer, ()),
            prepared=prepared[layer],
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
        "evidence_integrity": evidence_context.get("evidence_integrity"),
        "evidence_role": profile["evidence_role"] if profile else None,
        "thresholds": thresholds,
        "unique_winner_eligible": unique_winner_eligible,
        "attribution_integrity": (
            "ineligible"
            if (
                conflicts
                or cost_conflicts
                or not contract_ok
                or not evidence_ok
                or malformed_layers
                or incomplete_layers
            )
            else "eligible"
        ),
        "cross_layer_harm_event_count": len(conflicts),
        "cross_layer_cost_event_count": len(cost_conflicts),
        "incomplete_layers": incomplete_layers,
        "verdict": verdict,
        "winner_layer": winner_layer,
        "runner_up_harm_upper_bp": runner_upper,
        "global_gate_status": "eligible" if global_ok else "ineligible",
        "global_gate_reasons": sorted(set(global_reasons)),
        "residual": {
            "status": "unresolved" if conflicts or cost_conflicts else "none",
            "cross_layer_harm_event_count": len(conflicts),
            "cross_layer_cost_event_count": len(cost_conflicts),
            "economic_amount": None,
            "reason": (
                "cross_layer_harm_event"
                if conflicts
                else "cross_layer_cost_event"
                if cost_conflicts
                else None
            ),
        },
        "layers": layers,
        "next_action": next_action,
        "automatic_behavior_change_authorized": False,
    }
