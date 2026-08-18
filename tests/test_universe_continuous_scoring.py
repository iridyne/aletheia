"""Pure-contract tests for the non-authoritative continuous Universe scorer."""

from __future__ import annotations

import ast
import copy
import inspect
import math
from pathlib import Path
import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aletheia.universe import continuous_scoring as scoring
from aletheia.universe.continuous_scoring import (
    CONTINUOUS_SCORER_BEHAVIOR_VERSION,
    ContinuousScoringPolicy,
    evaluate_continuous_candidate,
    raw_feature_declarations,
    score_continuous_family,
    shape_preserving_curve,
)


def _complete_candidate(**overrides: object) -> dict[str, object]:
    feat: dict[str, object] = {
        "market_type": "perp",
        "rs_beta_adj_24h": None,
        "relative_strength_24h": 4.0,
        "relative_strength_4h": 0.0,
        "return_1h": 1.0,
        "return_4h": 2.0,
        "return_24h": 0.0,
        "rsi_14": 50.0,
        "rsi_4h": 50.0,
        "atr_pct": 2.0,
        "atr_4h": 2.0,
        "bb_squeeze_ratio": 1.0,
        "bb_width_pct": 4.0,
        "bb_width_4h": 6.0,
        "vol_ratio": 1.5,
        "quote_volume_24h": 10_000_000.0,
        "spread_bps": 10.0,
        "depth_10bps_usd": 25_000.0,
        "funding_rate": 0.0,
        "oi_delta_1h": 0.0,
        "taker_buy_ratio": 1.0,
        "data_freshness_sec": 300.0,
    }
    feat.update(overrides)
    return feat


def test_behavior_identity_is_reserved_shadow_version() -> None:
    assert CONTINUOUS_SCORER_BEHAVIOR_VERSION == "universe-continuous-shadow-v1"


def test_shape_preserving_curve_is_exact_clamped_and_bounded() -> None:
    anchors = ((0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0))
    assert shape_preserving_curve(-100.0, anchors) == 0.0
    assert shape_preserving_curve(100.0, anchors) == 0.0
    for x_value, y_value in anchors:
        assert shape_preserving_curve(x_value, anchors) == y_value
    samples = [shape_preserving_curve(index / 100.0 * 3.0, anchors) for index in range(101)]
    assert min(samples) >= 0.0
    assert max(samples) <= 1.0
    assert shape_preserving_curve(1.0 - 1e-9, anchors) == pytest.approx(
        shape_preserving_curve(1.0 + 1e-9, anchors), abs=1e-8
    )


@given(
    base=st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    increments=st.tuples(
        st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
        st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    ),
    value=st.floats(min_value=0.0, max_value=3.0, allow_nan=False, allow_infinity=False),
)
def test_monotone_curve_property_never_overshoots(
    base: float,
    increments: tuple[float, float, float],
    value: float,
) -> None:
    y_values = (
        base,
        base + increments[0],
        base + increments[0] + increments[1],
        base + sum(increments),
    )
    anchors = tuple((float(index), y_value) for index, y_value in enumerate(y_values))
    result = shape_preserving_curve(value, anchors)
    interval = min(int(math.floor(value)), 2)
    assert y_values[interval] - 1e-9 <= result <= y_values[interval + 1] + 1e-9


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "1", True])
def test_shape_preserving_curve_rejects_invalid_values(bad: object) -> None:
    with pytest.raises(scoring.InvalidFeature):
        shape_preserving_curve(bad, ((0.0, 0.0), (1.0, 1.0)))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "anchors",
    [(), ((0.0, 0.0),), ((0.0, 0.0), (0.0, 1.0)), ((0.0, 0.0), (math.inf, 1.0))],
)
def test_shape_preserving_curve_rejects_invalid_anchors(anchors: tuple) -> None:
    with pytest.raises(ValueError):
        shape_preserving_curve(0.5, anchors)


def test_momentum_observed_zero_does_not_trigger_fallback() -> None:
    result = score_continuous_family(
        "momentum",
        {
            "rs_beta_adj_24h": 0.0,
            "relative_strength_24h": 6.0,
            "return_4h": 4.0,
        },
    )
    assert result.valid is True
    assert result.direction is None
    assert result.point_score == 0.0
    primary = next(item for item in result.evidence if item.curve_id == "MOM-01")
    assert primary.raw_feature_id == "rs_beta_adj_24h"
    assert primary.observed is True


