from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import aletheia.analytics.profit_gradient as gradient
import aletheia.analytics.profit_gradient_estimators as estimators


def _context(**overrides) -> dict:
    context = {
        "gradient_contract_version": "profit_gradient/v3",
        "evidence_integrity": "verified",
        "reconciliation_contract_version": gradient.RECONCILIATION_CONTRACT_VERSION,
        "behavior_fingerprints": ["behavior-v1"],
        "policy_epochs": ["policy-v1"],
        "market_data_epochs": ["market-v1"],
        "cost_model_epochs": ["cost-v1"],
        "candidate_outcome_coverage": 0.9,
        "position_causal_link_completeness": 0.97,
        "verified_settled_economics_coverage": 0.98,
        "ordered_path_coverage": 0.85,
        "candidate_count": 100,
        "capital_days": 20.0,
        "risk_reject_count": 25,
        "comparison_method": "prospective_randomized",
        "assignment_salts": ["salt-v1"],
        "horizons": ["24h"],
        "outcome_contract_versions": ["outcome-v1"],
        "metric_versions": ["metric-v1"],
        "statistical_unit_versions": ["unit-v1"],
        "stages": ["paper_10"],
    }
    context.update(overrides)
    return context


def _samples(
    layer: str, *, effect_bp: float, count: int = 40, with_arms: bool = True
) -> list[dict]:
    fact_unit = "candidate_fact" if layer in {"Selection", "Decision", "Risk"} else "position_fact"
    economic_unit = "fixed_horizon_net_return" if fact_unit == "candidate_fact" else "verified_settled_net_economics"
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    samples = []
    for index in range(count):
        cluster = index
        arm = ("control" if cluster % 2 == 0 else "treatment") if with_arms else ""
        value_bp = effect_bp if not with_arms or arm == "treatment" else 0.0
        samples.append(
            {
                "fact_unit": fact_unit,
                "economic_unit": economic_unit,
                "unit_id": f"{layer}-{index}",
                "cluster_id": f"day-{cluster}",
                "occurred_at": (started + timedelta(hours=index)).isoformat(),
                "arm": arm,
                "paired_net_delta_bp": value_bp,
                "paired_net_delta_quote": value_bp / 10.0,
                "notional_quote": 1000.0,
                "harm_event_id": f"{layer}:harm:{index}",
                "attribution_event_id": f"{layer}:attr:{index}",
                "position_id": f"position-{layer}-{index}" if fact_unit == "position_fact" else None,
                "component": gradient._LAYER_COMPONENTS[layer],
                "ownership": (
                    {"stage": layer.lower(), "loss_component": gradient._LAYER_COMPONENTS[layer]}
                    if layer in {"Selection", "Decision", "Risk"}
                    else {"stage": "holding", "state": "horizon_expired", "authorization": "none", "loss_component": "post_boundary_hold"}
                    if layer == "Holding"
                    else {"stage": "exit", "thesis": "valid", "continued_hold": "reasonable", "failure": "protection_geometry", "loss_component": "protection_or_target_geometry"}
                    if layer == "Exit"
                    else {"stage": "execution", "decision": "exit_authorized", "loss_component": "fill_or_execution"}
                    if layer == "Execution"
                    else {"stage": "costs", "loss_component": "avoidable_extra_cost"}
                ),
                "stage_window": {"start_at": started.isoformat(), "end_at": (started + timedelta(hours=index + 1)).isoformat()},
                "evidence_refs": [f"fixture:{layer}:{index}"],
                "execution_evidence": {
                    "fees_excluded": True,
                    "funding_excluded": True,
                    "baseline_id": f"baseline:{layer}:{index}",
                    "incremental_fill_loss_quote": abs(value_bp / 10.0),
                } if layer == "Execution" else None,
                "cost_evidence": {
                    "cost_event_id": f"cost-event:{layer}:{index}",
                    "avoidable": True,
                    "baseline_kind": "realizable_limit",
                    "baseline_id": f"baseline:{layer}:{index}",
                    "cost_model_id": "cost-v1",
                    "avoidable_extra_cost_quote": abs(value_bp / 10.0),
                } if layer == "Costs" else None,
                "reconciliation": {
                    "mode": "exclusive",
                    "group_id": f"{layer}:reconciliation:{index}",
                    "legacy": True,
                },
            }
        )
    return samples


def _seven_layers(*, winner: str = "Costs") -> dict[str, list[dict]]:
    return {layer: _samples(layer, effect_bp=10.0 if layer == winner else 1.0) for layer in gradient.LAYERS}


def _unique_clone(sample: dict, suffix: str) -> dict:
    clone = deepcopy(sample)
    clone["unit_id"] = f"{clone['unit_id']}-{suffix}"
    clone["harm_event_id"] = f"{clone['harm_event_id']}-{suffix}"
    clone["attribution_event_id"] = f"{clone['attribution_event_id']}-{suffix}"
    clone["reconciliation"]["group_id"] = f"{clone['reconciliation']['group_id']}-{suffix}"
    if isinstance(clone.get("cost_evidence"), dict):
        clone["cost_evidence"]["cost_event_id"] = f"{clone['cost_evidence']['cost_event_id']}-{suffix}"
    return clone


def test_one_separated_layer_is_unique_winner_with_normalized_harm_units():
    samples = _seven_layers()
    frozen = deepcopy(samples)

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert samples == frozen
    assert result["contract_version"] == "profit_gradient/v3"
    assert result["comparison_method"] == "prospective_randomized"
    assert result["estimand"] == "treatment_minus_control_cluster_mean_bp"
    assert result["cohort_identity"] == {
        "assignment_salt": "salt-v1",
        "horizon": "24h",
        "outcome_contract_version": "outcome-v1",
        "metric_version": "metric-v1",
        "statistical_unit_version": "unit-v1",
        "stage": "paper_10",
    }
    assert result["thresholds"]["minimum_smaller_arm"] == 20
    assert result["verdict"] == "unique_max_direction"
    assert result["winner_layer"] == "Costs"
    assert result["next_action"] == "register_one_single_variable_forward_experiment"
    assert result["automatic_behavior_change_authorized"] is False
    costs = result["layers"]["Costs"]
    assert costs["fact_unit"] == "position_fact"
    assert costs["economic_unit"] == "verified_settled_net_economics"
    assert costs["lower_95_bp"] == pytest.approx(10.0)
    assert costs["gradient_lower_bp"] == pytest.approx(4.0)
    assert costs["cluster_count_by_arm"] == {"control": 20, "treatment": 20}
    assert costs["harm_quote"] == pytest.approx(20.0)
    assert costs["harm_quote_per_100_candidates"] == pytest.approx(20.0)
    assert costs["harm_quote_per_capital_day"] == pytest.approx(1.0)


