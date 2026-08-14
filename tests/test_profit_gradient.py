from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import aletheia.analytics.profit_gradient as gradient


def _context(**overrides) -> dict:
    context = {
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
        "gradient_contract_version": "profit_gradient/v2",
        "evidence_integrity": "verified",
        "comparison_method": "prospective_randomized",
    }
    context.update(overrides)
    return context


def _ownership(layer: str) -> dict:
    if layer == "Holding":
        return {
            "stage": "holding",
            "state": "hypothesis_invalidated",
            "authorization": "none",
            "loss_component": "post_boundary_hold",
        }
    if layer == "Exit":
        return {
            "stage": "exit",
            "thesis": "valid",
            "continued_hold": "reasonable",
            "failure": "protection_geometry",
            "loss_component": "protection_or_target_geometry",
        }
    if layer == "Execution":
        return {
            "stage": "execution",
            "decision": "exit_authorized",
            "loss_component": "fill_or_execution",
        }
    return {"stage": layer.lower()}


def _samples(
    layer: str,
    *,
    effect_bp: float,
    count: int = 40,
    with_arms: bool = True,
) -> list[dict]:
    fact_unit = "candidate_fact" if layer in {"Selection", "Decision", "Risk"} else "position_fact"
    economic_unit = "fixed_horizon_net_return" if fact_unit == "candidate_fact" else "verified_settled_net_economics"
    component = {
        "Selection": "candidate_selection",
        "Decision": "operator_decision",
        "Risk": "risk_policy",
        "Execution": "execution_fill",
        "Holding": "holding_authorization",
        "Exit": "exit_geometry",
        "Costs": "avoidable_extra_cost",
    }[layer]
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    stage_offsets = {"Holding": 0, "Exit": 1, "Execution": 2, "Costs": 3}
    samples = []
    for index in range(count):
        timestamp = (started + timedelta(hours=index)).isoformat()
        stage_timestamp = (
            started + timedelta(hours=index, minutes=stage_offsets.get(layer, 0))
        ).isoformat()
        sample = {
            "fact_unit": fact_unit,
            "economic_unit": economic_unit,
            "unit_id": f"{layer}-{index}",
            "harm_event_id": f"{layer}-harm-{index}",
            "attribution_event_id": f"{layer}-event-{index}",
            "position_id": f"position-{index}" if fact_unit == "position_fact" else None,
            "cluster_id": f"day-{index // 4}",
            "occurred_at": timestamp,
            "stage_window": {"start_at": stage_timestamp, "end_at": stage_timestamp},
            "component": component,
            "ownership": _ownership(layer),
            "evidence_refs": [f"{layer.lower()}-evidence-{index}"],
            "arm": (
                "baseline" if index < count // 2 else "treatment"
            ) if with_arms else "",
            "paired_net_delta_bp": effect_bp,
            "paired_net_delta_quote": effect_bp / 10.0,
            "notional_quote": 1000.0,
        }
        if layer == "Execution":
            sample["execution_evidence"] = {
                "fees_excluded": True,
                "funding_excluded": True,
                "baseline_id": f"execution-baseline-{index}",
                "incremental_fill_loss_quote": abs(effect_bp / 10.0),
            }
        if layer == "Costs":
            sample["cost_evidence"] = {
                "avoidable": True,
                "baseline_kind": "realizable_limit",
                "baseline_id": f"cost-baseline-{index}",
                "cost_event_id": f"cost-event-{index}",
                "cost_model_id": "cost-v1",
                "avoidable_extra_cost_quote": abs(effect_bp / 10.0),
            }
        samples.append(sample)
    return samples


def _seven_layers(*, winner: str = "Costs") -> dict[str, list[dict]]:
    return {layer: _samples(layer, effect_bp=10.0 if layer == winner else 1.0) for layer in gradient.LAYERS}


def test_one_separated_layer_is_unique_winner_with_normalized_harm_units():
    samples = _seven_layers()
    frozen = deepcopy(samples)

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert samples == frozen
    assert result["contract_version"] == "profit_gradient/v2"
    assert result["comparison_method"] == "prospective_randomized"
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
    assert costs["harm_quote"] == pytest.approx(40.0)
    assert costs["harm_quote_per_100_candidates"] == pytest.approx(40.0)
    assert costs["harm_quote_per_capital_day"] == pytest.approx(2.0)


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
    concentrated["Costs"][0]["paired_net_delta_quote"] = 1_000.0
    concentrated["Costs"][0]["paired_net_delta_bp"] = 10_000.0
    concentrated["Costs"][0]["cost_evidence"]["avoidable_extra_cost_quote"] = 1_000.0
    concentration_result = gradient.select_unique_max_direction(concentrated, evidence_context=_context())
    assert concentration_result["verdict"] == "no_unique_direction"
    assert "top1_concentration_above_25pct" in concentration_result["layers"]["Costs"]["gate_reasons"]


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