def test_momentum_long_short_symmetry_at_anchor() -> None:
    neutral = {
        "relative_strength_4h": 0.0,
        "return_4h": 0.0,
        "return_24h": 0.0,
        "rsi_14": 50.0,
        "rsi_4h": 50.0,
        "vol_ratio": 1.0,
        "taker_buy_ratio": 1.0,
        "oi_delta_1h": 0.0,
        "funding_rate": 0.0,
    }
    long = score_continuous_family("momentum", {**neutral, "relative_strength_24h": 4.0})
    short = score_continuous_family("momentum", {**neutral, "relative_strength_24h": -4.0})
    assert (long.point_score, long.direction) == (20.0, "long")
    assert (short.point_score, short.direction) == (20.0, "short")
    assert long.pessimistic_score == short.pessimistic_score == 20.0


@given(
    magnitude=st.floats(
        min_value=0.0,
        max_value=8.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_momentum_long_short_symmetry_property(magnitude: float) -> None:
    neutral = {
        "relative_strength_4h": 0.0,
        "return_4h": 0.0,
        "return_24h": 0.0,
        "rsi_14": 50.0,
        "rsi_4h": 50.0,
        "vol_ratio": 1.0,
        "taker_buy_ratio": 1.0,
        "oi_delta_1h": 0.0,
        "funding_rate": 0.0,
    }
    long = score_continuous_family("momentum", {**neutral, "relative_strength_24h": magnitude})
    short = score_continuous_family("momentum", {**neutral, "relative_strength_24h": -magnitude})
    assert long.point_score == pytest.approx(short.point_score, abs=1e-9)
    assert long.pessimistic_score == pytest.approx(short.pessimistic_score, abs=1e-9)


@pytest.mark.parametrize(
    ("family", "feat", "expected_direction", "expected_point"),
    [
        ("mean_reversion", {"rsi_14": 80.0}, "short", 35.0),
        (
            "volatility",
            {
                "return_4h": 3.0,
                "atr_pct": 5.0,
                "bb_squeeze_ratio": 1.0,
                "vol_ratio": 1.5,
                "taker_buy_ratio": 1.0,
            },
            "long",
            30.0,
        ),
        (
            "derivatives",
            {"market_type": "perp", "funding_rate": 0.00010},
            "short",
            40.0,
        ),
        (
            "event",
            {"return_1h": 4.0, "vol_ratio": 1.5},
            "long",
            25.0,
        ),
        (
            "short_bias",
            {"return_4h": -5.0, "return_24h": -3.0},
            "short",
            45.0,
        ),
    ],
)
def test_family_anchor_examples(
    family: str,
    feat: dict[str, object],
    expected_direction: str,
    expected_point: float,
) -> None:
    result = score_continuous_family(family, feat)
    assert result.valid is True
    assert result.direction == expected_direction
    assert result.point_score == pytest.approx(expected_point, abs=1e-9)
    assert result.pessimistic_score <= result.point_score


def test_missing_required_short_trend_is_invalid() -> None:
    result = score_continuous_family("short_bias", {"return_4h": -5.0})
    assert result.valid is False
    assert result.invalid_reason == "missing_required_trend"


def test_volatility_applies_direction_multiplier_after_taker_subtotal() -> None:
    result = score_continuous_family(
        "volatility",
        {
            "return_4h": 2.0,
            "atr_pct": 5.0,
            "bb_squeeze_ratio": 1.0,
            "taker_buy_ratio": 1.5,
            "vol_ratio": 1.5,
        },
    )
    # (ATR 30 + taker 10) * direction activation 0.5, then volume x1.
    assert result.point_score == pytest.approx(20.0, abs=1e-9)


def test_short_bias_multiplies_the_complete_additive_subtotal() -> None:
    result = score_continuous_family(
        "short_bias",
        {
            "return_4h": -5.0,
            "return_24h": -1.0,
            "funding_rate": 0.00010,
            "rsi_14": 35.0,
            "rsi_4h": 35.0,
            "oi_delta_1h": 0.0,
        },
    )
    # (core 30 + funding 10 + RSI bonuses 8 + 10) * 24h multiplier 0.5.
    assert result.point_score == pytest.approx(29.0, abs=1e-9)
    assert result.pessimistic_score == result.point_score


def test_missing_optional_penalty_cannot_improve_pessimistic_score() -> None:
    result = score_continuous_family("momentum", {"relative_strength_24h": 6.0})
    assert result.point_score == 30.0
    assert result.pessimistic_score == 0.0
    missing_funding = next(item for item in result.evidence if item.curve_id == "MOM-09")
    assert missing_funding.observed is False
    assert missing_funding.point_value == 0.0
    assert missing_funding.pessimistic_value == -15.0


def test_candidate_point_and_pessimistic_scores_preserve_missingness_order() -> None:
    missing = evaluate_continuous_candidate(_complete_candidate(spread_bps=None, depth_10bps_usd=None))
    assert missing.valid is True
    assert missing.pessimistic_score <= missing.point_score
    assert missing.missingness_delta >= 0.0
    assert {"spread_bps", "depth_10bps_usd"}.issubset(missing.missingness_feature_ids)
    for spread in (0.0, 5.0, 10.0, 25.0, 45.0):
        observed = evaluate_continuous_candidate(_complete_candidate(spread_bps=spread, depth_10bps_usd=25_000.0))
        assert observed.valid is True
        assert missing.pessimistic_score <= observed.point_score + 1e-9


@pytest.mark.parametrize(
    ("feature", "admissible_values"),
    [
        ("relative_strength_24h", (-6.0, 0.0, 6.0)),
        ("relative_strength_4h", (-1.5, 0.0, 1.5)),
        ("return_1h", (-12.0, 0.0, 12.0)),
        ("return_4h", (-12.0, 0.0, 12.0)),
        ("return_24h", (-20.0, 0.0, 20.0)),
        ("rsi_14", (10.0, 20.0, 50.0, 80.0, 90.0)),
        ("rsi_4h", (10.0, 25.0, 50.0, 75.0, 90.0)),
        ("atr_pct", (0.0, 3.0, 8.0)),
        ("atr_4h", (0.0, 4.0, 8.0)),
        ("bb_squeeze_ratio", (0.4, 0.8, 1.0)),
        ("bb_width_pct", (1.0, 2.0, 4.0)),
        ("bb_width_4h", (1.5, 3.0, 6.0)),
        ("vol_ratio", (0.0, 0.7, 1.0, 1.5, 3.0, 6.0)),
        ("spread_bps", (0.0, 5.0, 10.0, 25.0, 45.0)),
        ("depth_10bps_usd", (0.0, 2_500.0, 6_250.0, 25_000.0, 75_000.0)),
        ("funding_rate", (-0.00020, 0.0, 0.00020)),
        ("oi_delta_1h", (-30.0, 0.0, 30.0)),
        ("taker_buy_ratio", (0.5, 1.0, 2.0)),
    ],
)
def test_each_optional_missing_feature_is_no_better_than_observed_domain(
    feature: str,
    admissible_values: tuple[float, ...],
) -> None:
    missing = evaluate_continuous_candidate(_complete_candidate(**{feature: None}))
    assert missing.valid is True
    observed_scores = []
    for value in admissible_values:
        observed = evaluate_continuous_candidate(_complete_candidate(**{feature: value}))
        assert observed.valid is True
        observed_scores.append(observed.point_score)
    assert missing.pessimistic_score <= min(observed_scores) + 1e-9


def test_observed_zero_spread_is_not_the_missing_prior() -> None:
    result = evaluate_continuous_candidate(_complete_candidate(spread_bps=0.0))
    alloc_cost = next(item for item in result.point_evidence if item.curve_id == "ALLOC-02")
    assert alloc_cost.observed is True
    assert alloc_cost.point_value == 0.0
    assert alloc_cost.fallback_source is None


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("return_4h", math.nan, "return_4h must be finite"),
        ("taker_buy_ratio", 0.0, "taker_buy_ratio must be positive"),
        ("rsi_14", 101.0, "rsi_14 must be within [0, 100]"),
        ("spread_bps", -1.0, "spread_bps must be non-negative"),
    ],
)
def test_candidate_rejects_invalid_present_values(key: str, value: float, reason: str) -> None:
    result = evaluate_continuous_candidate(_complete_candidate(**{key: value}))
    assert result.valid is False
    assert result.invalid_reason == reason