def test_cross_layer_harm_event_requires_explicit_residual_decomposition():
    selection = _samples("Selection", effect_bp=20.0, count=1, with_arms=False)[0]
    decision = _samples("Decision", effect_bp=30.0, count=1, with_arms=False)[0]
    decision["harm_event_id"] = selection["harm_event_id"]
    samples = {layer: [] for layer in gradient.LAYERS}
    samples["Selection"] = [selection]
    samples["Decision"] = [decision]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context(comparison_method="paired_same_unit"))

    assert result["attribution_integrity"] == "ineligible"
    assert result["cross_layer_harm_event_count"] == 1
    assert "cross_layer_harm_event" in result["global_gate_reasons"]
    assert result["residual"]["status"] == "unresolved"


def test_residual_decomposition_reconciles_once_across_layers():
    selection = _samples("Selection", effect_bp=20.0, count=1, with_arms=False)[0]
    decision = _samples("Decision", effect_bp=30.0, count=1, with_arms=False)[0]
    decision["harm_event_id"] = selection["harm_event_id"]
    group = "residual:known-net-result"
    for sample, quote, bp in ((selection, 2.0, 20.0), (decision, 3.0, 30.0)):
        sample["reconciliation"] = {
            "mode": "residual",
            "group_id": group,
            "declared_total_quote": 5.0,
            "component_quote": quote,
            "component_bp": bp,
        }
    samples = {layer: [] for layer in gradient.LAYERS}
    samples["Selection"] = [selection]
    samples["Decision"] = [decision]

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    assert result["cross_layer_harm_event_count"] == 0
    assert result["reconciliation"]["status"] == "reconciled"
    assert result["reconciliation"]["declared_total_quote"] == pytest.approx(5.0)
    assert result["residual"] == {
        "status": "reconciled",
        "cross_layer_harm_event_count": 0,
        "cross_layer_cost_event_count": 0,
        "economic_amount": 5.0,
        "reason": None,
    }


def test_residual_reconciliation_perturbation_is_unavailable():
    selection = _samples("Selection", effect_bp=20.0, count=1)[0]
    decision = _samples("Decision", effect_bp=30.0, count=1)[0]
    decision["harm_event_id"] = selection["harm_event_id"]
    for sample, quote, bp in ((selection, 2.0, 20.0), (decision, 3.0, 30.0)):
        sample["reconciliation"] = {
            "mode": "residual",
            "group_id": "residual:perturbed",
            "declared_total_quote": 5.0,
            "component_quote": quote,
            "component_bp": bp,
        }
    decision["reconciliation"]["component_quote"] = 3.1
    samples = {layer: [] for layer in gradient.LAYERS}
    samples["Selection"] = [selection]
    samples["Decision"] = [decision]

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    assert result["attribution_integrity"] == "ineligible"
    assert "cross_layer_harm_event" in result["global_gate_reasons"]
    assert "reconciliation_mismatch" in result["global_gate_reasons"]


def test_missing_ownership_is_ineligible_even_with_valid_numeric_delta():
    samples = _seven_layers()
    samples["Holding"][0].pop("ownership")

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["layers"]["Holding"]["eligible_sample_count"] == 39
    assert "ownership_evidence_missing" in result["layers"]["Holding"]["gate_reasons"]


def test_reconciliation_mapping_is_mandatory_at_the_selector_boundary():
    samples = _seven_layers()
    samples["Costs"][0].pop("reconciliation")

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert "reconciliation_mapping_missing" in result["layers"]["Costs"]["gate_reasons"]
    assert result["layers"]["Costs"]["eligible_sample_count"] == 39


def test_duplicate_harm_and_cost_events_are_visible_exclusions():
    samples = _seven_layers()
    samples["Costs"][1]["harm_event_id"] = samples["Costs"][0]["harm_event_id"]
    samples["Costs"][2]["cost_evidence"]["cost_event_id"] = samples["Costs"][0]["cost_evidence"]["cost_event_id"]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    reasons = result["layers"]["Costs"]["gate_reasons"]
    assert "duplicate_harm_event" in reasons
    assert "duplicate_cost_event" in reasons


def test_execution_and_cost_cannot_share_a_cost_event():
    execution = _samples("Execution", effect_bp=10.0, count=1, with_arms=False)[0]
    costs = _samples("Costs", effect_bp=10.0, count=1, with_arms=False)[0]
    execution["execution_evidence"]["cost_event_id"] = "shared-cost"
    costs["cost_evidence"]["cost_event_id"] = "shared-cost"
    samples = {layer: [] for layer in gradient.LAYERS}
    samples["Execution"] = [execution]
    samples["Costs"] = [costs]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context(comparison_method="paired_same_unit"))

    assert result["cross_layer_cost_event_count"] == 1
    assert "cross_layer_cost_event" in result["global_gate_reasons"]


def test_multiple_eligible_layers_or_nonseparated_intervals_have_no_winner():
    samples = _seven_layers()
    samples["Exit"] = _samples("Exit", effect_bp=12.0)

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None
    assert result["next_action"] == "close_largest_evidence_gap_only"


def test_mixed_epoch_and_low_coverage_fail_every_layer_closed():
    context = _context(
        behavior_fingerprints=["behavior-v1", "behavior-v2"],
        candidate_outcome_coverage=0.79,
    )

    result = gradient.select_unique_max_direction(_seven_layers(), evidence_context=context)

    assert result["global_gate_status"] == "ineligible"
    assert result["verdict"] == "no_unique_direction"
    assert "mixed_or_missing_behavior_fingerprints" in result["global_gate_reasons"]
    assert "candidate_outcome_coverage_below_0.80" in result["global_gate_reasons"]
    assert all(layer["gate_status"] == "ineligible" for layer in result["layers"].values())


def test_denominators_risk_rejects_and_concentration_are_hard_gates():
    samples = _seven_layers(winner="Risk")
    wrong = deepcopy(samples["Risk"])
    for sample in wrong:
        sample["fact_unit"] = "position_fact"
        sample["economic_unit"] = "verified_settled_net_economics"
    samples["Risk"] = wrong

    result = gradient.select_unique_max_direction(samples, evidence_context=_context(risk_reject_count=19))

    risk = result["layers"]["Risk"]
    assert risk["independent_unit_count"] == 0
    assert "denominator_or_economic_unit_mismatch" in risk["gate_reasons"]
    assert "risk_rejects_below_20" in risk["gate_reasons"]
    assert result["verdict"] == "no_unique_direction"

    concentrated = _seven_layers()
    concentrated["Costs"][5]["paired_net_delta_quote"] = 1_000.0
    concentration_result = gradient.select_unique_max_direction(concentrated, evidence_context=_context())
    assert concentration_result["verdict"] == "no_unique_direction"
    assert "top1_concentration_above_25pct" in concentration_result["layers"]["Costs"]["gate_reasons"]


