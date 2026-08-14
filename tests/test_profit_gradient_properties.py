from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from hypothesis import given, settings, strategies as st
import pytest

import aletheia.analytics.profit_gradient as gradient
import aletheia.analytics.profit_gradient_estimators as estimators


_BOOTSTRAP_ITERATIONS = 64
_ESTIMATORS = (
    estimators.estimate_prospective_randomized,
    estimators.estimate_paired_same_unit,
    estimators.estimate_deterministic_decomposition,
)


def _context(method: str = "prospective_randomized") -> dict:
    return {
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
        "comparison_method": method,
        "assignment_salts": ["salt-v1"],
        "horizons": ["24h"],
        "outcome_contract_versions": ["outcome-v1"],
        "metric_versions": ["metric-v1"],
        "statistical_unit_versions": ["unit-v1"],
        "stages": ["paper_10"],
    }


def _cost_rows(
    values: list[tuple[float, float, str]],
    *,
    cluster_ids: list[str] | None = None,
) -> list[dict]:
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    return [
        {
            "fact_unit": "position_fact",
            "economic_unit": "verified_settled_net_economics",
            "unit_id": f"unit-{index}",
            "cluster_id": (
                cluster_ids[index] if cluster_ids is not None else f"cluster-{index}"
            ),
            "occurred_at": (started + timedelta(hours=index)).isoformat(),
            "arm": arm,
            "paired_net_delta_bp": bp,
            "paired_net_delta_quote": quote,
        }
        for index, (bp, quote, arm) in enumerate(values)
    ]


def _normalized(rows: list[dict]) -> list[dict]:
    samples, exclusions, raw_count = gradient._normalized_samples("Costs", rows)
    assert exclusions == []
    assert raw_count == len(samples)
    return samples


def _select(costs: list[dict], method: str = "prospective_randomized") -> dict:
    return gradient.select_unique_max_direction(
        {layer: costs if layer == "Costs" else [] for layer in gradient.LAYERS},
        evidence_context=_context(method),
    )


@settings(max_examples=15, deadline=None)
@given(
    data=st.data(),
    values=st.lists(st.integers(min_value=-50, max_value=50), min_size=8, max_size=8),
)
def test_estimators_are_invariant_to_input_permutation(data, values):
    rows = _cost_rows(
        [
            (float(value), float(value) / 10.0, "control" if index % 2 == 0 else "treatment")
            for index, value in enumerate(values)
        ]
    )
    samples = _normalized(rows)
    frozen = deepcopy(samples)
    order = data.draw(st.permutations(tuple(range(len(samples)))))
    permuted = [samples[index] for index in order]

    for estimator in _ESTIMATORS:
        assert estimator(
            samples, iterations=_BOOTSTRAP_ITERATIONS
        ) == estimator(permuted, iterations=_BOOTSTRAP_ITERATIONS)
    assert samples == frozen


@pytest.mark.parametrize(
    "method",
    [
        "prospective_randomized",
        "paired_same_unit",
        "deterministic_decomposition",
        "directional_observational",
    ],
)
@settings(max_examples=5, deadline=None)
@given(order=st.permutations(tuple(range(40))))
def test_selector_gates_and_verdict_are_invariant_to_input_permutation(method, order):
    rows = _cost_rows(
        [
            (
                10.0 if index % 2 else 0.0,
                1.0 if index % 2 else 0.0,
                "treatment" if index % 2 else "control",
            )
            for index in range(40)
        ]
    )
    permuted = [rows[index] for index in order]

    assert _select(rows, method) == _select(permuted, method)


@settings(max_examples=15, deadline=None)
@given(labels=st.permutations(tuple(range(8))))
def test_estimators_are_invariant_to_bijective_cluster_renaming(labels):
    samples = _normalized(
        _cost_rows(
            [
                (
                    float(index + 1),
                    float(index + 1) / 10.0,
                    "control" if index % 2 == 0 else "treatment",
                )
                for index in range(8)
            ]
        )
    )
    renamed = deepcopy(samples)
    mapping = {
        f"cluster-{index}": f"renamed-{labels[index]}" for index in range(8)
    }
    for sample in renamed:
        sample["cluster_id"] = mapping[sample["cluster_id"]]

    for estimator in _ESTIMATORS:
        assert estimator(
            samples, iterations=_BOOTSTRAP_ITERATIONS
        ) == estimator(renamed, iterations=_BOOTSTRAP_ITERATIONS)


@pytest.mark.parametrize(
    "method",
    [
        "prospective_randomized",
        "paired_same_unit",
        "deterministic_decomposition",
        "directional_observational",
    ],
)
@settings(max_examples=5, deadline=None)
@given(effect=st.floats(min_value=6.0, max_value=50.0, allow_nan=False, allow_infinity=False))
def test_timestamp_tie_at_chronological_midpoint_fails_closed(method, effect):
    rows = _cost_rows(
        [
            (
                10.0 + (effect if index % 2 else 0.0),
                1.0 + (effect / 10.0 if index % 2 else 0.0),
                "treatment" if index % 2 else "control",
            )
            for index in range(40)
        ]
    )
    rows[20]["occurred_at"] = rows[19]["occurred_at"]

    result = _select(rows, method)

    assert result["layers"]["Costs"]["gate_status"] == "ineligible"
    assert "temporal_halves_disagree" in result["layers"]["Costs"]["gate_reasons"]
    assert result["winner_layer"] is None