def test_one_dependent_cluster_cannot_produce_confidence_interval():
    estimate, lower, upper = gradient._cluster_interval(
        [
            {"cluster_id": "same-day", "paired_net_delta_bp": 5.0},
            {"cluster_id": "same-day", "paired_net_delta_bp": 15.0},
        ]
    )
    assert estimate == 10.0
    assert lower is None
    assert upper is None


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("harm_event_id", "attribution_identity_missing"),
        ("stage_window", "stage_window_invalid"),
        ("evidence_refs", "evidence_reference_missing"),
    ],
)
def test_missing_attribution_identity_fails_closed(field, reason):
    samples = _seven_layers()
    samples["Costs"][0].pop(field)

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["gate_status"] == "ineligible"
    assert reason in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_same_harm_event_within_layer_is_not_counted_twice():
    samples = _seven_layers()
    samples["Costs"][1]["harm_event_id"] = samples["Costs"][0]["harm_event_id"]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "duplicate_harm_event" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_cross_layer_conflict_survives_invalid_sample_filtering():
    samples = _seven_layers()
    shared = "same-damage-event"
    samples["Holding"][0]["harm_event_id"] = shared
    samples["Holding"][0]["ownership"]["stage"] = "wrong-stage"
    samples["Exit"][0]["harm_event_id"] = shared

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["global_gate_status"] == "ineligible"
    assert "cross_layer_harm_event" in result["global_gate_reasons"]
    assert result["cross_layer_harm_event_count"] == 1
    assert "cross_layer_harm_event" in result["layers"]["Holding"]["gate_reasons"]
    assert "cross_layer_harm_event" in result["layers"]["Exit"]["gate_reasons"]


def test_malformed_layer_input_fails_closed_without_raising():
    samples = _seven_layers()
    samples["Costs"] = None
    samples["Exit"] = [None]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["global_gate_status"] == "ineligible"
    assert "layer_samples_malformed" in result["global_gate_reasons"]
    assert result["layers"]["Exit"]["gate_status"] == "ineligible"
    assert "sample_not_object" in result["layers"]["Exit"]["gate_reasons"]


def test_same_harm_event_across_layers_is_residual_and_cannot_win():
    samples = _seven_layers()
    shared = "same-damage-event"
    for layer in ("Holding", "Exit", "Execution"):
        samples[layer][0]["harm_event_id"] = shared

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None
    assert result["attribution_integrity"] == "ineligible"
    assert result["cross_layer_harm_event_count"] == 1
    assert result["residual"] == {
        "status": "unresolved",
        "cross_layer_harm_event_count": 1,
        "cross_layer_cost_event_count": 0,
        "economic_amount": None,
        "reason": "cross_layer_harm_event",
    }
    for layer in ("Holding", "Exit", "Execution"):
        assert result["layers"][layer]["independent_unit_count"] == 39
        assert "cross_layer_harm_event" in result["layers"][layer]["gate_reasons"]


def test_same_position_can_have_distinct_stage_events_without_global_dedupe():
    samples = _seven_layers()
    samples["Holding"][0]["position_id"] = "position-shared"
    samples["Exit"][0]["position_id"] = "position-shared"
    samples["Execution"][0]["position_id"] = "position-shared"

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["attribution_integrity"] == "eligible"
    assert result["cross_layer_harm_event_count"] == 0
    assert result["layers"]["Holding"]["independent_unit_count"] == 40
    assert result["layers"]["Exit"]["independent_unit_count"] == 40
    assert result["layers"]["Execution"]["independent_unit_count"] == 40
    assert samples["Holding"][0]["stage_window"] != samples["Exit"][0]["stage_window"]
    assert samples["Exit"][0]["stage_window"] != samples["Execution"][0]["stage_window"]


def test_holding_and_exit_require_distinct_ownership_facts():
    samples = _seven_layers()
    samples["Holding"][0]["ownership"]["authorization"] = "renewed"
    samples["Exit"][0]["ownership"]["thesis"] = "invalid"

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    holding = result["layers"]["Holding"]
    exit_layer = result["layers"]["Exit"]
    assert "holding_authorization_present" in holding["gate_reasons"]
    assert "exit_thesis_not_valid" in exit_layer["gate_reasons"]
    assert result["winner_layer"] == "Costs"


def test_unavoidable_or_frictionless_cost_evidence_cannot_be_cost_winner():
    samples = _seven_layers()
    for sample in samples["Costs"]:
        sample["cost_evidence"].update(
            {
                "avoidable": False,
                "baseline_kind": "frictionless_request",
            }
        )

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 0
    assert "cost_not_avoidable" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_cost_baseline_must_be_realizable_and_identity_complete():
    samples = _seven_layers()
    for sample in samples["Costs"]:
        sample["cost_evidence"]["baseline_kind"] = "frictionless_request"

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 0
    assert "cost_baseline_not_realizable" in costs["gate_reasons"]