def test_paired_same_unit_does_not_require_arm_labels():
    samples = {
        layer: _samples(
            layer,
            effect_bp=10.0 if layer == "Costs" else 1.0,
            count=30,
            with_arms=False,
        )
        for layer in gradient.LAYERS
    }

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    assert result["comparison_method"] == "paired_same_unit"
    assert result["thresholds"]["minimum_smaller_arm"] is None
    assert result["layers"]["Costs"]["smaller_arm_count"] is None
    assert "smaller_arm_below_20" not in result["layers"]["Costs"]["gate_reasons"]
    assert result["winner_layer"] == "Costs"


def test_paired_effect_is_unit_weighted_not_cluster_weighted():
    samples = _samples("Costs", effect_bp=10.0, count=29, with_arms=False)
    large_cluster = _samples("Costs", effect_bp=-20.0, count=100, with_arms=False)
    for index, sample in enumerate(large_cluster):
        sample["unit_id"] = f"Costs-large-{index}"
        sample["cluster_id"] = "large-cluster"
        sample["harm_event_id"] = f"Costs:large-harm:{index}"
        sample["attribution_event_id"] = f"Costs:large-attr:{index}"
        sample["cost_evidence"]["cost_event_id"] = f"cost-event:large:{index}"

    normalized, exclusions, _ = gradient._normalized_samples(
        "Costs", [*samples, *large_cluster]
    )
    estimate = estimators.estimate_paired_same_unit(normalized)

    assert exclusions == []
    assert estimate.paired_net_delta_bp == pytest.approx(
        (29 * 10.0 - 100 * 20.0) / 129.0
    )
    assert estimate.paired_net_delta_bp < 0
    assert estimate.lower_95_bp is not None
    assert estimate.upper_95_bp is not None


def test_paired_same_unit_requires_thirty_unique_units():
    samples = {
        layer: _samples(layer, effect_bp=10.0, count=29, with_arms=False)
        for layer in gradient.LAYERS
    }

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 29
    assert costs["smaller_arm_count"] is None
    assert "independent_units_below_30" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_paired_same_unit_rejects_duplicate_position_units():
    samples = {
        layer: _samples(layer, effect_bp=10.0, count=30, with_arms=False)
        for layer in gradient.LAYERS
    }
    samples["Costs"][1]["unit_id"] = samples["Costs"][0]["unit_id"]

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 29
    assert "duplicate_assignment_unit" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_paired_same_unit_uses_resampling_clusters_for_independence_gate():
    samples = {
        layer: _samples(layer, effect_bp=10.0, count=40, with_arms=False)
        for layer in gradient.LAYERS
    }
    for index, sample in enumerate(samples["Costs"]):
        sample["cluster_id"] = f"cluster-{index // 20}"

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["eligible_sample_count"] == 40
    assert costs["independent_unit_count"] == 2
    assert "independent_units_below_30" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_prospective_randomized_retains_two_arm_threshold():
    samples = _seven_layers()
    for index, sample in enumerate(samples["Costs"]):
        sample["arm"] = "treatment" if index < 19 else "control"

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="prospective_randomized"),
    )

    costs = result["layers"]["Costs"]
    assert costs["smaller_arm_count"] == 19
    assert "smaller_arm_below_20" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_prospective_randomized_thresholds_use_independent_cluster_counts():
    samples = _seven_layers()
    costs = samples["Costs"]
    for index, sample in enumerate(costs):
        cluster = index // 10
        sample["cluster_id"] = f"cluster-{cluster}"
        sample["arm"] = "control" if cluster % 2 == 0 else "treatment"
        sample["paired_net_delta_bp"] = 0.0 if sample["arm"] == "control" else 10.0
        sample["paired_net_delta_quote"] = sample["paired_net_delta_bp"] / 10.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["eligible_sample_count"] == 40
    assert costs["independent_unit_count"] == 4
    assert costs["eligible_frequency"] == pytest.approx(0.4)
    assert costs["harm_quote_per_100_candidates"] == pytest.approx(20.0)
    assert costs["cluster_count_by_arm"] == {"control": 2, "treatment": 2}
    assert costs["smaller_arm_count"] == 2
    assert "independent_units_below_30" in costs["gate_reasons"]
    assert "smaller_arm_below_20" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_prospective_randomized_rejects_clusters_assigned_to_both_arms():
    samples = _seven_layers()
    costs = samples["Costs"]
    for index, sample in enumerate(costs):
        cluster = index // 2
        sample["cluster_id"] = f"shared-{cluster}"
        sample["arm"] = "control" if index % 2 == 0 else "treatment"

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["cluster_count_by_arm"] == {"control": 0, "treatment": 0}
    assert "randomized_cluster_multiple_arms" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_prospective_randomized_rejects_unassigned_units():
    samples = _seven_layers()
    unassigned = _samples("Costs", effect_bp=10.0, count=1)[0]
    unassigned["unit_id"] = "Costs-unassigned"
    unassigned["arm"] = ""
    unassigned["harm_event_id"] = "Costs:harm:unassigned"
    unassigned["attribution_event_id"] = "Costs:attr:unassigned"
    unassigned["cost_evidence"]["cost_event_id"] = "cost-event:unassigned"
    unassigned["reconciliation"]["group_id"] = "Costs:reconciliation:unassigned"
    samples["Costs"].append(unassigned)

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="prospective_randomized"),
    )

    costs = result["layers"]["Costs"]
    assert costs["unassigned_arm_count"] == 1
    assert "randomized_arm_missing" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_conflicting_delta_units_are_excluded():
    samples = _seven_layers()
    samples["Costs"][5]["paired_net_delta_quote"] = -1.0

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "paired_delta_sign_mismatch" in costs["gate_reasons"]
    assert result["winner_layer"] is None


@pytest.mark.parametrize(
    ("bp", "quote"),
    [(0.0, 1.0), (1.0, 0.0)],
)
def test_asymmetric_zero_delta_units_are_excluded(bp, quote):
    samples = _seven_layers()
    samples["Costs"][0]["paired_net_delta_bp"] = bp
    samples["Costs"][0]["paired_net_delta_quote"] = quote

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "paired_delta_zero_mismatch" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_randomized_temporal_effects_must_match_aggregate_direction():
    samples = _seven_layers()
    costs = samples["Costs"]
    for index, sample in enumerate(costs):
        if index < 19:
            sample["arm"] = "treatment"
            value = 100.0
        elif index < 20:
            sample["arm"] = "control"
            value = 110.0
        elif index == 20:
            sample["arm"] = "treatment"
            value = 0.0
        else:
            sample["arm"] = "control"
            value = 10.0
        sample["paired_net_delta_bp"] = value
        sample["paired_net_delta_quote"] = value / 10.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["paired_net_delta_bp"] == pytest.approx(80.0)
    assert "temporal_halves_disagree" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_randomized_effect_is_treatment_minus_control_not_pooled_mean():
    samples = _seven_layers()
    for sample in samples["Costs"]:
        if sample["arm"] == "control":
            sample["paired_net_delta_bp"] = 100.0
            sample["paired_net_delta_quote"] = 10.0
        else:
            sample["paired_net_delta_bp"] = 110.0
            sample["paired_net_delta_quote"] = 11.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["paired_net_delta_bp"] == pytest.approx(10.0)
    assert costs["estimand"] == "treatment_minus_control_cluster_mean_bp"
    assert result["winner_layer"] == "Costs"


