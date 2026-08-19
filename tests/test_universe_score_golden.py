"""Golden contract for the legacy Universe piecewise/step score path.

This suite intentionally does not generate or update its own expectations.  A
behavior change must use a new, explicitly reviewed fixture/version instead of
silently teaching the test the new output.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.universe.allocator import (
    compute_late_entry_penalty,
    compute_total_score,
)
from aletheia.universe.filters import _compute_tradability_score
from aletheia.universe.scanners import (
    UNIVERSE_SCORER_BEHAVIOR_VERSION,
    run_all_scanners,
    score_derivatives,
    score_event,
    score_mean_reversion,
    score_momentum,
    score_short_bias,
    score_volatility,
)


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "universe_step_score_v3.json"
_SCANNERS = {
    "momentum": score_momentum,
    "mean_reversion": score_mean_reversion,
    "volatility": score_volatility,
    "derivatives": score_derivatives,
    "event": score_event,
    "short_bias": score_short_bias,
}


@pytest.fixture(scope="module")
def golden() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _assert_number(actual: float, expected: float) -> None:
    assert actual == pytest.approx(expected, abs=1e-9)


def test_golden_contract_identity_and_inventory(golden: dict) -> None:
    """The fixture must identify the exact frozen scorer and its full inventory."""
    assert golden["contract"] == "universe-step-score-golden/v3"
    assert golden["scorer_behavior_version"] == UNIVERSE_SCORER_BEHAVIOR_VERSION
    assert UNIVERSE_SCORER_BEHAVIOR_VERSION == "universe-step-v3"

    coverage = golden["coverage"]
    assert coverage["real_vector_count"] == len(golden["real_vectors"]) == 6
    assert coverage["threshold_group_count"] == len(golden["threshold_probes"])
    assert coverage["threshold_case_count"] == sum(
        len(group["values"]) for group in golden["threshold_probes"]
    )
    assert coverage["edge_case_count"] == len(golden["edge_cases"])
    assert coverage["threshold_group_count"] >= 100
    assert coverage["threshold_case_count"] >= 300

    covered_components = {
        group["component"] for group in golden["threshold_probes"]
    }
    assert covered_components == {
        *_SCANNERS,
        "run_all",
        "tradability",
        "late_entry",
        "total_score",
    }


def test_golden_fixture_contains_no_runtime_identity(golden: dict) -> None:
    """Only sanitized numeric shapes may cross the runtime/fixture boundary."""
    forbidden = {
        "asset_id",
        "feature_snapshot_id",
        "snapshot_id",
        "symbol",
        "venue",
        "entry_price_usd",
        "raw",
    }

    def visit(value: object) -> None:
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(golden["neutral_features"])
    visit(golden["real_vectors"])
    visit(golden["threshold_probes"])
    visit(golden["edge_cases"])


@pytest.mark.parametrize("vector_index", range(6))
def test_fedora_derived_real_vector_baseline(golden: dict, vector_index: int) -> None:
    """Persisted Fedora Paper feature shapes retain exact local scorer outputs."""
    case = golden["real_vectors"][vector_index]
    feat = {**golden["neutral_features"], **case["features"]}

    score, reasons, direction = _SCANNERS[case["source_family"]](dict(feat))
    expected_family = case["expected_family_result"]
    _assert_number(score, expected_family["score"])
    assert direction == expected_family["direction"]
    assert reasons == expected_family["reasons"]

    winner = run_all_scanners(dict(feat))
    expected_winner = case["expected_winner"]
    _assert_number(winner["opportunity_score"], expected_winner["score"])
    assert winner["scanner_family"] == expected_winner["family"]
    assert winner["scanner_direction"] == expected_winner["direction"]
    assert winner["reasons"] == expected_winner["reasons"]
    assert winner["is_proxy"] is expected_winner["is_proxy"]
    assert winner["all_scores"] == pytest.approx(
        expected_winner["all_scores"], abs=1e-9
    )


def test_all_threshold_neighborhoods_match_frozen_outputs(golden: dict) -> None:
    """Every below/at/above probe must preserve the current discontinuities."""
    cfg = UniverseConfig()
    neutral = golden["neutral_features"]

    for group in golden["threshold_probes"]:
        values = group["values"]
        expected_values = group["expected"]
        assert len(values) == len(expected_values) == 3, group["name"]
        assert values == sorted(values), group["name"]

        for value, expected in zip(values, expected_values, strict=True):
            feat = {
                **neutral,
                **group["base_overrides"],
                group["feature"]: value,
            }
            component = group["component"]
            context = f"{group['name']} value={value!r}"

            if component in _SCANNERS:
                score, _reasons, direction = _SCANNERS[component](feat)
                assert score == pytest.approx(expected["score"], abs=1e-9), context
                assert direction == expected["direction"], context
            elif component == "run_all":
                result = run_all_scanners(feat)
                assert result["opportunity_score"] == pytest.approx(
                    expected["score"], abs=1e-9
                ), context
                assert result["scanner_direction"] == expected["direction"], context
                assert result["scanner_family"] == expected["family"], context
            elif component == "tradability":
                actual = _compute_tradability_score(feat, cfg)
                assert actual == pytest.approx(expected, abs=1e-9), context
            elif component == "late_entry":
                actual = compute_late_entry_penalty(feat)
                assert actual == pytest.approx(expected, abs=1e-9), context
            elif component == "total_score":
                scanner_result = {
                    "opportunity_score": 50.0,
                    "scanner_family": "momentum",
                }
                actual = compute_total_score(feat, scanner_result, cfg)
                assert actual == pytest.approx(expected, abs=1e-9), context
            else:  # pragma: no cover - fixture schema guard
                pytest.fail(f"unknown component {component!r} in {context}")


def test_categorical_and_missing_semantics_match_frozen_outputs(golden: dict) -> None:
    """Freeze non-numeric branches that the later curve design must define."""
    cfg = UniverseConfig()
    neutral = golden["neutral_features"]

    for case in golden["edge_cases"]:
        feat = {**neutral, **case["feature_overrides"]}
        component = case["component"]
        expected = case["expected"]
        context = case["name"]

        if component in _SCANNERS:
            score, reasons, direction = _SCANNERS[component](feat)
            assert score == pytest.approx(expected["score"], abs=1e-9), context
            assert direction == expected["direction"], context
            assert reasons == expected["reasons"], context
        elif component == "tradability":
            actual = _compute_tradability_score(feat, cfg)
            assert actual == pytest.approx(expected, abs=1e-9), context
        elif component == "total_score":
            scanner_result = {
                "opportunity_score": 50.0,
                "scanner_family": "momentum",
            }
            actual = compute_total_score(feat, scanner_result, cfg)
            assert actual == pytest.approx(expected, abs=1e-9), context
        else:  # pragma: no cover - fixture schema guard
            pytest.fail(f"unknown edge component {component!r} in {context}")
