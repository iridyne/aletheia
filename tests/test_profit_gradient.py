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
        "comparison_method": "prospective_randomized",
    }
    context.update(overrides)
    return context


def _samples(
    layer: str, *, effect_bp: float, count: int = 40, with_arms: bool = True
) -> list[dict]:
    fact_unit = "candidate_fact" if layer in {"Selection", "Decision", "Risk"} else "position_fact"
    economic_unit = "fixed_horizon_net_return" if fact_unit == "candidate_fact" else "verified_settled_net_economics"
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [
        {
            "fact_unit": fact_unit,
            "economic_unit": economic_unit,
            "unit_id": f"{layer}-{index}",
            "cluster_id": f"day-{index // 4}",
            "occurred_at": (started + timedelta(hours=index)).isoformat(),
            "arm": (
                "baseline" if index < count // 2 else "treatment"
            ) if with_arms else "",
            "paired_net_delta_bp": effect_bp,
            "paired_net_delta_quote": effect_bp / 10.0,
        }
        for index in range(count)
    ]


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


def test_prospective_randomized_rejects_unassigned_units():
    samples = _seven_layers()
    unassigned = _samples("Costs", effect_bp=10.0, count=1)[0]
    unassigned["unit_id"] = "Costs-unassigned"
    unassigned["arm"] = ""
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
    samples["Costs"][0]["paired_net_delta_quote"] = -1.0

    result = gradient.select_unique_max_direction(
        samples,
        evidence_context=_context(comparison_method="paired_same_unit"),
    )

    costs = result["layers"]["Costs"]
    assert costs["independent_unit_count"] == 39
    assert "paired_delta_sign_mismatch" in costs["gate_reasons"]
    assert result["winner_layer"] is None


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