@pytest.mark.parametrize("estimator", _ESTIMATORS)
@settings(max_examples=15, deadline=None)
@given(scale=st.floats(min_value=0.25, max_value=10.0, allow_nan=False, allow_infinity=False))
def test_positive_quote_scaling_preserves_bp_and_concentration(estimator, scale):
    samples = _normalized(
        _cost_rows(
            [
                (
                    20.0 if index % 2 else 10.0,
                    2.0 if index % 2 else 1.0,
                    "treatment" if index % 2 else "control",
                )
                for index in range(40)
            ]
        )
    )
    scaled = deepcopy(samples)
    for sample in scaled:
        sample["paired_net_delta_quote"] *= scale

    baseline = estimator(samples, iterations=_BOOTSTRAP_ITERATIONS)
    changed = estimator(scaled, iterations=_BOOTSTRAP_ITERATIONS)

    assert (
        baseline.paired_net_delta_bp,
        baseline.lower_95_bp,
        baseline.upper_95_bp,
    ) == (
        changed.paired_net_delta_bp,
        changed.lower_95_bp,
        changed.upper_95_bp,
    )
    assert changed.harm_quote == pytest.approx(baseline.harm_quote * scale)
    if baseline.quote_contrast is not None:
        assert changed.quote_contrast == pytest.approx(
            baseline.quote_contrast * scale
        )
    assert changed.top1_absolute_contribution_share == pytest.approx(
        baseline.top1_absolute_contribution_share
    )
    assert changed.top5_absolute_contribution_share == pytest.approx(
        baseline.top5_absolute_contribution_share
    )
    assert changed.method_reasons == baseline.method_reasons


@pytest.mark.parametrize(
    "method",
    [
        "prospective_randomized",
        "paired_same_unit",
        "deterministic_decomposition",
        "directional_observational",
    ],
)
@settings(max_examples=4, deadline=None)
@given(scale=st.floats(min_value=0.25, max_value=10.0, allow_nan=False, allow_infinity=False))
def test_positive_quote_scaling_preserves_selector_verdict(method, scale):
    rows = _cost_rows(
        [
            (
                20.0 if index % 2 else 10.0,
                2.0 if index % 2 else 1.0,
                "treatment" if index % 2 else "control",
            )
            for index in range(40)
        ]
    )
    scaled = deepcopy(rows)
    for sample in scaled:
        sample["paired_net_delta_quote"] *= scale

    baseline = _select(rows, method)
    changed = _select(scaled, method)

    assert changed["verdict"] == baseline["verdict"]
    assert changed["winner_layer"] == baseline["winner_layer"]
    assert changed["layers"]["Costs"]["gate_reasons"] == baseline["layers"]["Costs"]["gate_reasons"]


@settings(max_examples=15, deadline=None)
@given(
    translation=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    scale=st.floats(min_value=0.25, max_value=10.0, allow_nan=False, allow_infinity=False),
)
def test_randomized_centered_concentration_is_affine_invariant(translation, scale):
    samples = _normalized(
        _cost_rows(
            [
                (
                    20.0 if index % 2 else 10.0,
                    120.0 + index % 5 if index % 2 else 100.0 + index % 3,
                    "treatment" if index % 2 else "control",
                )
                for index in range(40)
            ]
        )
    )
    transformed = deepcopy(samples)
    for sample in transformed:
        sample["paired_net_delta_quote"] = (
            sample["paired_net_delta_quote"] + translation
        ) * scale

    baseline = estimators.estimate_prospective_randomized(
        samples, iterations=_BOOTSTRAP_ITERATIONS
    )
    changed = estimators.estimate_prospective_randomized(
        transformed, iterations=_BOOTSTRAP_ITERATIONS
    )

    assert changed.top1_absolute_contribution_share == pytest.approx(
        baseline.top1_absolute_contribution_share, rel=1e-10, abs=1e-12
    )
    assert changed.top5_absolute_contribution_share == pytest.approx(
        baseline.top5_absolute_contribution_share, rel=1e-10, abs=1e-12
    )
    assert changed.harm_quote == pytest.approx(
        baseline.harm_quote * scale, rel=1e-10, abs=1e-10
    )