@pytest.mark.parametrize(
    "field",
    [
        "assignment_salts",
        "horizons",
        "outcome_contract_versions",
        "metric_versions",
        "statistical_unit_versions",
        "stages",
    ],
)
def test_randomized_cohort_identity_is_required(field):
    context = _context()
    context.pop(field)

    result = gradient.select_unique_max_direction(_seven_layers(), evidence_context=context)

    assert result["cohort_identity"] is None
    assert f"mixed_or_missing_{field}" in result["global_gate_reasons"]
    assert result["winner_layer"] is None


@pytest.mark.parametrize(
    ("candidate_count", "reason"),
    [
        (None, "candidate_count_not_positive"),
        (0, "candidate_count_not_positive"),
        (-1, "candidate_count_not_positive"),
        (float("nan"), "candidate_count_not_positive"),
        (0.5, "candidate_count_not_integral"),
    ],
)
def test_invalid_candidate_denominator_cannot_select_a_winner(candidate_count, reason):
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(candidate_count=candidate_count)
    )
    assert result["winner_layer"] is None
    assert result["verdict"] == "no_unique_direction"
    assert reason in result["global_gate_reasons"]


@pytest.mark.parametrize(
    ("method", "reason"),
    [(None, "missing_comparison_method"), ("future_method", "unsupported_comparison_method")],
)
def test_missing_or_unknown_comparison_method_fails_closed(method, reason):
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(comparison_method=method)
    )

    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None
    assert reason in result["global_gate_reasons"]
    assert all(layer["gate_status"] == "ineligible" for layer in result["layers"].values())


@pytest.mark.parametrize(
    ("method", "role"),
    [
        ("deterministic_decomposition", "measured_only"),
        ("directional_observational", "directional_only"),
    ],
)
def test_noncausal_comparison_methods_cannot_produce_a_unique_winner(method, role):
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(comparison_method=method)
    )

    assert result["comparison_method"] == method
    assert result["evidence_role"] == role
    assert result["unique_winner_eligible"] is False
    assert result["layers"]["Costs"]["gate_status"] == "eligible"
    assert result["winner_layer"] is None
    assert result["verdict"] == "no_unique_direction"


@pytest.mark.parametrize("method", ["deterministic_decomposition", "directional_observational"])
@pytest.mark.parametrize(
    "field",
    [
        "horizons",
        "outcome_contract_versions",
        "metric_versions",
        "statistical_unit_versions",
        "stages",
    ],
)
def test_noncausal_cohort_identity_is_required(method, field):
    context = _context(comparison_method=method)
    context.pop(field)

    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=context
    )

    assert result["cohort_identity"] is None
    assert f"mixed_or_missing_{field}" in result["global_gate_reasons"]
    assert result["layers"]["Costs"]["gate_status"] == "ineligible"


@pytest.mark.parametrize(
    "field",
    [
        "horizons",
        "outcome_contract_versions",
        "metric_versions",
        "statistical_unit_versions",
        "stages",
    ],
)
def test_paired_cohort_identity_is_required(field):
    context = _context(comparison_method="paired_same_unit")
    context.pop(field)

    result = gradient.select_unique_max_direction(_seven_layers(), evidence_context=context)

    assert result["cohort_identity"] is None
    assert f"mixed_or_missing_{field}" in result["global_gate_reasons"]
    assert result["winner_layer"] is None


@pytest.mark.parametrize(
    "field",
    [
        "assignment_salts",
        "horizons",
        "outcome_contract_versions",
        "metric_versions",
        "statistical_unit_versions",
        "stages",
    ],
)
def test_randomized_cohort_identity_rejects_non_string_values(field):
    context = _context(**{field: [{}]})

    result = gradient.select_unique_max_direction(_seven_layers(), evidence_context=context)

    assert result["cohort_identity"] is None
    assert f"mixed_or_missing_{field}" in result["global_gate_reasons"]
    assert result["winner_layer"] is None


def test_paired_quote_harm_must_match_positive_bp_direction():
    samples = {
        layer: _samples(
            layer,
            effect_bp=10.0 if layer == "Costs" else 1.0,
            count=40,
            with_arms=False,
        )
        for layer in gradient.LAYERS
    }
    for sample in samples["Costs"][:10]:
        sample["paired_net_delta_bp"] = -1.0
        sample["paired_net_delta_quote"] = -10.0

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["paired_net_delta_bp"] > 5.0
    assert costs["harm_quote"] == pytest.approx(-70.0)
    assert "paired_quote_harm_not_positive" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_randomized_quote_harm_uses_cluster_arm_contrast_not_row_sums():
    samples = _seven_layers()
    costs = samples["Costs"]
    for index in range(10):
        extra = _unique_clone(costs[index * 2], f"extra-{index}")
        costs.append(extra)
    for sample in costs:
        value = 100.0 if sample["arm"] == "control" else 110.0
        sample["paired_net_delta_bp"] = value
        sample["paired_net_delta_quote"] = value

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["paired_net_delta_bp"] == pytest.approx(10.0)
    assert costs["harm_quote"] == pytest.approx(200.0)
    assert costs["harm_quote_per_100_candidates"] == pytest.approx(200.0)
    assert costs["top1_absolute_contribution_share"] < 0.25
    assert result["winner_layer"] == "Costs"


@pytest.mark.parametrize(
    ("large_quote", "expected_harm", "expected_winner"),
    [(0.0, 190.0, "Costs"), (-3.0, -110.0, None)],
)
def test_randomized_quote_harm_weights_heterogeneous_cluster_exposures(
    large_quote, expected_harm, expected_winner
):
    samples = _seven_layers()
    costs = samples["Costs"]
    treatment = next(sample for sample in costs if sample["arm"] == "treatment")
    treatment["cluster_id"] = "day-treatment-large"
    treatment["paired_net_delta_bp"] = large_quote
    treatment["paired_net_delta_quote"] = large_quote
    for index in range(99):
        extra = _unique_clone(treatment, f"treatment-large-{index + 1}")
        costs.append(extra)

    for sample in costs:
        if sample["arm"] == "control":
            sample["paired_net_delta_bp"] = 0.0
            sample["paired_net_delta_quote"] = 0.0
        elif sample["cluster_id"] != "day-treatment-large":
            sample["paired_net_delta_bp"] = 110.0
            sample["paired_net_delta_quote"] = 10.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["harm_quote"] == pytest.approx(expected_harm)
    if expected_harm < 0:
        assert "randomized_quote_harm_not_positive" in costs["gate_reasons"]
    assert result["winner_layer"] == expected_winner