def test_raw_evidence_is_grouped_by_source_not_indicator_name() -> None:
    declarations = raw_feature_declarations()
    for key in (
        "return_1h",
        "return_4h",
        "return_24h",
        "rsi_14",
        "rsi_4h",
        "atr_pct",
        "bb_width_pct",
    ):
        assert declarations[key].evidence_group_id == "price_path"
    assert declarations["vol_ratio"].evidence_group_id == ("market_activity_liquidity")
    assert declarations["funding_rate"].evidence_group_id == "positioning_flow"
    assert declarations["return_4h"].unit == "percent"
    assert declarations["spread_bps"].unit == "basis_points"
    assert declarations["quote_volume_24h"].unit == "usd"


def test_duplicate_consumers_are_declared_once_on_raw_feature() -> None:
    declarations = raw_feature_declarations()
    assert {"MOM-06", "MR-05", "VOL-08a", "EVT-06a"}.issubset(declarations["vol_ratio"].consumer_curve_ids)
    assert {"TRD-02", "ALLOC-02"}.issubset(declarations["spread_bps"].consumer_curve_ids)
    assert {"MR-01", "EVT-04", "ALLOC-03c", "ALLOC-04"}.issubset(declarations["rsi_14"].consumer_curve_ids)


def test_undeclared_curve_consumer_fails_closed() -> None:
    with pytest.raises(scoring.InvalidFeature, match="undeclared_consumer"):
        scoring._evidence("UNKNOWN", "return_4h", 1.0, 1.0, 1.0)