def test_duplicate_attribution_event_is_not_counted_twice():
    samples = _seven_layers()
    samples["Costs"][1]["attribution_event_id"] = samples["Costs"][0]["attribution_event_id"]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "duplicate_attribution_event" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_duplicate_cost_event_is_not_counted_twice():
    samples = _seven_layers()
    samples["Costs"][1]["cost_evidence"]["cost_event_id"] = samples["Costs"][0]["cost_evidence"]["cost_event_id"]

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "duplicate_cost_event" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_cost_event_cannot_cross_execution_and_cost_layers():
    samples = _seven_layers()
    samples["Execution"][0]["cost_evidence"] = deepcopy(samples["Costs"][0]["cost_evidence"])
    samples["Execution"][0]["harm_event_id"] = "execution-harm-distinct"

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert result["winner_layer"] is None
    assert result["cross_layer_cost_event_count"] == 1
    assert "cross_layer_cost_event" in result["global_gate_reasons"]
    assert "cost_evidence_non_cost_layer" in result["layers"]["Execution"]["exclusion_waterfall"]


def test_paired_delta_units_must_reconcile():
    samples = _seven_layers()
    samples["Costs"][0]["paired_net_delta_quote"] = 2.0

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "paired_delta_units_mismatch" in costs["gate_reasons"]
    assert result["winner_layer"] is None


def test_cost_evidence_amount_must_bind_to_paired_delta():
    samples = _seven_layers()
    samples["Costs"][0]["cost_evidence"]["avoidable_extra_cost_quote"] = 0.01

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    assert "avoidable_cost_amount_mismatch" in result["layers"]["Costs"]["gate_reasons"]
    assert result["winner_layer"] is None


def test_execution_evidence_must_exclude_fees_and_funding():
    samples = _seven_layers()
    for sample in samples["Execution"]:
        sample["execution_evidence"]["fees_excluded"] = False

    result = gradient.select_unique_max_direction(samples, evidence_context=_context())

    execution = result["layers"]["Execution"]
    assert execution["independent_unit_count"] == 0
    assert "execution_fees_not_excluded" in execution["gate_reasons"]


def test_paired_same_unit_does_not_require_arms_but_keeps_unique_units():
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

    assert result["winner_layer"] == "Costs"
    assert result["layers"]["Costs"]["smaller_arm_count"] is None
    assert "smaller_arm_below_20" not in result["layers"]["Costs"]["gate_reasons"]


def test_noncausal_comparison_methods_never_produce_a_winner():
    result = gradient.select_unique_max_direction(
        _seven_layers(),
        evidence_context=_context(comparison_method="deterministic_decomposition"),
    )

    assert result["unique_winner_eligible"] is False
    assert result["winner_layer"] is None
    assert result["layers"]["Costs"]["gate_status"] == "eligible"
    assert result["next_action"] == "retain_measured_fact_no_behavior_promotion"


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


def test_epoch_gate_rejects_missing_epoch_value():
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(policy_epochs=["policy-v1", ""])
    )

    assert result["global_gate_status"] == "ineligible"
    assert "mixed_or_missing_policy_epochs" in result["global_gate_reasons"]


def test_epoch_gate_rejects_none_epoch_value():
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(policy_epochs=[None])
    )

    assert result["global_gate_status"] == "ineligible"
    assert "mixed_or_missing_policy_epochs" in result["global_gate_reasons"]


def test_v1_evidence_cannot_be_reinterpreted_as_v2():
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(gradient_contract_version="profit_gradient/v1")
    )

    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None
    assert "gradient_contract_version_mismatch" in result["global_gate_reasons"]


def test_malformed_top_level_input_fails_closed_without_raising():
    result = gradient.select_unique_max_direction(None, evidence_context=None)

    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None
    assert result["global_gate_status"] == "ineligible"


def test_unverified_evidence_cannot_produce_a_winner():
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(evidence_integrity="observational")
    )

    assert result["global_gate_status"] == "ineligible"
    assert result["winner_layer"] is None
    assert "evidence_integrity_unverified" in result["global_gate_reasons"]


def test_invalid_denominators_cannot_produce_a_winner():
    result = gradient.select_unique_max_direction(
        _seven_layers(), evidence_context=_context(candidate_count=0, capital_days=0)
    )

    assert result["global_gate_status"] == "ineligible"
    assert result["winner_layer"] is None
    assert "candidate_count_invalid" in result["global_gate_reasons"]
    assert "capital_days_invalid" in result["global_gate_reasons"]
