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
    }
    context.update(overrides)
    return context


def _samples(layer: str, *, effect_bp: float, count: int = 40) -> list[dict]:
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
            "arm": "baseline" if index < count // 2 else "treatment",
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
    assert result["contract_version"] == "profit_gradient/v1"
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
    estimate, lower, upper = gradient._cluster_interval(
        [
            {"cluster_id": "same-day", "paired_net_delta_bp": 5.0},
            {"cluster_id": "same-day", "paired_net_delta_bp": 15.0},
        ]
    )
    assert estimate == 10.0
    assert lower is None
    assert upper is None
