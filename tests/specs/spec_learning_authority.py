"""Executable authority limits for factor and universe Learning paths."""

from __future__ import annotations


import pytest


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_numeric_treatment_rejects_non_finite_values(invalid):
    from extensions.learning.experiments import apply_numeric_treatment

    with pytest.raises(ValueError, match="must be finite"):
        apply_numeric_treatment(
            path_id="universe_family_calibration",
            scope={"family": "momentum", "direction": "long", "market_type": "perp"},
            action={"type": "opportunity_multiplier", "multiplier": 1.1},
            source_cohort="universe_forward_outcomes:clean_net:v2",
            target_horizon="4h",
            candidate_key="non-finite-candidate",
            baseline=0.0,
            treatment=invalid,
            consumer="universe_allocator",
            consumer_stage="opportunity_score",
        )