def test_randomized_quote_concentration_centers_common_arm_baseline():
    samples = _seven_layers()
    for index, sample in enumerate(samples["Costs"]):
        if sample["arm"] == "control":
            sample["paired_net_delta_bp"] = 100.0
            sample["paired_net_delta_quote"] = 1_000.0
        else:
            sample["paired_net_delta_bp"] = 110.0
            sample["paired_net_delta_quote"] = 1_200.0 if index == 1 else 1_000.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["harm_quote"] == pytest.approx(200.0)
    assert costs["top1_absolute_contribution_share"] > 0.25
    assert "top1_concentration_above_25pct" in costs["gate_reasons"]
    assert result["winner_layer"] is None


@pytest.mark.parametrize("treatment_quote", [1_000.0, 900.0])
def test_randomized_quote_contrast_must_be_positive(treatment_quote):
    samples = _seven_layers()
    for sample in samples["Costs"]:
        if sample["arm"] == "control":
            sample["paired_net_delta_bp"] = 100.0
            sample["paired_net_delta_quote"] = 1_000.0
        else:
            sample["paired_net_delta_bp"] = 110.0
            sample["paired_net_delta_quote"] = treatment_quote

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert "randomized_quote_contrast_not_positive" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_randomized_quote_rates_scale_with_treatment_cluster_exposure():
    samples = _seven_layers()
    doubled = deepcopy(samples)
    for index, sample in enumerate(samples["Costs"]):
        extra = _unique_clone(sample, f"copy-{index}")
        extra["cluster_id"] = f"{sample['cluster_id']}-copy"
        doubled["Costs"].append(extra)

    base = gradient.select_unique_max_direction(samples, evidence_context=_context())
    scaled = gradient.select_unique_max_direction(
        doubled,
        evidence_context=_context(candidate_count=200, capital_days=40.0),
    )

    base_costs = base["layers"]["Costs"]
    scaled_costs = scaled["layers"]["Costs"]
    assert base_costs["eligible_frequency"] == pytest.approx(0.4)
    assert scaled_costs["eligible_frequency"] == pytest.approx(0.4)
    assert scaled_costs["harm_quote"] == pytest.approx(base_costs["harm_quote"] * 2.0)
    assert scaled_costs["harm_quote_per_100_candidates"] == pytest.approx(
        base_costs["harm_quote_per_100_candidates"]
    )
    assert scaled_costs["harm_quote_per_capital_day"] == pytest.approx(
        base_costs["harm_quote_per_capital_day"]
    )


def test_randomized_bootstrap_seed_ignores_quote_values():
    samples = _seven_layers()
    scaled = deepcopy(samples)
    for sample in scaled["Costs"]:
        sample["paired_net_delta_quote"] *= 10.0

    baseline_samples, _, _ = gradient._normalized_samples("Costs", samples["Costs"])
    scaled_samples, _, _ = gradient._normalized_samples("Costs", scaled["Costs"])
    baseline = estimators.estimate_prospective_randomized(baseline_samples)
    changed_quotes = estimators.estimate_prospective_randomized(scaled_samples)

    assert (
        baseline.paired_net_delta_bp,
        baseline.lower_95_bp,
        baseline.upper_95_bp,
    ) == pytest.approx(
        (
            changed_quotes.paired_net_delta_bp,
            changed_quotes.lower_95_bp,
            changed_quotes.upper_95_bp,
        )
    )
    assert baseline.cluster_count_by_arm == changed_quotes.cluster_count_by_arm
    assert baseline.temporal_stable == changed_quotes.temporal_stable


def test_randomized_bootstrap_seed_ignores_cluster_labels():
    samples = _samples("Costs", effect_bp=1.0, count=40)
    normalized, _, _ = gradient._normalized_samples("Costs", samples)
    renamed = deepcopy(normalized)
    labels = {
        cluster_id: f"renamed-{index}"
        for index, cluster_id in enumerate(
            reversed(sorted({sample["cluster_id"] for sample in normalized}))
        )
    }
    for sample in renamed:
        sample["cluster_id"] = labels[sample["cluster_id"]]
    baseline = estimators.estimate_prospective_randomized(normalized)
    changed = estimators.estimate_prospective_randomized(list(reversed(renamed)))

    assert baseline == changed


def test_randomized_quote_concentration_weights_exposure():
    samples = _seven_layers()
    costs = samples["Costs"]
    for sample in costs:
        if sample["arm"] == "control":
            sample["paired_net_delta_quote"] = 0.0
        else:
            sample["paired_net_delta_quote"] = 10.0
    large = next(sample for sample in costs if sample["arm"] == "treatment")
    for index in range(99):
        extra = _unique_clone(large, f"exposure-{index}")
        costs.append(extra)

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["layers"]["Costs"]["top1_absolute_contribution_share"] > 0.25
    assert result["winner_layer"] is None


def test_randomized_quote_metrics_weight_control_exposure():
    samples = _seven_layers()
    costs = samples["Costs"]
    for sample in costs:
        if sample["arm"] == "treatment":
            sample["paired_net_delta_quote"] = 10.0
        else:
            sample["paired_net_delta_quote"] = 0.0
    large = next(sample for sample in costs if sample["arm"] == "control")
    large["paired_net_delta_bp"] = 15.0
    large["paired_net_delta_quote"] = 15.0
    for index in range(99):
        extra = _unique_clone(large, f"control-exposure-{index}")
        costs.append(extra)

    normalized, _, _ = gradient._normalized_samples("Costs", costs)
    estimate = estimators.estimate_prospective_randomized(normalized)

    expected_control_mean = 1_500.0 / 119.0
    assert estimate.quote_contrast == pytest.approx(10.0 - expected_control_mean)
    assert estimate.harm_quote == pytest.approx(
        200.0 - expected_control_mean * 20.0
    )
    assert estimate.harm_quote < 0