@settings(max_examples=10, deadline=None)
@given(values=st.lists(st.integers(min_value=1, max_value=100), min_size=20, max_size=20))
def test_equal_randomized_arm_cluster_means_cannot_confirm_a_winner(values):
    rows = []
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    for index, value in enumerate(values):
        for arm_index, arm in enumerate(("control", "treatment")):
            rows.append(
                {
                    "fact_unit": "position_fact",
                    "economic_unit": "verified_settled_net_economics",
                    "unit_id": f"{arm}-{index}",
                    "cluster_id": f"{arm}-{index}",
                    "occurred_at": (
                        started + timedelta(hours=index * 2 + arm_index)
                    ).isoformat(),
                    "arm": arm,
                    "paired_net_delta_bp": float(value),
                    "paired_net_delta_quote": float(value) / 10.0,
                }
            )

    estimate = estimators.estimate_prospective_randomized(
        _normalized(rows), iterations=_BOOTSTRAP_ITERATIONS
    )
    result = _select(rows)

    assert estimate.paired_net_delta_bp == pytest.approx(0.0)
    assert result["layers"]["Costs"]["paired_net_delta_bp"] == pytest.approx(0.0)
    assert result["verdict"] == "no_unique_direction"
    assert result["winner_layer"] is None


@st.composite
def _nonpositive_randomized_quote_contrast(draw):
    control = draw(st.integers(min_value=2, max_value=100))
    treatment = draw(st.integers(min_value=1, max_value=control))
    return float(control), float(treatment)


@settings(max_examples=10, deadline=None)
@given(quotes=_nonpositive_randomized_quote_contrast())
def test_positive_randomized_bp_with_nonpositive_quote_harm_is_ineligible(quotes):
    control_quote, treatment_quote = quotes
    rows = _cost_rows(
        [
            (
                20.0 if index % 2 else 10.0,
                treatment_quote if index % 2 else control_quote,
                "treatment" if index % 2 else "control",
            )
            for index in range(40)
        ]
    )

    estimate = estimators.estimate_prospective_randomized(
        _normalized(rows), iterations=_BOOTSTRAP_ITERATIONS
    )
    result = _select(rows)
    costs = result["layers"]["Costs"]

    assert estimate.paired_net_delta_bp > 0
    assert "randomized_quote_contrast_not_positive" in estimate.method_reasons
    assert "randomized_quote_harm_not_positive" in estimate.method_reasons
    assert costs["gate_status"] == "ineligible"
    assert result["winner_layer"] is None


@settings(max_examples=10, deadline=None)
@given(negative_quote=st.floats(min_value=4.0, max_value=20.0, allow_nan=False, allow_infinity=False))
def test_positive_paired_bp_with_aggregate_monetary_harm_is_ineligible(negative_quote):
    negative_indices = {*range(5), *range(20, 25)}
    rows = _cost_rows(
        [
            (-1.0, -negative_quote, "")
            if index in negative_indices
            else (10.0, 1.0, "")
            for index in range(40)
        ]
    )

    estimate = estimators.estimate_paired_same_unit(
        _normalized(rows), iterations=_BOOTSTRAP_ITERATIONS
    )
    result = _select(rows, "paired_same_unit")

    assert estimate.paired_net_delta_bp > 5.0
    assert estimate.harm_quote < 0
    assert "paired_quote_harm_not_positive" in estimate.method_reasons
    assert result["layers"]["Costs"]["gate_status"] == "ineligible"
    assert result["winner_layer"] is None


@pytest.mark.parametrize(
    ("method", "estimator"),
    [
        ("prospective_randomized", estimators.estimate_prospective_randomized),
        ("paired_same_unit", estimators.estimate_paired_same_unit),
        (
            "deterministic_decomposition",
            estimators.estimate_deterministic_decomposition,
        ),
        ("directional_observational", estimators.estimate_deterministic_decomposition),
    ],
)
def test_finite_inputs_with_non_finite_aggregates_fail_closed(method, estimator):
    rows = _cost_rows(
        [
            (1e308, 1e308, "treatment" if index % 2 else "control")
            for index in range(40)
        ]
    )

    estimate = estimator(_normalized(rows), iterations=_BOOTSTRAP_ITERATIONS)
    result = _select(rows, method)
    costs = result["layers"]["Costs"]

    for field in (
        "paired_net_delta_bp",
        "lower_95_bp",
        "upper_95_bp",
        "harm_quote",
        "top1_absolute_contribution_share",
        "top5_absolute_contribution_share",
    ):
        assert getattr(estimate, field) is None
        assert costs[field] is None
    assert "non_finite_aggregate_metric" in estimate.method_reasons
    assert "non_finite_aggregate_metric" in costs["gate_reasons"]
    assert "temporal_halves_disagree" in costs["gate_reasons"]
    if method == "prospective_randomized":
        assert "randomized_quote_contrast_not_positive" in costs["gate_reasons"]
        assert "randomized_quote_harm_not_positive" in costs["gate_reasons"]
    elif method == "paired_same_unit":
        assert "paired_quote_harm_not_positive" in costs["gate_reasons"]
    assert costs["gate_status"] == "ineligible"
    assert result["winner_layer"] is None
    json.dumps(result, allow_nan=False)