def test_interactions_are_not_labelled_as_independent_additive_votes() -> None:
    result = evaluate_continuous_candidate(
        _complete_candidate(
            relative_strength_24h=None,
            funding_rate=0.0,
            oi_delta_1h=15.0,
            return_1h=-2.0,
            return_4h=-5.0,
            rsi_14=20.0,
        )
    )
    interaction_kinds = {
        item.kind
        for item in (*result.point_evidence, *result.pessimistic_evidence)
        if item.curve_id in {"DER-04", "DER-05", "ALLOC-04"} and item.kind != "interaction_input"
    }
    assert interaction_kinds
    assert interaction_kinds == {"interaction"}


def test_influence_budgets_match_frozen_step_boundary_envelope() -> None:
    """Budgets are frozen step-v2 evidence, not tuned to new outputs."""

    import json

    from aletheia.config.schema import UniverseConfig
    from aletheia.universe.allocator import compute_total_score
    from aletheia.universe.filters import _compute_tradability_score
    from aletheia.universe.scanners import run_all_scanners

    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "universe_step_score_v2.json").read_text(encoding="utf-8")
    )
    values_by_feature: dict[str, set[float]] = {}
    for group in fixture["threshold_probes"]:
        values_by_feature.setdefault(group["feature"], set()).update(group["values"])

    cfg = UniverseConfig()
    neutral = fixture["neutral_features"]
    budgets: dict[str, float] = {}
    for group in fixture["threshold_probes"]:
        feature = group["feature"]
        totals: list[float] = []
        for value in sorted(values_by_feature[feature]):
            feat = {
                **neutral,
                **group["base_overrides"],
                feature: value,
            }
            feat["tradability_score"] = _compute_tradability_score(feat, cfg)
            scanner_result = run_all_scanners(dict(feat))
            totals.append(compute_total_score(feat, scanner_result, cfg))
        budgets[feature] = max(budgets.get(feature, 0.0), max(totals) - min(totals))

    declarations = raw_feature_declarations()
    for feature, budget in budgets.items():
        if feature in declarations:
            assert declarations[feature].influence_budget == pytest.approx(budget, abs=1e-9)
    # Numeric squeeze and beta-adjusted RS had no separate step-v1 boundary
    # fields; cap them at their frozen equivalent consumer effects.
    assert declarations["bb_squeeze_ratio"].influence_budget == 15.0 * 0.35
    assert declarations["rs_beta_adj_24h"].influence_budget == declarations["relative_strength_24h"].influence_budget