def test_cluster_bootstrap_seed_ignores_cluster_labels():
    samples = _samples("Costs", effect_bp=1.0, count=40, with_arms=False)
    for index, sample in enumerate(samples):
        sample["cluster_id"] = f"cluster-{index // 2}"
        sample["paired_net_delta_bp"] = float(index + 1)

    normalized, _, _ = gradient._normalized_samples("Costs", samples)
    renamed = deepcopy(normalized)
    labels = {
        cluster_id: f"renamed-{index}"
        for index, cluster_id in enumerate(
            reversed(sorted({sample["cluster_id"] for sample in normalized}))
        )
    }
    for sample in renamed:
        sample["cluster_id"] = labels[sample["cluster_id"]]

    assert estimators.estimate_deterministic_decomposition(
        normalized
    ) == estimators.estimate_deterministic_decomposition(renamed)


def test_paired_bootstrap_seed_ignores_row_order_within_clusters():
    samples = _samples("Costs", effect_bp=1.0, count=12, with_arms=False)
    values = [1.0, 4.0, 8.0, 13.0, 21.0, 34.0]
    for index, sample in enumerate(samples):
        sample["cluster_id"] = f"cluster-{index // 2}"
        sample["paired_net_delta_bp"] = values[index // 2] + index % 2
        sample["paired_net_delta_quote"] = sample["paired_net_delta_bp"] / 10.0

    normalized, _, _ = gradient._normalized_samples("Costs", samples)
    reordered = list(reversed(normalized))

    baseline = estimators.estimate_paired_same_unit(normalized)
    changed_order = estimators.estimate_paired_same_unit(reordered)

    assert baseline == changed_order


def test_paired_bootstrap_seed_ignores_cluster_labels():
    samples = _samples("Costs", effect_bp=1.0, count=30, with_arms=False)
    for index, sample in enumerate(samples):
        sample["paired_net_delta_bp"] = float(index + 1)
        sample["paired_net_delta_quote"] = sample["paired_net_delta_bp"] / 10.0

    normalized, _, _ = gradient._normalized_samples("Costs", samples)
    renamed = deepcopy(normalized)
    labels = {
        cluster_id: f"renamed-{index}"
        for index, cluster_id in enumerate(
            reversed(sorted({sample["cluster_id"] for sample in normalized}))
        )
    }
    for sample in renamed:
        sample["cluster_id"] = labels[sample["cluster_id"]]

    assert estimators.estimate_paired_same_unit(
        normalized
    ) == estimators.estimate_paired_same_unit(renamed)


def test_paired_temporal_midpoint_timestamp_tie_fails_closed():
    samples = {
        layer: _samples(
            layer,
            effect_bp=10.0 if layer == "Costs" else 1.0,
            count=40,
            with_arms=False,
        )
        for layer in gradient.LAYERS
    }
    midpoint_timestamp = samples["Costs"][0]["occurred_at"]
    for sample in samples["Costs"]:
        sample["occurred_at"] = midpoint_timestamp

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert "temporal_halves_disagree" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_noncausal_methods_use_resampling_clusters_for_independence_gate():
    samples = {
        layer: _samples(layer, effect_bp=10.0, count=40, with_arms=False)
        for layer in gradient.LAYERS
    }
    for index, sample in enumerate(samples["Costs"]):
        sample["cluster_id"] = f"cluster-{index // 20}"

    for method in ("deterministic_decomposition", "directional_observational"):
        result = gradient.select_unique_max_direction(
            samples,
            evidence_context=_context(comparison_method=method),
        )

        costs = result["layers"]["Costs"]
        assert costs["independent_unit_count"] == 2
        assert "independent_units_below_30" in costs["gate_reasons"]
        assert costs["gate_status"] == "ineligible"


def test_non_finite_aggregate_evidence_is_ineligible():
    samples = _seven_layers()
    for sample in samples["Costs"]:
        sample["paired_net_delta_bp"] = 1e308
        sample["paired_net_delta_quote"] = 1e308

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["paired_net_delta_bp"] is None
    assert costs["harm_quote"] is None
    assert "non_finite_aggregate_metric" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_randomized_temporal_midpoint_timestamp_tie_fails_closed():
    samples = _seven_layers()
    costs = samples["Costs"]
    costs[20]["occurred_at"] = costs[19]["occurred_at"]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert "temporal_halves_disagree" in result["layers"]["Costs"]["gate_reasons"]
    assert result["winner_layer"] is None


def test_temporal_sign_reversal_blocks_apparent_average_effect():
    samples = _seven_layers()
    costs = samples["Costs"]
    for sample in costs[:20]:
        sample["paired_net_delta_bp"] = -6.0
        sample["paired_net_delta_quote"] = -0.6
    for sample in costs[20:]:
        sample["paired_net_delta_bp"] = 30.0
        sample["paired_net_delta_quote"] = 3.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["verdict"] == "no_unique_direction"
    assert "temporal_halves_disagree" in result["layers"]["Costs"]["gate_reasons"]


def test_null_epoch_identity_fails_closed_after_normalization():
    result = gradient.select_unique_max_direction(
        _seven_layers(),
        evidence_context=_context(behavior_fingerprints=[None]),
    )

    assert result["global_gate_status"] == "ineligible"
    assert "mixed_or_missing_behavior_fingerprints" in result["global_gate_reasons"]


def test_missing_epoch_value_fails_closed_even_when_another_value_is_present():
    result = gradient.select_unique_max_direction(
        _seven_layers(),
        evidence_context=_context(cost_model_epochs=["cost-v1", ""]),
    )

    assert result["global_gate_status"] == "ineligible"
    assert "mixed_or_missing_cost_model_epochs" in result["global_gate_reasons"]


def test_pair_level_samples_count_arms_without_double_counting_units():
    samples = _samples("Costs", effect_bp=10.0, count=30)
    for sample in samples:
        sample.update({"arm": "paired", "arm_counts": {"treatment": 1, "control": 1}})

    result = gradient.select_unique_max_direction(
        {layer: samples if layer == "Costs" else [] for layer in gradient.LAYERS},
        evidence_context=_context(),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 30
    assert costs["smaller_arm_count"] == 30


def test_one_dependent_cluster_cannot_produce_confidence_interval():
    estimate = estimators.estimate_deterministic_decomposition(
        [
            {
                "unit_id": "one",
                "cluster_id": "same-day",
                "paired_net_delta_bp": 5.0,
                "paired_net_delta_quote": 0.5,
                "occurred_at": 1.0,
                "arm": "",
                "arm_counts": None,
            },
            {
                "unit_id": "two",
                "cluster_id": "same-day",
                "paired_net_delta_bp": 15.0,
                "paired_net_delta_quote": 1.5,
                "occurred_at": 2.0,
                "arm": "",
                "arm_counts": None,
            },
        ],
    )
    assert estimate.paired_net_delta_bp == 10.0
    assert estimate.lower_95_bp is None
    assert estimate.upper_95_bp is None


def test_low_level_guards_reject_malformed_timestamps_and_inputs():
    assert gradient._timestamp("") is None
    assert gradient._timestamp("not-a-timestamp") is None
    assert gradient._timestamp("2026-08-01T00:00:00") is not None
    assert gradient._valid_stage_window(None) is False
    assert gradient._coerce_raw_samples(None) == ([], True)
    assert gradient._coerce_raw_samples("not-samples") == ([], True)
    assert gradient._coerce_raw_samples(1) == ([], True)


@pytest.mark.parametrize(
    ("layer", "ownership", "reason"),
    [
        ("Holding", {"stage": "Exit"}, "ownership_stage_mismatch"),
        (
            "Holding",
            {"stage": "holding", "state": "valid", "authorization": "none", "loss_component": "post_boundary_hold"},
            "holding_invalidity_not_proven",
        ),
        (
            "Holding",
            {"stage": "holding", "state": "horizon_expired", "authorization": "manual", "loss_component": "post_boundary_hold"},
            "holding_authorization_present",
        ),
        (
            "Holding",
            {"stage": "holding", "state": "horizon_expired", "authorization": "none", "loss_component": "wrong"},
            "holding_loss_component_invalid",
        ),
        ("Exit", {"stage": "exit", "thesis": "invalid"}, "exit_thesis_not_valid"),
        ("Exit", {"stage": "exit", "thesis": "valid", "continued_hold": "unreasonable"}, "continued_hold_not_reasonable"),
        ("Exit", {"stage": "exit", "thesis": "valid", "continued_hold": "reasonable", "failure": "other"}, "exit_protection_failure_not_proven"),
        ("Exit", {"stage": "exit", "thesis": "valid", "continued_hold": "reasonable", "failure": "protection_geometry", "loss_component": "wrong"}, "exit_loss_component_invalid"),
        ("Execution", {"stage": "execution", "decision": "not_authorized"}, "execution_exit_decision_missing"),
        ("Execution", {"stage": "execution", "decision": "exit_authorized", "loss_component": "wrong"}, "execution_loss_component_invalid"),
    ],
)
def test_ownership_evidence_rejects_each_invalid_proof(layer, ownership, reason):
    assert gradient._ownership_reason(layer, {"ownership": ownership}) == reason


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        (None, "execution_component_not_isolated"),
        ({"fees_excluded": False, "funding_excluded": True}, "execution_fees_not_excluded"),
        ({"fees_excluded": True, "funding_excluded": False}, "execution_funding_not_excluded"),
        ({"fees_excluded": True, "funding_excluded": True, "cost_event_id": "cost", "baseline_id": "base", "incremental_fill_loss_quote": 1}, "execution_cost_event_overlap"),
        ({"fees_excluded": True, "funding_excluded": True, "baseline_id": "", "incremental_fill_loss_quote": 1}, "execution_baseline_identity_missing"),
        ({"fees_excluded": True, "funding_excluded": True, "baseline_id": "base", "incremental_fill_loss_quote": -1}, "execution_fill_loss_invalid"),
    ],
)
def test_execution_evidence_is_fail_closed(evidence, reason):
    assert gradient._execution_reason({"execution_evidence": evidence}) == reason


@pytest.mark.parametrize(
    ("evidence", "reason"),
    [
        (None, "cost_baseline_unavailable"),
        ({"cost_event_id": "", "avoidable": True}, "cost_event_identity_missing"),
        ({"cost_event_id": "event", "avoidable": False}, "cost_not_avoidable"),
        ({"cost_event_id": "event", "avoidable": True, "baseline_kind": "gross"}, "cost_baseline_not_realizable"),
        ({"cost_event_id": "event", "avoidable": True, "baseline_kind": "venue", "baseline_id": ""}, "cost_baseline_identity_missing"),
        ({"cost_event_id": "event", "avoidable": True, "baseline_kind": "venue", "baseline_id": "base", "cost_model_id": ""}, "cost_model_identity_missing"),
        ({"cost_event_id": "event", "avoidable": True, "baseline_kind": "venue", "baseline_id": "base", "cost_model_id": "model", "avoidable_extra_cost_quote": -1}, "avoidable_cost_amount_invalid"),
        ({"cost_event_id": "event", "avoidable": True, "baseline_kind": "venue", "baseline_id": "base", "cost_model_id": "model", "avoidable_extra_cost_quote": 2}, "avoidable_cost_amount_mismatch"),
    ],
)
def test_cost_evidence_requires_actionable_realizable_baseline(evidence, reason):
    assert gradient._cost_reason({"cost_evidence": evidence}, 1.0) == reason


@pytest.mark.parametrize(
    ("reconciliation", "reason"),
    [
        ({"mode": "unknown", "group_id": "group", "declared_total_quote": 1, "component_quote": 1, "component_bp": 10}, "reconciliation_mode_invalid"),
        ({"mode": "exclusive", "group_id": "", "declared_total_quote": 1, "component_quote": 1, "component_bp": 10}, "reconciliation_identity_missing"),
        ({"mode": "exclusive", "group_id": "group"}, "reconciliation_identity_missing"),
        ({"mode": "exclusive", "group_id": "group", "declared_total_quote": 1, "component_quote": 1, "component_bp": 11}, "reconciliation_component_bp_mismatch"),
    ],
)
def test_reconciliation_mapping_rejects_incomplete_or_inconsistent_declarations(reconciliation, reason):
    raw = {"reconciliation": reconciliation}
    _normalized, actual_reason = gradient._normalize_reconciliation(raw, 1.0, 10.0)
    assert actual_reason == reason