def test_candidate_sensitivity_perturbs_whole_raw_feature_once() -> None:
    result = evaluate_continuous_candidate(
        _complete_candidate(
            relative_strength_24h=4.5,
            vol_ratio=2.2,
            funding_rate=0.00004,
        )
    )
    assert result.valid is True
    by_key = {item.raw_feature_id: item for item in result.sensitivities}
    assert len(by_key) == len(result.sensitivities)
    assert by_key["rsi_14"].evidence_group_id == "price_path"
    assert by_key["vol_ratio"].evidence_group_id == ("market_activity_liquidity")
    assert by_key["funding_rate"].evidence_group_id == "positioning_flow"
    assert result.observed_signal_group_count >= 2
    assert all(not item.budget_exceeded for item in result.sensitivities)
    assert result.joint_neutral_score_delta is None
    assert result.interaction_residual is None
    assert "interaction_residual_unavailable" in result.flags
    group_values = dict(result.group_sensitivities)
    assert {item.evidence_group_id for item in result.group_evidence} == set(group_values)
    assert all(
        item.aggregate_abs_local_score_sensitivity == pytest.approx(group_values[item.evidence_group_id])
        for item in result.group_evidence
    )
    grouped_raw_ids = {raw_feature_id for item in result.group_evidence for raw_feature_id in item.raw_feature_ids}
    assert grouped_raw_ids == {item.raw_feature_id for item in result.sensitivities}
    assert all(item.neutral_comparison_valid for item in result.group_evidence)


def test_factor_evidence_carries_units_neutral_and_budget() -> None:
    result = evaluate_continuous_candidate(_complete_candidate())
    evidence = next(item for item in result.point_evidence if item.raw_feature_id == "return_4h")
    declaration = raw_feature_declarations()["return_4h"]
    assert evidence.unit == declaration.unit
    assert evidence.neutral_value == declaration.neutral_value
    assert evidence.influence_budget == declaration.influence_budget


def test_candidate_sensitivity_uses_one_sided_endpoint_perturbation() -> None:
    result = evaluate_continuous_candidate(_complete_candidate(relative_strength_24h=6.0))
    sensitivity = next(item for item in result.sensitivities if item.raw_feature_id == "relative_strength_24h")
    assert sensitivity.local_score_sensitivity > 0.0
    assert sensitivity.budget_exceeded is True
    assert "raw_feature_budget_exceeded" in result.flags


def test_candidate_evaluation_does_not_mutate_input() -> None:
    feat = _complete_candidate(spread_bps=None)
    before = copy.deepcopy(feat)
    evaluate_continuous_candidate(feat)
    assert feat == before


def test_deterministic_boundary_fuzz_preserves_fail_closed_invariants() -> None:
    rng = random.Random(20260807)
    declarations = raw_feature_declarations()
    valid_count = 0
    for _ in range(500):
        feat = _complete_candidate(
            relative_strength_24h=rng.uniform(-8.0, 8.0),
            relative_strength_4h=rng.uniform(-2.0, 2.0),
            return_1h=rng.uniform(-12.0, 12.0),
            return_4h=rng.uniform(-15.0, 15.0),
            return_24h=rng.uniform(-25.0, 25.0),
            rsi_14=rng.uniform(0.0, 100.0),
            rsi_4h=rng.uniform(0.0, 100.0),
            atr_pct=rng.uniform(0.0, 10.0),
            atr_4h=rng.uniform(0.0, 10.0),
            bb_squeeze_ratio=(None if rng.random() < 0.25 else rng.uniform(0.1, 1.5)),
            vol_ratio=(None if rng.random() < 0.20 else rng.uniform(0.0, 7.0)),
            spread_bps=(None if rng.random() < 0.30 else rng.uniform(0.0, 45.0)),
            depth_10bps_usd=(None if rng.random() < 0.30 else rng.uniform(0.0, 100_000.0)),
            funding_rate=(None if rng.random() < 0.20 else rng.uniform(-0.00025, 0.00025)),
            oi_delta_1h=(None if rng.random() < 0.30 else rng.uniform(-35.0, 35.0)),
            taker_buy_ratio=(None if rng.random() < 0.30 else rng.uniform(0.2, 2.5)),
            data_freshness_sec=rng.uniform(0.0, 7200.0),
        )
        before = copy.deepcopy(feat)
        result = evaluate_continuous_candidate(feat)
        assert feat == before
        if not result.valid:
            continue
        valid_count += 1
        assert 0.0 <= result.pessimistic_score <= result.point_score <= 100.0
        assert math.isfinite(result.point_score)
        assert math.isfinite(result.pessimistic_score)
        for item in (*result.point_evidence, *result.pessimistic_evidence):
            assert item.raw_feature_id in declarations
            assert item.curve_id in declarations[item.raw_feature_id].consumer_curve_ids
            assert math.isfinite(item.point_value)
            assert math.isfinite(item.pessimistic_value)
    assert valid_count >= 450