def test_normalized_samples_exclude_each_malformed_boundary_shape():
    def exclusions(layer="Costs", **updates):
        sample = _samples(layer, effect_bp=10.0, count=1, with_arms=False)[0]
        sample.update(updates)
        return gradient._normalized_samples(layer, [sample])[1]

    assert "sample_not_object" in gradient._normalized_samples("Costs", [None])[1]
    assert "layer_component_mismatch" in exclusions(component="wrong")
    assert "evidence_reference_missing" in exclusions(evidence_refs=[])
    assert "stage_window_invalid" in exclusions(stage_window={})
    assert "incomplete_paired_sample" in exclusions(arm_counts="bad")
    assert "incomplete_paired_sample" in exclusions(arm_counts={"treatment": "bad", "control": 1})
    assert "incomplete_paired_sample" in exclusions(arm_counts={"treatment": 1})
    assert "attribution_identity_missing" in exclusions(unit_id="")
    assert "incomplete_paired_sample" in exclusions(paired_net_delta_bp=None)
    assert "notional_basis_invalid" in exclusions(notional_quote=0)
    assert "position_identity_missing" in exclusions(position_id="")
    assert "cost_evidence_non_cost_layer" in exclusions("Selection", cost_evidence={})

    mismatched_units = _samples("Costs", effect_bp=10.0, count=1, with_arms=False)[0]
    mismatched_units.update(
        paired_net_delta_bp=2.0,
        paired_net_delta_quote=0.1,
        reconciliation={
            "mode": "exclusive",
            "group_id": "unit-mismatch",
            "declared_total_quote": 0.1,
            "component_quote": 0.1,
            "component_bp": 2.0,
        },
    )
    assert "paired_delta_units_mismatch" in gradient._normalized_samples("Costs", [mismatched_units])[1]

    invalid_cost = _samples("Costs", effect_bp=10.0, count=1, with_arms=False)[0]
    invalid_cost["cost_evidence"]["avoidable"] = False
    invalid_cost["reconciliation"] = {
        "mode": "exclusive",
        "group_id": "cost-invalid",
        "declared_total_quote": 1.0,
        "component_quote": 1.0,
        "component_bp": 10.0,
    }
    assert "cost_not_avoidable" in gradient._normalized_samples("Costs", [invalid_cost])[1]

    duplicate = _samples("Costs", effect_bp=10.0, count=1, with_arms=False)[0]
    duplicate_copy = _unique_clone(duplicate, "duplicate")
    duplicate_copy["attribution_event_id"] = duplicate["attribution_event_id"]
    _normalized, duplicate_exclusions, _ = gradient._normalized_samples("Costs", [duplicate, duplicate_copy])
    assert "duplicate_attribution_event" in duplicate_exclusions


def test_contract_evidence_and_global_gates_cover_fail_closed_edges():
    contract_ok, contract_reasons = gradient._contract_gate({})
    assert not contract_ok
    assert contract_reasons == [
        "gradient_contract_version_mismatch",
        "reconciliation_contract_version_mismatch",
    ]
    assert gradient._evidence_gate({}) == (False, ["evidence_integrity_unverified"])
    assert "capital_days_invalid" in gradient._global_gate(_context(capital_days=0))[1]
    assert "candidate_outcome_coverage_above_1.00" in gradient._global_gate(_context(candidate_outcome_coverage=1.1))[1]


def _prepared_reconciliation_sample(group, *, mode="residual", declared=1.0, quote=0.5, bp=5.0, notional=1000.0):
    return {
        "notional_quote": notional,
        "reconciliation": {
            "group_id": group,
            "mode": mode,
            "declared_total_quote": declared,
            "component_quote": quote,
            "component_bp": bp,
            "explicit": True,
        },
    }


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        ({"mode": "residual"}, {"mode": "exclusive"}, "reconciliation_mode_mismatch"),
        ({"declared_total_quote": 1.0}, {"declared_total_quote": 2.0}, "reconciliation_declared_total_mismatch"),
        ({}, {"notional": 900.0}, "reconciliation_notional_mismatch"),
        ({"mode": "exclusive"}, {"mode": "exclusive"}, "exclusive_reconciliation_multiple_components"),
        ({"quote": float("nan")}, {}, "reconciliation_non_finite"),
        ({"quote": 0.4}, {"quote": 0.4}, "reconciliation_component_sum_mismatch"),
        ({"bp": 4.0}, {"bp": 4.0}, "reconciliation_bp_sum_mismatch"),
    ],
)
def test_reconciliation_groups_fail_closed_for_each_sum_invariant(first, second, reason):
    first_kwargs = {"group": "group", "mode": "residual", "declared": 1.0, "quote": 0.5, "bp": 5.0, "notional": 1000.0}
    second_kwargs = dict(first_kwargs)
    for key, value in first.items():
        first_kwargs[{"notional": "notional", "mode": "mode", "declared_total_quote": "declared", "quote": "quote", "bp": "bp"}.get(key, key)] = value
    for key, value in second.items():
        second_kwargs[{"notional": "notional", "mode": "mode", "declared_total_quote": "declared", "quote": "quote", "bp": "bp"}.get(key, key)] = value
    prepared = {
        "Selection": ([_prepared_reconciliation_sample(**first_kwargs)], [], 1),
        "Decision": ([_prepared_reconciliation_sample(**second_kwargs)], [], 1),
    }
    reports, invalid = gradient._reconciliation_groups(prepared)
    assert reports["group"]["reason"] == reason
    assert "group" in invalid


def test_selector_reports_shape_conflicts_unknown_layers_and_supplied_total_mismatch():
    selection = _samples("Selection", effect_bp=10.0, count=1, with_arms=False)[0]
    decision = _samples("Decision", effect_bp=10.0, count=1, with_arms=False)[0]
    decision["attribution_event_id"] = selection["attribution_event_id"]
    samples = {layer: [] for layer in gradient.LAYERS}
    samples["Selection"] = [selection]
    samples["Decision"] = [decision]
    samples["Risk"] = "malformed"
    samples.pop("Exit")
    samples["Unknown"] = []

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit", declared_net_result_quote=999.0),
    )

    assert "cross_layer_attribution_event" in result["global_gate_reasons"]
    assert "layer_samples_malformed" in result["global_gate_reasons"]
    assert "layer_samples_incomplete" in result["global_gate_reasons"]
    assert "layer_samples_unknown_layer" in result["global_gate_reasons"]
    assert "reconciliation_net_result_mismatch" in result["global_gate_reasons"]
    prepared, *_ = gradient._prepare_layers({"Risk": [None]})
    assert "sample_not_object" in prepared["Risk"][1]
    assert gradient.select_unique_max_direction([], evidence_context=None)["global_gate_status"] == "ineligible"


def test_layer_result_marks_non_finite_aggregate_metrics(monkeypatch):
    estimate = SimpleNamespace(
        paired_net_delta_bp=10.0,
        lower_95_bp=float("inf"),
        upper_95_bp=20.0,
        harm_quote=float("inf"),
        top1_absolute_contribution_share=0.1,
        top5_absolute_contribution_share=0.2,
        independent_unit_count=30,
        temporal_stable=True,
        cluster_count_by_arm=(),
        arm_counts=(),
        unassigned_arm_count=0,
        smaller_arm_count=None,
        method_reasons=(),
    )
    monkeypatch.setattr(gradient, "estimate_paired_same_unit", lambda _samples: estimate)
    result = gradient._layer_result(
        "Costs",
        [],
        prepared=([], [], 0),
        candidate_count=1,
        capital_days=1.0,
        risk_reject_count=0,
        global_ok=True,
        global_reasons=[],
        comparison_method="paired_same_unit",
        thresholds=gradient._thresholds_for("paired_same_unit"),
    )
    assert result["paired_net_delta_bp"] == 10.0
    assert result["gradient_lower_bp"] is None
    assert result["harm_quote_per_100_candidates"] is None
    assert "non_finite_aggregate_metric" in result["gate_reasons"]