@settings(max_examples=100, deadline=None)
@given(
    sign=st.sampled_from((-1.0, 1.0)),
    rs_magnitude=st.floats(min_value=2.0, max_value=8.0, allow_nan=False, allow_infinity=False),
    ret_1h=st.floats(min_value=-12.0, max_value=12.0, allow_nan=False, allow_infinity=False),
    ret_4h=st.floats(min_value=-15.0, max_value=15.0, allow_nan=False, allow_infinity=False),
    ret_24h=st.one_of(
        st.none(),
        st.floats(min_value=-25.0, max_value=25.0, allow_nan=False, allow_infinity=False),
    ),
    rsi=st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    ),
    volume_ratio=st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=7.0, allow_nan=False, allow_infinity=False),
    ),
    spread=st.one_of(
        st.none(),
        st.floats(min_value=0.0, max_value=45.0, allow_nan=False, allow_infinity=False),
    ),
    funding=st.one_of(
        st.none(),
        st.floats(min_value=-0.00025, max_value=0.00025, allow_nan=False, allow_infinity=False),
    ),
)
def test_candidate_property_pessimistic_score_never_exceeds_point_score(
    sign: float,
    rs_magnitude: float,
    ret_1h: float,
    ret_4h: float,
    ret_24h: float | None,
    rsi: float | None,
    volume_ratio: float | None,
    spread: float | None,
    funding: float | None,
) -> None:
    feat = _complete_candidate(
        relative_strength_24h=sign * rs_magnitude,
        return_1h=ret_1h,
        return_4h=ret_4h,
        return_24h=ret_24h,
        rsi_14=rsi,
        vol_ratio=volume_ratio,
        spread_bps=spread,
        funding_rate=funding,
    )
    result = evaluate_continuous_candidate(feat)
    assert result.valid is True
    assert 0.0 <= result.pessimistic_score <= result.point_score <= 100.0


def test_pure_module_has_no_authority_or_persistence_imports() -> None:
    path = Path(inspect.getsourcefile(scoring) or "")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported.update(node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
    forbidden = (
        "aletheia.learning",
        "aletheia.storage",
        "aletheia.risk",
        "aletheia.execution",
        "aletheia.universe.allocator",
        "aletheia.universe.filters",
        "aletheia.universe.signal_emitter",
        "aletheia.universe.service",
    )
    assert not any(module == prefix or module.startswith(f"{prefix}.") for module in imported for prefix in forbidden)


def test_explicit_policy_rejects_invalid_denominators() -> None:
    with pytest.raises(ValueError):
        ContinuousScoringPolicy(min_volume_24h_usdt=0.0)
    with pytest.raises(ValueError):
        ContinuousScoringPolicy(min_depth_10bps_usd=math.inf)


def test_continuous_mean_reversion_keeps_opposing_core_out_of_directional_score() -> None:
    result = scoring._score_mean_reversion(
        _complete_candidate(rsi_14=80.0, return_4h=-12.0)
    )
    assert result.direction == "short"
    assert result.point_score == 35.0
    assert next(item for item in result.evidence if item.curve_id == "MR-03").point_value == 0.0


def test_continuous_derivatives_keeps_opposing_interactions_out_of_directional_score() -> None:
    result = scoring._score_derivatives(
        _complete_candidate(
            funding_rate=-0.001,
            oi_delta_1h=25.0,
            return_1h=-2.0,
        )
    )
    assert result.direction == "long"
    assert next(item for item in result.evidence if item.curve_id == "DER-03").point_value == 0.0
