"""Pure, non-authoritative Universe continuous scoring primitives.

The module does not import the legacy allocator, filters, signal emitter,
Learning, repositories, or runtime services.  A separate twin adapter may call
it for diagnostics, but ``universe-step-v1`` remains the only authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from numbers import Real
from typing import Callable, Iterable, Mapping, Sequence


CONTINUOUS_SCORER_BEHAVIOR_VERSION = "universe-continuous-shadow-v1"

_DERIVATIVES_MARKET_TYPES = frozenset({"perp", "swap", "futures", "usdt_futures"})
_SIGNAL_GROUPS = frozenset({"price_path", "market_activity_liquidity", "positioning_flow"})


@dataclass(frozen=True)
class ContinuousScoringPolicy:
    """Explicit non-secret inputs that existing configuration will later supply."""

    min_volume_24h_usdt: float = 5_000_000.0
    min_depth_10bps_usd: float = 25_000.0
    external_opportunity_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_volume_24h_usdt) or self.min_volume_24h_usdt <= 0:
            raise ValueError("min_volume_24h_usdt must be finite and positive")
        if not math.isfinite(self.min_depth_10bps_usd) or self.min_depth_10bps_usd <= 0:
            raise ValueError("min_depth_10bps_usd must be finite and positive")
        if not math.isfinite(self.external_opportunity_multiplier) or self.external_opportunity_multiplier < 0:
            raise ValueError("external_opportunity_multiplier must be finite and non-negative")


@dataclass(frozen=True)
class RawFeatureDeclaration:
    evidence_group_id: str
    consumer_curve_ids: tuple[str, ...]
    neutral_value: float
    influence_budget: float
    unit: str


@dataclass(frozen=True)
class FactorEvidence:
    curve_id: str
    raw_feature_id: str
    evidence_group_id: str
    consumer_curve_ids: tuple[str, ...]
    unit: str
    neutral_value: float
    influence_budget: float
    raw_value: float | None
    transformed_value: float | None
    observed: bool
    fallback_source: str | None
    kind: str
    point_value: float
    pessimistic_value: float


@dataclass(frozen=True)
class ComponentScore:
    component_id: str
    direction: str | None
    point_score: float
    pessimistic_score: float
    valid: bool
    invalid_reason: str | None
    evidence: tuple[FactorEvidence, ...] = ()


@dataclass(frozen=True)
class FeatureSensitivity:
    raw_feature_id: str
    evidence_group_id: str
    observed_value: float
    normalized_half_step: float
    local_score_sensitivity: float
    score_delta_to_neutral: float | None
    neutral_comparison_valid: bool
    influence_budget: float
    budget_exceeded: bool


@dataclass(frozen=True)
class EvidenceGroupSensitivity:
    evidence_group_id: str
    raw_feature_ids: tuple[str, ...]
    aggregate_abs_local_score_sensitivity: float
    score_delta_to_joint_neutral: float | None
    neutral_comparison_valid: bool


@dataclass(frozen=True)
class CandidateScore:
    behavior_version: str
    point_family: str | None
    pessimistic_family: str | None
    point_direction: str | None
    pessimistic_direction: str | None
    point_score: float
    pessimistic_score: float
    valid: bool
    invalid_reason: str | None
    point_evidence: tuple[FactorEvidence, ...]
    pessimistic_evidence: tuple[FactorEvidence, ...]
    sensitivities: tuple[FeatureSensitivity, ...] = ()
    group_sensitivities: tuple[tuple[str, float], ...] = ()
    group_evidence: tuple[EvidenceGroupSensitivity, ...] = ()
    observed_signal_group_count: int = 0
    concentration_share: float | None = None
    flags: tuple[str, ...] = ()
    joint_neutral_score_delta: float | None = None
    interaction_residual: float | None = None

    @property
    def missingness_delta(self) -> float:
        return self.point_score - self.pessimistic_score

    @property
    def missingness_feature_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.raw_feature_id
                    for item in (*self.point_evidence, *self.pessimistic_evidence)
                    if not item.observed and abs(item.point_value - item.pessimistic_value) > 1e-12
                }
            )
        )


class InvalidFeature(ValueError):
    """A present feature is invalid and must not be treated as missing or zero."""


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


def _same_sign(left: float, right: float) -> bool:
    return _sign(left) == _sign(right) and _sign(left) != 0


def _validate_anchors(anchors: Sequence[tuple[float, float]]) -> None:
    if len(anchors) < 2:
        raise ValueError("a curve requires at least two anchors")
    previous_x: float | None = None
    for x_value, y_value in anchors:
        if not math.isfinite(x_value) or not math.isfinite(y_value):
            raise ValueError("curve anchors must be finite")
        if previous_x is not None and x_value <= previous_x:
            raise ValueError("curve x anchors must be strictly increasing")
        previous_x = x_value


def _endpoint_slope(h0: float, h1: float, delta0: float, delta1: float) -> float:
    slope = ((2.0 * h0 + h1) * delta0 - h0 * delta1) / (h0 + h1)
    if not _same_sign(slope, delta0):
        return 0.0
    if not _same_sign(delta0, delta1) and abs(slope) > abs(3.0 * delta0):
        return 3.0 * delta0
    return slope


def shape_preserving_curve(
    value: float,
    anchors: Sequence[tuple[float, float]],
) -> float:
    """Evaluate a clamped shape-preserving cubic Hermite curve.

    Slopes follow the monotone PCHIP/Fritsch-Carlson family of rules.  Interior
    extrema and plateaus receive a zero derivative, preventing overshoot while
    allowing bounded single-peak and single-valley bands.
    """

    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidFeature("curve input must be a real number")
    x = float(value)
    if not math.isfinite(x):
        raise InvalidFeature("curve input must be finite")
    _validate_anchors(anchors)

    xs = [float(item[0]) for item in anchors]
    ys = [float(item[1]) for item in anchors]
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]

    count = len(xs)
    h = [xs[index + 1] - xs[index] for index in range(count - 1)]
    delta = [(ys[index + 1] - ys[index]) / h[index] for index in range(count - 1)]
    slopes = [0.0] * count
    if count == 2:
        slopes[0] = slopes[1] = delta[0]
    else:
        slopes[0] = _endpoint_slope(h[0], h[1], delta[0], delta[1])
        slopes[-1] = _endpoint_slope(h[-1], h[-2], delta[-1], delta[-2])
        for index in range(1, count - 1):
            left = delta[index - 1]
            right = delta[index]
            if not _same_sign(left, right):
                slopes[index] = 0.0
                continue
            weight_left = 2.0 * h[index] + h[index - 1]
            weight_right = h[index] + 2.0 * h[index - 1]
            slopes[index] = (weight_left + weight_right) / (weight_left / left + weight_right / right)

    interval = 0
    for index in range(count - 1):
        if xs[index] <= x <= xs[index + 1]:
            interval = index
            break
    width = h[interval]
    t = (x - xs[interval]) / width
    t2 = t * t
    t3 = t2 * t
    result = (
        (2.0 * t3 - 3.0 * t2 + 1.0) * ys[interval]
        + (t3 - 2.0 * t2 + t) * width * slopes[interval]
        + (-2.0 * t3 + 3.0 * t2) * ys[interval + 1]
        + (t3 - t2) * width * slopes[interval + 1]
    )
    lower = min(ys[interval], ys[interval + 1])
    upper = max(ys[interval], ys[interval + 1])
    return min(upper, max(lower, result))


_FEATURES: Mapping[str, RawFeatureDeclaration] = {
    "rs_beta_adj_24h": RawFeatureDeclaration("price_path", ("MOM-01", "SH-03"), 0.0, 10.5, "percent"),
    "relative_strength_24h": RawFeatureDeclaration("price_path", ("MOM-01", "SH-03"), 0.0, 10.5, "percent"),
    "relative_strength_4h": RawFeatureDeclaration("price_path", ("MOM-03",), 0.0, 5.25, "percent"),
    "return_1h": RawFeatureDeclaration(
        "price_path", ("DER-03", "DER-04", "EVT-01", "ALLOC-03a"), 0.0, 15.75, "percent"
    ),
    "return_4h": RawFeatureDeclaration(
        "price_path",
        ("MOM-02", "MR-03", "VOL-06", "DER-03", "DER-04", "DER-05", "EVT-05", "SH-01", "ALLOC-03b", "ALLOC-04"),
        0.0,
        23.75,
        "percent",
    ),
    "return_24h": RawFeatureDeclaration("price_path", ("MOM-10", "MR-04", "SH-02a", "SH-02b"), 0.0, 10.15, "percent"),
    "rsi_14": RawFeatureDeclaration(
        "price_path",
        ("MOM-04", "MR-01", "DER-05", "EVT-04", "SH-05a", "SH-05b", "ALLOC-03c", "ALLOC-04"),
        50.0,
        21.5,
        "index_0_100",
    ),
    "rsi_4h": RawFeatureDeclaration("price_path", ("MOM-05", "MR-02", "SH-06a", "SH-06b"), 50.0, 5.25, "index_0_100"),
    "atr_pct": RawFeatureDeclaration("price_path", ("VOL-01",), 0.0, 10.5, "percent"),
    "atr_4h": RawFeatureDeclaration("price_path", ("VOL-04",), 0.0, 4.2, "percent"),
    "bb_squeeze_ratio": RawFeatureDeclaration("price_path", ("VOL-02",), 1.0, 5.25, "ratio"),
    "bb_width_pct": RawFeatureDeclaration("price_path", ("VOL-03",), 4.0, 3.5, "percent"),
    "bb_width_4h": RawFeatureDeclaration("price_path", ("VOL-05",), 6.0, 4.2, "percent"),
    "vol_ratio": RawFeatureDeclaration(
        "market_activity_liquidity",
        ("MOM-06", "MR-05", "VOL-08a", "VOL-08b", "EVT-06a", "EVT-06b"),
        1.0,
        14.875,
        "ratio",
    ),
    "quote_volume_24h": RawFeatureDeclaration(
        "market_activity_liquidity", ("TRD-01", "ALLOC-01"), 5_000_000.0, 12.500001, "usd"
    ),
    "spread_bps": RawFeatureDeclaration(
        "market_activity_liquidity", ("TRD-02", "ALLOC-02"), 10.0, 25.00075, "basis_points"
    ),
    "depth_10bps_usd": RawFeatureDeclaration("market_activity_liquidity", ("TRD-04",), 25_000.0, 5.0, "usd"),
    "funding_rate": RawFeatureDeclaration(
        "positioning_flow", ("MOM-09", "MR-06", "DER-01", "SH-04a", "SH-04b"), 0.0, 14.0, "fraction"
    ),
    "oi_delta_1h": RawFeatureDeclaration(
        "positioning_flow", ("MOM-08", "DER-02", "DER-03", "DER-04", "EVT-03", "SH-07a", "SH-07b"), 0.0, 10.5, "percent"
    ),
    "taker_buy_ratio": RawFeatureDeclaration("positioning_flow", ("MOM-07", "VOL-07", "EVT-02"), 1.0, 5.25, "ratio"),
    "data_freshness_sec": RawFeatureDeclaration("data_quality_cost", ("TRD-03", "ALLOC-05"), 0.0, 16.25, "seconds"),
}


_FEATURE_ANCHORS: Mapping[str, tuple[float, ...]] = {
    "rs_beta_adj_24h": (-6.0, -4.0, -3.5, -1.5, 0.0, 1.5, 3.5, 4.0, 6.0),
    "relative_strength_24h": (-6.0, -4.0, -3.5, -1.5, 0.0, 1.5, 3.5, 4.0, 6.0),
    "relative_strength_4h": (-1.5, -0.5, 0.0, 0.5, 1.5),
    "return_1h": (-12.0, -8.0, -4.0, -2.5, 0.0, 2.5, 4.0, 8.0, 12.0),
    "return_4h": (-12.0, -8.0, -6.0, -5.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 5.0, 6.0, 8.0, 12.0),
    "return_24h": (-20.0, -15.0, -12.0, -10.0, -8.0, -7.5, -3.0, -1.0, 0.0, 1.0, 3.0, 7.5, 8.0, 10.0, 12.0, 15.0, 20.0),
    "rsi_14": (
        10.0,
        15.0,
        20.0,
        22.0,
        25.0,
        28.0,
        30.0,
        31.5,
        35.0,
        40.0,
        45.0,
        50.0,
        55.0,
        60.0,
        70.0,
        75.0,
        78.0,
        80.0,
        85.0,
        90.0,
    ),
    "rsi_4h": (10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 45.0, 50.0, 55.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0),
    "atr_pct": (0.0, 2.0, 3.0, 5.0, 8.0),
    "atr_4h": (0.0, 2.0, 4.0, 8.0),
    "bb_squeeze_ratio": (0.4, 0.6, 0.8, 1.0),
    "bb_width_pct": (1.0, 2.0, 4.0),
    "bb_width_4h": (1.5, 3.0, 6.0),
    "vol_ratio": (0.0, 0.4, 0.7, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0),
    "spread_bps": (0.0, 5.0, 10.0, 25.0, 40.0, 45.0),
    "funding_rate": (-0.00020, -0.00010, -0.00005, -0.00002, 0.0, 0.00002, 0.00005, 0.00010, 0.00020),
    "oi_delta_1h": (-30.0, -20.0, -15.0, -10.0, -7.5, -5.0, -3.0, 0.0, 3.0, 5.0, 7.5, 10.0, 15.0, 20.0, 25.0, 30.0),
    "taker_buy_ratio": (0.5, 0.65, 0.8, 1.0, 1.2, 1.5, 2.0),
    "data_freshness_sec": (0.0, 300.0, 1200.0, 1800.0, 2400.0, 3600.0, 5400.0, 7200.0, 9000.0),
}


def raw_feature_declarations() -> Mapping[str, RawFeatureDeclaration]:
    """Return a copy of the raw-feature manifest used by the pure scorer."""

    return dict(_FEATURES)


def _read(feat: Mapping[str, object], key: str) -> float | None:
    value = feat.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise InvalidFeature(f"{key} must be a real number or missing")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise InvalidFeature(f"{key} must be finite")
    return numeric


def _feature_validation_error(feat: Mapping[str, object]) -> str | None:
    try:
        observed = {key: _read(feat, key) for key in _FEATURES if key in feat and feat.get(key) is not None}
    except InvalidFeature as exc:
        return str(exc)
    nonnegative = {
        "atr_pct",
        "atr_4h",
        "bb_squeeze_ratio",
        "bb_width_pct",
        "bb_width_4h",
        "vol_ratio",
        "quote_volume_24h",
        "spread_bps",
        "depth_10bps_usd",
        "data_freshness_sec",
    }
    for key in nonnegative:
        value = observed.get(key)
        if value is not None and value < 0:
            return f"{key} must be non-negative"
    taker = observed.get("taker_buy_ratio")
    if taker is not None and taker <= 0:
        return "taker_buy_ratio must be positive"
    squeeze = observed.get("bb_squeeze_ratio")
    if squeeze is not None and squeeze <= 0:
        return "bb_squeeze_ratio must be positive"
    for key in ("rsi_14", "rsi_4h"):
        value = observed.get(key)
        if value is not None and not 0.0 <= value <= 100.0:
            return f"{key} must be within [0, 100]"
    return None


def _evidence(
    curve_id: str,
    key: str,
    raw_value: float | None,
    transformed_value: float | None,
    point_value: float,
    *,
    pessimistic_value: float | None = None,
    observed: bool | None = None,
    fallback_source: str | None = None,
    kind: str = "additive",
) -> FactorEvidence:
    declaration = _FEATURES.get(key)
    if declaration is None:
        raise InvalidFeature(f"undeclared_raw_feature:{key}")
    if curve_id not in declaration.consumer_curve_ids:
        raise InvalidFeature(f"undeclared_consumer:{curve_id}:{key}")
    return FactorEvidence(
        curve_id=curve_id,
        raw_feature_id=key,
        evidence_group_id=declaration.evidence_group_id,
        consumer_curve_ids=declaration.consumer_curve_ids,
        unit=declaration.unit,
        neutral_value=declaration.neutral_value,
        influence_budget=declaration.influence_budget,
        raw_value=raw_value,
        transformed_value=transformed_value,
        observed=raw_value is not None if observed is None else observed,
        fallback_source=fallback_source,
        kind=kind,
        point_value=float(point_value),
        pessimistic_value=float(point_value if pessimistic_value is None else pessimistic_value),
    )


def _invalid(component_id: str, reason: str) -> ComponentScore:
    return ComponentScore(component_id, None, 0.0, 0.0, False, reason)


def _finalize(
    component_id: str,
    direction: str | None,
    point: float,
    pessimistic: float,
    evidence: Iterable[FactorEvidence],
) -> ComponentScore:
    if direction is None:
        point = pessimistic = 0.0
    point = min(100.0, max(0.0, float(point)))
    pessimistic = min(point, min(100.0, max(0.0, float(pessimistic))))
    return ComponentScore(
        component_id,
        direction,
        point,
        pessimistic,
        True,
        None,
        tuple(evidence),
    )


def _curve(value: float, anchors: Sequence[tuple[float, float]]) -> float:
    return shape_preserving_curve(value, anchors)


def _score_momentum(feat: Mapping[str, object]) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    beta_rs = _read(feat, "rs_beta_adj_24h")
    raw_rs = _read(feat, "relative_strength_24h")
    ret_4h = _read(feat, "return_4h")
    direction: str | None = None
    point = pessimistic = 0.0

    rs_key: str | None = None
    rs_value: float | None = None
    fallback: str | None = None
    if beta_rs is not None:
        rs_key, rs_value = "rs_beta_adj_24h", beta_rs
    elif raw_rs is not None:
        rs_key, rs_value = "relative_strength_24h", raw_rs
        fallback = "rs_beta_adj_24h_missing"
    if rs_value is not None and rs_key is not None:
        contribution = _curve(abs(rs_value), ((1.5, 0.0), (3.5, 20.0), (4.0, 20.0), (6.0, 30.0)))
        evidence.append(_evidence("MOM-01", rs_key, rs_value, abs(rs_value), contribution, fallback_source=fallback))
        if contribution > 0 and rs_value != 0:
            direction = "long" if rs_value > 0 else "short"
        point += contribution
        pessimistic += contribution
    elif ret_4h is not None:
        contribution = _curve(abs(ret_4h), ((2.0, 0.0), (4.0, 15.0)))
        evidence.append(
            _evidence(
                "MOM-02",
                "return_4h",
                ret_4h,
                abs(ret_4h),
                contribution,
                fallback_source="relative_strength_24h_missing",
            )
        )
        if contribution > 0 and ret_4h != 0:
            direction = "long" if ret_4h > 0 else "short"
        point += contribution
        pessimistic += contribution
    else:
        return _invalid("momentum", "missing_directional_core")

    if direction is None:
        return _finalize("momentum", None, 0.0, 0.0, evidence)
    directional = 1.0 if direction == "long" else -1.0

    def add_optional(
        curve_id: str,
        key: str,
        transform: Callable[[float], float],
        anchors: Sequence[tuple[float, float]],
        missing_lower: float = 0.0,
    ) -> None:
        nonlocal point, pessimistic
        raw = _read(feat, key)
        if raw is None:
            evidence.append(_evidence(curve_id, key, None, None, 0.0, pessimistic_value=missing_lower, observed=False))
            pessimistic += missing_lower
            return
        transformed = transform(raw)
        contribution = _curve(transformed, anchors)
        evidence.append(_evidence(curve_id, key, raw, transformed, contribution))
        point += contribution
        pessimistic += contribution

    add_optional("MOM-03", "relative_strength_4h", lambda value: directional * value, ((0.5, 0.0), (1.5, 15.0)))
    rsi_1h_anchors = (
        ((50.0, 0.0), (55.0, 5.0), (60.0, 10.0), (70.0, 10.0), (75.0, 5.0), (80.0, 0.0))
        if direction == "long"
        else ((20.0, 0.0), (25.0, 5.0), (30.0, 10.0), (40.0, 10.0), (45.0, 5.0), (50.0, 0.0))
    )
    add_optional("MOM-04", "rsi_14", lambda value: value, rsi_1h_anchors)
    add_optional("MOM-05", "rsi_4h", lambda value: value, tuple((x, y / 2.0) for x, y in rsi_1h_anchors))
    add_optional(
        "MOM-06", "vol_ratio", lambda value: value, ((1.0, 0.0), (1.5, 5.0), (2.0, 10.0), (3.0, 15.0), (5.0, 20.0))
    )
    add_optional(
        "MOM-07",
        "taker_buy_ratio",
        lambda value: value if direction == "long" else (1.0 / value if value > 0 else math.nan),
        ((1.0, 0.0), (1.2, 5.0), (1.5, 10.0)),
    )
    add_optional("MOM-08", "oi_delta_1h", lambda value: directional * value, ((0.0, 0.0), (5.0, 5.0), (10.0, 10.0)))
    add_optional(
        "MOM-09",
        "funding_rate",
        lambda value: directional * value,
        ((0.00005, 0.0), (0.00010, -7.5), (0.00020, -15.0)),
        -15.0,
    )
    add_optional("MOM-10", "return_24h", abs, ((7.5, 0.0), (10.0, -10.0), (15.0, -20.0)), -20.0)
    return _finalize("momentum", direction, point, pessimistic, evidence)


def _score_mean_reversion(feat: Mapping[str, object]) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    direction: str | None = None
    point = pessimistic = 0.0
    cores = (
        (
            "MR-01",
            "rsi_14",
            ((20.0, 0.0), (25.0, 20.0), (30.0, 35.0), (40.0, 35.0)),
            lambda value: abs(value - 50.0),
            lambda value: "short" if value > 50 else "long",
        ),
        (
            "MR-02",
            "rsi_4h",
            ((15.0, 0.0), (25.0, 15.0), (35.0, 15.0)),
            lambda value: abs(value - 50.0),
            lambda value: "short" if value > 50 else "long",
        ),
        (
            "MR-03",
            "return_4h",
            ((5.0, 0.0), (8.0, 25.0), (12.0, 25.0)),
            abs,
            lambda value: "short" if value > 0 else "long",
        ),
        (
            "MR-04",
            "return_24h",
            ((8.0, 0.0), (12.0, 15.0), (20.0, 15.0)),
            abs,
            lambda value: "short" if value > 0 else "long",
        ),
    )
    for curve_id, key, anchors, transform, direction_from in cores:
        raw = _read(feat, key)
        if raw is None:
            evidence.append(_evidence(curve_id, key, None, None, 0.0, observed=False))
            continue
        transformed = transform(raw)
        contribution = _curve(transformed, anchors)
        evidence.append(_evidence(curve_id, key, raw, transformed, contribution))
        point += contribution
        pessimistic += contribution
        is_directional_value = raw != 50 if key.startswith("rsi_") else raw != 0
        if direction is None and contribution > 0 and is_directional_value:
            direction = direction_from(raw)
    if direction is None:
        return _finalize("mean_reversion", None, 0.0, 0.0, evidence)

    vol = _read(feat, "vol_ratio")
    if vol is None:
        evidence.append(_evidence("MR-05", "vol_ratio", None, None, 0.0, observed=False))
    else:
        contribution = _curve(vol, ((0.0, 15.0), (0.4, 15.0), (0.7, 15.0), (1.0, 0.0)))
        evidence.append(_evidence("MR-05", "vol_ratio", vol, vol, contribution))
        point += contribution
        pessimistic += contribution
    funding = _read(feat, "funding_rate")
    if funding is None:
        evidence.append(_evidence("MR-06", "funding_rate", None, None, 0.0, observed=False))
    else:
        transformed = funding if direction == "short" else -funding
        contribution = _curve(transformed, ((0.00002, 0.0), (0.00005, 15.0), (0.00010, 15.0)))
        evidence.append(_evidence("MR-06", "funding_rate", funding, transformed, contribution))
        point += contribution
        pessimistic += contribution
    return _finalize("mean_reversion", direction, point, pessimistic, evidence)


def _score_volatility(feat: Mapping[str, object]) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    point = pessimistic = 0.0

    for curve_id, key, anchors in (
        ("VOL-01", "atr_pct", ((2.0, 0.0), (3.0, 20.0), (5.0, 30.0), (8.0, 30.0))),
        ("VOL-04", "atr_4h", ((2.0, 0.0), (4.0, 12.0), (8.0, 12.0))),
        ("VOL-05", "bb_width_4h", ((1.5, 12.0), (3.0, 12.0), (6.0, 0.0))),
    ):
        raw = _read(feat, key)
        contribution = 0.0 if raw is None else _curve(raw, anchors)
        evidence.append(_evidence(curve_id, key, raw, raw, contribution, observed=raw is not None))
        point += contribution
        pessimistic += contribution

    squeeze = _read(feat, "bb_squeeze_ratio")
    width = _read(feat, "bb_width_pct")
    if squeeze is not None:
        contribution = _curve(squeeze, ((0.4, 15.0), (0.6, 15.0), (0.8, 5.0), (1.0, 0.0)))
        evidence.append(_evidence("VOL-02", "bb_squeeze_ratio", squeeze, squeeze, contribution))
        point += contribution
        pessimistic += contribution
    else:
        evidence.append(
            _evidence(
                "VOL-02",
                "bb_squeeze_ratio",
                None,
                None,
                0.0,
                observed=False,
                fallback_source="bb_width_pct" if width is not None else None,
            )
        )
        contribution = 0.0 if width is None else _curve(width, ((1.0, 10.0), (2.0, 10.0), (4.0, 0.0)))
        evidence.append(
            _evidence(
                "VOL-03",
                "bb_width_pct",
                width,
                width,
                contribution,
                observed=width is not None,
                fallback_source="bb_squeeze_ratio_missing",
            )
        )
        point += contribution
        pessimistic += contribution

    ret_4h = _read(feat, "return_4h")
    if ret_4h is None:
        return _invalid("volatility", "missing_directional_core")
    activation = _curve(abs(ret_4h), ((1.0, 0.0), (2.0, 0.5), (3.0, 1.0)))
    direction = None if activation <= 0 or ret_4h == 0 else ("long" if ret_4h > 0 else "short")
    evidence.append(_evidence("VOL-06", "return_4h", ret_4h, abs(ret_4h), activation, kind="multiplier"))
    if direction is None:
        return _finalize("volatility", None, 0.0, 0.0, evidence)
    taker = _read(feat, "taker_buy_ratio")
    if taker is None:
        evidence.append(_evidence("VOL-07", "taker_buy_ratio", None, None, 0.0, observed=False))
    else:
        if taker <= 0:
            raise InvalidFeature("taker_buy_ratio must be positive")
        aligned = taker if direction == "long" else 1.0 / taker
        contribution = _curve(aligned, ((1.0, 0.0), (1.2, 5.0), (1.5, 10.0)))
        evidence.append(_evidence("VOL-07", "taker_buy_ratio", taker, aligned, contribution))
        point += contribution
        pessimistic += contribution

    point *= activation
    pessimistic *= activation

    vol = _read(feat, "vol_ratio")
    if vol is None:
        multiplier_point, multiplier_pessimistic, bonus = 0.7, 0.5, 0.0
        evidence.append(
            _evidence(
                "VOL-08a",
                "vol_ratio",
                None,
                None,
                multiplier_point,
                pessimistic_value=multiplier_pessimistic,
                observed=False,
                kind="multiplier",
            )
        )
        evidence.append(_evidence("VOL-08b", "vol_ratio", None, None, 0.0, observed=False))
    else:
        multiplier_point = multiplier_pessimistic = _curve(vol, ((0.0, 0.5), (1.0, 0.5), (1.5, 1.0)))
        bonus = _curve(vol, ((1.5, 0.0), (2.0, 10.0), (3.0, 25.0)))
        evidence.append(_evidence("VOL-08a", "vol_ratio", vol, vol, multiplier_point, kind="multiplier"))
        evidence.append(_evidence("VOL-08b", "vol_ratio", vol, vol, bonus))
    point = point * multiplier_point + bonus
    pessimistic = pessimistic * multiplier_pessimistic + bonus
    return _finalize("volatility", direction, point, pessimistic, evidence)


def _derivatives_oi_activation(
    oi_delta: float | None,
    price_return: float | None,
    current_direction: str | None,
) -> tuple[float, str | None]:
    if current_direction is not None:
        return 1.0, current_direction
    if oi_delta is None or price_return is None or oi_delta <= 0:
        return 0.0, None
    activation = _curve(oi_delta, ((5.0, 0.0), (10.0, 1.0))) * _curve(abs(price_return), ((0.25, 0.0), (0.5, 1.0)))
    if activation <= 0:
        return activation, None
    return activation, "long" if price_return > 0 else "short"


def _derivatives_divergence(
    oi_delta: float | None,
    price_return: float | None,
) -> tuple[float, str | None]:
    if oi_delta is None or price_return is None or oi_delta * price_return >= 0:
        return 0.0, None
    divergence = (
        15.0 * _curve(abs(oi_delta), ((3.0, 0.0), (10.0, 1.0))) * _curve(abs(price_return), ((0.5, 0.0), (2.0, 1.0)))
    )
    direction = "long" if oi_delta > 0 and price_return < 0 else "short"
    return divergence, direction


def _derivatives_proxy_evidence(
    feat: Mapping[str, object],
    ret_4h: float | None,
) -> tuple[float, str | None, tuple[FactorEvidence, ...]]:
    rsi = _read(feat, "rsi_14")
    if rsi is None or ret_4h is None:
        return (
            0.0,
            None,
            (
                _evidence(
                    "DER-05",
                    "rsi_14",
                    rsi,
                    None,
                    0.0,
                    observed=rsi is not None,
                    fallback_source="funding_and_oi_missing",
                ),
            ),
        )
    short_score = 20.0 * _curve(rsi - 50.0, ((20.0, 0.0), (25.0, 1.0))) * _curve(ret_4h, ((3.0, 0.0), (5.0, 1.0)))
    long_score = 20.0 * _curve(50.0 - rsi, ((20.0, 0.0), (25.0, 1.0))) * _curve(-ret_4h, ((3.0, 0.0), (5.0, 1.0)))
    proxy = max(short_score, long_score)
    direction = None if proxy <= 0 else ("short" if short_score >= long_score else "long")
    return (
        proxy,
        direction,
        (
            _evidence(
                "DER-05",
                "rsi_14",
                rsi,
                abs(rsi - 50.0),
                proxy,
                fallback_source="funding_and_oi_missing",
                kind="interaction",
            ),
            _evidence(
                "DER-05",
                "return_4h",
                ret_4h,
                abs(ret_4h),
                proxy,
                fallback_source="rsi_14",
                kind="interaction_input",
            ),
        ),
    )


def _score_derivatives(feat: Mapping[str, object]) -> ComponentScore:
    market_type = str(feat.get("market_type") or "").lower()
    if market_type not in _DERIVATIVES_MARKET_TYPES:
        return _invalid("derivatives", "non_derivatives_market")
    evidence: list[FactorEvidence] = []
    point = pessimistic = 0.0
    direction: str | None = None
    funding = _read(feat, "funding_rate")
    oi_delta = _read(feat, "oi_delta_1h")
    ret_1h = _read(feat, "return_1h")
    ret_4h = _read(feat, "return_4h")
    price_return = ret_1h if ret_1h is not None else ret_4h
    return_key = "return_1h" if ret_1h is not None else "return_4h"

    if funding is None:
        evidence.append(_evidence("DER-01", "funding_rate", None, None, 0.0, observed=False))
    else:
        contribution = _curve(
            abs(funding), ((0.00001, 0.0), (0.00002, 10.0), (0.00005, 25.0), (0.00010, 40.0), (0.00020, 40.0))
        )
        evidence.append(_evidence("DER-01", "funding_rate", funding, abs(funding), contribution))
        point += contribution
        pessimistic += contribution
        if contribution > 0 and funding != 0:
            direction = "long" if funding < 0 else "short"

    oi_contribution = 0.0
    if oi_delta is None:
        evidence.append(_evidence("DER-02", "oi_delta_1h", None, None, 0.0, observed=False))
    else:
        oi_contribution = _curve(abs(oi_delta), ((5.0, 0.0), (10.0, 20.0), (20.0, 30.0), (30.0, 30.0)))
        evidence.append(_evidence("DER-02", "oi_delta_1h", oi_delta, abs(oi_delta), oi_contribution))

    oi_activation, oi_direction = _derivatives_oi_activation(oi_delta, price_return, direction)
    if direction is None:
        direction = oi_direction
    evidence.append(
        _evidence(
            "DER-03",
            "oi_delta_1h",
            oi_delta,
            oi_delta,
            oi_activation,
            observed=oi_delta is not None and price_return is not None,
            fallback_source=return_key if price_return is not None else None,
            kind="multiplier",
        )
    )
    evidence.append(
        _evidence(
            "DER-03",
            return_key,
            price_return,
            abs(price_return) if price_return is not None else None,
            oi_activation,
            observed=oi_delta is not None and price_return is not None,
            fallback_source="oi_delta_1h",
            kind="interaction_input",
        )
    )
    point += oi_contribution * oi_activation
    pessimistic += oi_contribution * oi_activation

    divergence, divergence_direction = _derivatives_divergence(oi_delta, price_return)
    evidence.append(
        _evidence(
            "DER-04",
            "oi_delta_1h",
            oi_delta,
            abs(oi_delta) if oi_delta is not None else None,
            divergence,
            observed=oi_delta is not None and price_return is not None,
            fallback_source=return_key if price_return is not None else None,
            kind="interaction",
        )
    )
    evidence.append(
        _evidence(
            "DER-04",
            return_key,
            price_return,
            abs(price_return) if price_return is not None else None,
            divergence,
            observed=oi_delta is not None and price_return is not None,
            fallback_source="oi_delta_1h",
            kind="interaction_input",
        )
    )
    point += divergence
    pessimistic += divergence
    if direction is None and divergence > 0:
        direction = divergence_direction

    if funding is None and oi_delta is None:
        proxy, proxy_direction, proxy_evidence = _derivatives_proxy_evidence(feat, ret_4h)
        evidence.extend(proxy_evidence)
        point += proxy
        pessimistic += proxy
        if direction is None:
            direction = proxy_direction
    return _finalize("derivatives", direction, point, pessimistic, evidence)


def _score_event(feat: Mapping[str, object]) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    ret_1h = _read(feat, "return_1h")
    if ret_1h is None:
        return _invalid("event", "missing_directional_core")
    point = pessimistic = _curve(abs(ret_1h), ((2.5, 0.0), (4.0, 25.0), (8.0, 45.0), (12.0, 45.0)))
    evidence.append(_evidence("EVT-01", "return_1h", ret_1h, abs(ret_1h), point))
    direction = None if point <= 0 or ret_1h == 0 else ("long" if ret_1h > 0 else "short")
    if direction is None:
        return _finalize("event", None, 0.0, 0.0, evidence)
    directional = 1.0 if direction == "long" else -1.0

    taker = _read(feat, "taker_buy_ratio")
    if taker is None:
        evidence.append(_evidence("EVT-02", "taker_buy_ratio", None, None, 0.0, observed=False))
    else:
        if taker <= 0:
            raise InvalidFeature("taker_buy_ratio must be positive")
        aligned = taker if direction == "long" else 1.0 / taker
        contribution = _curve(aligned, ((1.0, 0.0), (1.5, 15.0), (2.0, 15.0)))
        evidence.append(_evidence("EVT-02", "taker_buy_ratio", taker, aligned, contribution))
        point += contribution
        pessimistic += contribution
    oi = _read(feat, "oi_delta_1h")
    oi_contribution = 0.0 if oi is None else _curve(abs(oi), ((7.5, 0.0), (15.0, 10.0), (25.0, 10.0)))
    evidence.append(
        _evidence(
            "EVT-03", "oi_delta_1h", oi, abs(oi) if oi is not None else None, oi_contribution, observed=oi is not None
        )
    )
    point += oi_contribution
    pessimistic += oi_contribution

    rsi = _read(feat, "rsi_14")
    rsi_on_event_side = rsi is not None and ((direction == "long" and rsi > 50) or (direction == "short" and rsi < 50))
    if rsi is None:
        rsi_multiplier_point, rsi_multiplier_pessimistic = 1.0, 0.7
    elif rsi_on_event_side:
        rsi_multiplier_point = rsi_multiplier_pessimistic = _curve(
            abs(rsi - 50.0), ((20.0, 1.0), (30.0, 0.85), (40.0, 0.7))
        )
    else:
        rsi_multiplier_point = rsi_multiplier_pessimistic = 1.0
    evidence.append(
        _evidence(
            "EVT-04",
            "rsi_14",
            rsi,
            abs(rsi - 50.0) if rsi is not None else None,
            rsi_multiplier_point,
            pessimistic_value=rsi_multiplier_pessimistic,
            observed=rsi is not None,
            kind="multiplier",
        )
    )
    point *= rsi_multiplier_point
    pessimistic *= rsi_multiplier_pessimistic

    ret_4h = _read(feat, "return_4h")
    aligned_ret = None if ret_4h is None else directional * ret_4h
    trend_bonus = 0.0 if aligned_ret is None else _curve(aligned_ret, ((1.5, 0.0), (3.0, 5.0), (6.0, 5.0)))
    evidence.append(_evidence("EVT-05", "return_4h", ret_4h, aligned_ret, trend_bonus, observed=ret_4h is not None))
    point += trend_bonus
    pessimistic += trend_bonus

    vol = _read(feat, "vol_ratio")
    if vol is None:
        vol_multiplier_point, vol_multiplier_pessimistic, bonus = 0.7, 0.5, 0.0
    else:
        vol_multiplier_point = vol_multiplier_pessimistic = _curve(vol, ((0.0, 0.5), (1.0, 0.5), (1.5, 1.0)))
        bonus = _curve(vol, ((1.5, 0.0), (2.0, 20.0), (4.0, 30.0), (6.0, 30.0)))
    evidence.append(
        _evidence(
            "EVT-06a",
            "vol_ratio",
            vol,
            vol,
            vol_multiplier_point,
            pessimistic_value=vol_multiplier_pessimistic,
            observed=vol is not None,
            kind="multiplier",
        )
    )
    evidence.append(_evidence("EVT-06b", "vol_ratio", vol, vol, bonus, observed=vol is not None))
    point = point * vol_multiplier_point + bonus
    pessimistic = pessimistic * vol_multiplier_pessimistic + bonus
    return _finalize("event", direction, point, pessimistic, evidence)


def _score_short_bias(feat: Mapping[str, object]) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    ret_4h = _read(feat, "return_4h")
    ret_24h = _read(feat, "return_24h")
    if ret_4h is None or ret_24h is None:
        return _invalid("short_bias", "missing_required_trend")
    decline = -ret_4h
    core = _curve(decline, ((1.0, 0.0), (2.5, 22.0), (5.0, 30.0), (8.0, 30.0)))
    evidence.append(_evidence("SH-01", "return_4h", ret_4h, decline, core))
    if core <= 0:
        return _finalize("short_bias", None, 0.0, 0.0, evidence)
    trend = -ret_24h
    trend_multiplier = _curve(trend, ((0.0, 0.0), (1.0, 0.5), (3.0, 1.0)))
    trend_bonus = _curve(trend, ((1.0, 0.0), (3.0, 15.0), (6.0, 15.0)))
    evidence.append(_evidence("SH-02a", "return_24h", ret_24h, trend, trend_multiplier, kind="multiplier"))
    evidence.append(_evidence("SH-02b", "return_24h", ret_24h, trend, trend_bonus))

    beta_rs = _read(feat, "rs_beta_adj_24h")
    raw_rs = _read(feat, "relative_strength_24h")
    rs_key = "rs_beta_adj_24h" if beta_rs is not None else "relative_strength_24h"
    rs = beta_rs if beta_rs is not None else raw_rs
    rs_bonus = 0.0 if rs is None else _curve(-rs, ((1.5, 0.0), (3.0, 15.0), (6.0, 15.0)))
    evidence.append(
        _evidence(
            "SH-03",
            rs_key,
            rs,
            -rs if rs is not None else None,
            rs_bonus,
            observed=rs is not None,
            fallback_source="rs_beta_adj_24h_missing" if beta_rs is None and raw_rs is not None else None,
        )
    )

    funding = _read(feat, "funding_rate")
    if funding is None:
        funding_bonus = 0.0
        funding_multiplier_point, funding_multiplier_pessimistic = 1.0, 0.0
    else:
        funding_bonus = _curve(funding, ((0.00002, 0.0), (0.00005, 10.0), (0.00010, 10.0)))
        funding_multiplier_point = funding_multiplier_pessimistic = _curve(
            abs(min(funding, 0.0)), ((0.00005, 1.0), (0.00010, 0.5), (0.00020, 0.0))
        )
    evidence.append(_evidence("SH-04a", "funding_rate", funding, funding, funding_bonus, observed=funding is not None))
    evidence.append(
        _evidence(
            "SH-04b",
            "funding_rate",
            funding,
            abs(min(funding, 0.0)) if funding is not None else None,
            funding_multiplier_point,
            pessimistic_value=funding_multiplier_pessimistic,
            observed=funding is not None,
            kind="multiplier",
        )
    )
    rsi_bonus_total = 0.0
    rsi_multiplier_point_total = 1.0
    rsi_multiplier_pessimistic_total = 1.0
    for multiplier_id, bonus_id, key, bonus_anchors, cap in (
        ("SH-05a", "SH-05b", "rsi_14", ((25.0, 0.0), (35.0, 8.0), (45.0, 8.0), (55.0, 0.0)), 8.0),
        ("SH-06a", "SH-06b", "rsi_4h", ((25.0, 0.0), (35.0, 10.0), (45.0, 10.0), (55.0, 0.0)), 10.0),
    ):
        raw = _read(feat, key)
        if raw is None:
            multiplier_point, multiplier_pessimistic, bonus = 1.0, 0.0, 0.0
        else:
            multiplier_point = multiplier_pessimistic = _curve(raw, ((15.0, 0.0), (25.0, 0.5), (30.0, 1.0)))
            bonus = min(cap, _curve(raw, bonus_anchors))
        evidence.append(
            _evidence(
                multiplier_id,
                key,
                raw,
                raw,
                multiplier_point,
                pessimistic_value=multiplier_pessimistic,
                observed=raw is not None,
                kind="multiplier",
            )
        )
        evidence.append(_evidence(bonus_id, key, raw, raw, bonus, observed=raw is not None))
        rsi_bonus_total += bonus
        rsi_multiplier_point_total *= multiplier_point
        rsi_multiplier_pessimistic_total *= multiplier_pessimistic

    oi = _read(feat, "oi_delta_1h")
    liquidation = 0.0 if oi is None else _curve(-oi, ((5.0, 0.0), (10.0, 12.0), (20.0, 12.0)))
    new_short = 0.0 if oi is None else _curve(oi, ((5.0, 0.0), (10.0, 10.0), (20.0, 10.0)))
    oi_bonus = max(liquidation, new_short)
    curve_id = "SH-07a" if liquidation >= new_short else "SH-07b"
    evidence.append(
        _evidence(curve_id, "oi_delta_1h", oi, abs(oi) if oi is not None else None, oi_bonus, observed=oi is not None)
    )
    additive_subtotal = core + trend_bonus + rs_bonus + funding_bonus + rsi_bonus_total + oi_bonus
    point = additive_subtotal * trend_multiplier * funding_multiplier_point * rsi_multiplier_point_total
    pessimistic = (
        additive_subtotal * trend_multiplier * funding_multiplier_pessimistic * rsi_multiplier_pessimistic_total
    )
    return _finalize("short_bias", "short", point, pessimistic, evidence)


_FAMILY_SCORERS: Mapping[str, Callable[[Mapping[str, object]], ComponentScore]] = {
    "momentum": _score_momentum,
    "mean_reversion": _score_mean_reversion,
    "volatility": _score_volatility,
    "derivatives": _score_derivatives,
    "event": _score_event,
    "short_bias": _score_short_bias,
}


def score_continuous_family(
    family: str,
    feat: Mapping[str, object],
) -> ComponentScore:
    """Evaluate one family without mutating input or reaching an authority path."""

    scorer = _FAMILY_SCORERS.get(family)
    if scorer is None:
        raise ValueError(f"unknown continuous family: {family}")
    validation_error = _feature_validation_error(feat)
    if validation_error is not None:
        return _invalid(family, validation_error)
    try:
        return scorer(feat)
    except InvalidFeature as exc:
        return _invalid(family, str(exc))


def score_all_continuous_families(
    feat: Mapping[str, object],
) -> tuple[ComponentScore, ...]:
    return tuple(score_continuous_family(family, feat) for family in _FAMILY_SCORERS)


def _score_tradability(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
) -> ComponentScore:
    evidence: list[FactorEvidence] = []
    volume = _read(feat, "quote_volume_24h")
    freshness = _read(feat, "data_freshness_sec")
    if volume is None or volume <= 0:
        return _invalid("tradability", "missing_required_volume")
    if freshness is None:
        return _invalid("tradability", "missing_required_freshness")
    ratio = volume / policy.min_volume_24h_usdt
    point = pessimistic = _curve(ratio, ((0.0, 0.0), (1.0, 20.0), (2.0, 30.0), (5.0, 40.0)))
    evidence.append(_evidence("TRD-01", "quote_volume_24h", volume, ratio, point))
    spread = _read(feat, "spread_bps")
    if spread is None:
        spread_point, spread_pessimistic = 15.0, 0.0
    else:
        spread_point = spread_pessimistic = _curve(
            spread, ((0.0, 35.0), (5.0, 35.0), (10.0, 28.0), (25.0, 20.0), (45.0, 0.0))
        )
    evidence.append(
        _evidence(
            "TRD-02",
            "spread_bps",
            spread,
            spread,
            spread_point,
            pessimistic_value=spread_pessimistic,
            observed=spread is not None,
            fallback_source="explicit_spread_prior" if spread is None else None,
        )
    )
    point += spread_point
    pessimistic += spread_pessimistic
    fresh_score = _curve(freshness, ((0.0, 25.0), (300.0, 25.0), (3600.0, 20.0), (7200.0, 10.0), (9000.0, 0.0)))
    evidence.append(_evidence("TRD-03", "data_freshness_sec", freshness, freshness, fresh_score))
    point += fresh_score
    pessimistic += fresh_score
    depth = _read(feat, "depth_10bps_usd")
    if depth is None:
        depth_point, depth_pessimistic, depth_ratio = 0.0, -10.0, None
    else:
        depth_ratio = depth / policy.min_depth_10bps_usd
        depth_point = depth_pessimistic = _curve(
            depth_ratio, ((0.0, -10.0), (0.1, -10.0), (0.25, 0.0), (1.0, 5.0), (3.0, 10.0))
        )
    evidence.append(
        _evidence(
            "TRD-04",
            "depth_10bps_usd",
            depth,
            depth_ratio,
            depth_point,
            pessimistic_value=depth_pessimistic,
            observed=depth is not None,
        )
    )
    point += depth_point
    pessimistic += depth_pessimistic
    return _finalize("tradability", "n/a", point, pessimistic, evidence)


def _score_total_for_family(
    feat: Mapping[str, object],
    family: ComponentScore,
    policy: ContinuousScoringPolicy,
) -> ComponentScore:
    tradability = _score_tradability(feat, policy)
    if not family.valid or family.direction is None:
        return _invalid("total", family.invalid_reason or "missing_directional_family")
    if not tradability.valid:
        return _invalid("total", tradability.invalid_reason or "invalid_tradability")
    evidence = [*family.evidence, *tradability.evidence]
    volume = _read(feat, "quote_volume_24h")
    freshness = _read(feat, "data_freshness_sec")
    assert volume is not None and freshness is not None
    volume_ratio = volume / policy.min_volume_24h_usdt
    liquidity = _curve(volume_ratio, ((1.0, 40.0), (2.0, 60.0), (5.0, 75.0), (10.0, 90.0), (20.0, 90.0)))
    evidence.append(_evidence("ALLOC-01", "quote_volume_24h", volume, volume_ratio, liquidity))
    spread = _read(feat, "spread_bps")
    cost_point = 5.0 if spread is None else min(20.0, max(0.0, spread) * 0.5)
    cost_pessimistic = 20.0 if spread is None else cost_point
    evidence.append(
        _evidence(
            "ALLOC-02",
            "spread_bps",
            spread,
            spread,
            -cost_point,
            pessimistic_value=-cost_pessimistic,
            observed=spread is not None,
            fallback_source="10bps_cost_prior" if spread is None else None,
        )
    )

    late_point = late_pessimistic = 0.0
    for curve_id, key, anchors, missing_penalty in (
        ("ALLOC-03a", "return_1h", ((2.0, 0.0), (3.0, 4.0), (4.0, 8.0), (5.0, 11.5), (6.0, 15.0)), 15.0),
        ("ALLOC-03b", "return_4h", ((5.0, 0.0), (7.0, 2.5), (8.5, 5.0), (10.0, 7.5), (12.0, 10.0)), 10.0),
        ("ALLOC-03c", "rsi_14", ((22.0, 0.0), (28.0, 2.5), (31.5, 5.0), (35.0, 7.5), (40.0, 10.0)), 10.0),
    ):
        raw = _read(feat, key)
        transformed = None if raw is None else (abs(raw - 50.0) if key == "rsi_14" else abs(raw))
        penalty = 0.0 if transformed is None else _curve(transformed, anchors)
        evidence.append(
            _evidence(
                curve_id,
                key,
                raw,
                transformed,
                -penalty,
                pessimistic_value=-(missing_penalty if raw is None else penalty),
                observed=raw is not None,
            )
        )
        late_point += penalty
        late_pessimistic += missing_penalty if raw is None else penalty
    late_point = min(30.0, late_point)
    late_pessimistic = min(30.0, late_pessimistic)

    rsi = _read(feat, "rsi_14")
    ret_4h = _read(feat, "return_4h")
    if rsi is None or ret_4h is None:
        crowding_point, crowding_pessimistic = 0.0, 15.0
    else:
        directional = 1.0 if family.direction == "long" else -1.0
        rsi_activation = _curve(abs(rsi - 50.0), ((15.0, 0.0), (20.0, 0.7302967433), (30.0, 1.0)))
        return_activation = _curve(directional * ret_4h, ((2.0, 0.0), (3.0, 0.7302967433), (5.0, 1.0)))
        crowding_point = crowding_pessimistic = 15.0 * rsi_activation * return_activation
    evidence.append(
        _evidence(
            "ALLOC-04",
            "rsi_14",
            rsi,
            abs(rsi - 50.0) if rsi is not None else None,
            -crowding_point,
            pessimistic_value=-crowding_pessimistic,
            observed=rsi is not None and ret_4h is not None,
            fallback_source="return_4h" if ret_4h is not None else None,
            kind="interaction",
        )
    )
    evidence.append(
        _evidence(
            "ALLOC-04",
            "return_4h",
            ret_4h,
            abs(ret_4h) if ret_4h is not None else None,
            -crowding_point,
            pessimistic_value=-crowding_pessimistic,
            observed=rsi is not None and ret_4h is not None,
            fallback_source="rsi_14" if rsi is not None else None,
            kind="interaction_input",
        )
    )

    stale_penalty = _curve(freshness, ((1200.0, 0.0), (1800.0, 2.5), (2400.0, 5.0), (3600.0, 7.5), (5400.0, 10.0)))
    evidence.append(_evidence("ALLOC-05", "data_freshness_sec", freshness, freshness, -stale_penalty))
    point = (
        0.35 * family.point_score * policy.external_opportunity_multiplier
        + 0.25 * tradability.point_score
        + 0.15 * liquidity
        - cost_point
        - late_point
        - crowding_point
        - stale_penalty
    )
    pessimistic = (
        0.35 * family.pessimistic_score * policy.external_opportunity_multiplier
        + 0.25 * tradability.pessimistic_score
        + 0.15 * liquidity
        - cost_pessimistic
        - late_pessimistic
        - crowding_pessimistic
        - stale_penalty
    )
    return _finalize("total", family.direction, point, pessimistic, evidence)


def _candidate_core(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
) -> CandidateScore:
    validation_error = _feature_validation_error(feat)
    if validation_error is not None:
        return CandidateScore(
            CONTINUOUS_SCORER_BEHAVIOR_VERSION,
            None,
            None,
            None,
            None,
            0.0,
            0.0,
            False,
            validation_error,
            (),
            (),
        )
    families = score_all_continuous_families(feat)
    eligible = [item for item in families if item.valid and item.direction is not None]
    if not eligible:
        return CandidateScore(
            CONTINUOUS_SCORER_BEHAVIOR_VERSION,
            None,
            None,
            None,
            None,
            0.0,
            0.0,
            False,
            "no_valid_directional_family",
            (),
            (),
        )
    point_family = max(eligible, key=lambda item: item.point_score)
    pessimistic_family = max(eligible, key=lambda item: item.pessimistic_score)
    point_total = _score_total_for_family(feat, point_family, policy)
    pessimistic_total = _score_total_for_family(feat, pessimistic_family, policy)
    if not point_total.valid or not pessimistic_total.valid:
        reason = point_total.invalid_reason or pessimistic_total.invalid_reason
        return CandidateScore(
            CONTINUOUS_SCORER_BEHAVIOR_VERSION,
            point_family.component_id,
            pessimistic_family.component_id,
            point_family.direction,
            pessimistic_family.direction,
            0.0,
            0.0,
            False,
            reason,
            point_total.evidence,
            pessimistic_total.evidence,
        )
    pessimistic_score = min(point_total.point_score, pessimistic_total.pessimistic_score)
    return CandidateScore(
        CONTINUOUS_SCORER_BEHAVIOR_VERSION,
        point_family.component_id,
        pessimistic_family.component_id,
        point_family.direction,
        pessimistic_family.direction,
        point_total.point_score,
        pessimistic_score,
        True,
        None,
        point_total.evidence,
        pessimistic_total.evidence,
    )


def _dynamic_anchors(
    key: str,
    policy: ContinuousScoringPolicy,
) -> tuple[float, ...] | None:
    if key == "quote_volume_24h":
        return tuple(policy.min_volume_24h_usdt * ratio for ratio in (0.0, 1.0, 2.0, 5.0, 10.0, 20.0))
    if key == "depth_10bps_usd":
        return tuple(policy.min_depth_10bps_usd * ratio for ratio in (0.0, 0.1, 0.25, 1.0, 3.0))
    return _FEATURE_ANCHORS.get(key)


def _normalized_perturbations(
    value: float,
    anchors: Sequence[float],
    half_step: float = 0.05,
) -> tuple[float, float, float]:
    """Return raw perturbations and their central/one-sided divisor."""

    def from_z(z_coord: float) -> float:
        z_coord = min(float(len(anchors) - 1), max(0.0, z_coord))
        lower = min(int(math.floor(z_coord)), len(anchors) - 2)
        fraction = z_coord - lower
        return anchors[lower] + fraction * (anchors[lower + 1] - anchors[lower])

    if value <= anchors[0]:
        return anchors[0], from_z(half_step), 1.0
    if value >= anchors[-1]:
        return from_z(float(len(anchors) - 1) - half_step), anchors[-1], 1.0

    index = 0
    for candidate in range(len(anchors) - 1):
        if anchors[candidate] <= value <= anchors[candidate + 1]:
            index = candidate
            break
    width = anchors[index + 1] - anchors[index]
    z_value = index + (value - anchors[index]) / width
    return from_z(z_value - half_step), from_z(z_value + half_step), 2.0


def _neutral_value(key: str, policy: ContinuousScoringPolicy) -> float:
    if key == "quote_volume_24h":
        return policy.min_volume_24h_usdt
    if key == "depth_10bps_usd":
        return policy.min_depth_10bps_usd
    return _FEATURES[key].neutral_value


def _feature_sensitivity(
    key: str,
    observed: float,
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
    base: CandidateScore,
) -> FeatureSensitivity | None:
    declaration = _FEATURES.get(key)
    if declaration is None:
        raise InvalidFeature(f"undeclared_raw_feature:{key}")
    anchors = _dynamic_anchors(key, policy)
    if anchors is None:
        return None

    lower, upper, divisor = _normalized_perturbations(observed, anchors)
    local_sensitivity = 0.0
    if lower != upper:
        low_feat = dict(feat)
        high_feat = dict(feat)
        low_feat[key] = lower
        high_feat[key] = upper
        low_score = _candidate_core(low_feat, policy)
        high_score = _candidate_core(high_feat, policy)
        if low_score.valid and high_score.valid:
            local_sensitivity = abs(high_score.point_score - low_score.point_score) / divisor

    neutral_feat = dict(feat)
    neutral_feat[key] = _neutral_value(key, policy)
    neutral = _candidate_core(neutral_feat, policy)
    neutral_delta = abs(base.point_score - neutral.point_score) if neutral.valid else None
    return FeatureSensitivity(
        key,
        declaration.evidence_group_id,
        observed,
        0.05,
        local_sensitivity,
        neutral_delta,
        neutral.valid,
        declaration.influence_budget,
        neutral_delta is not None and neutral_delta > declaration.influence_budget + 1e-9,
    )


def _candidate_sensitivities(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
    base: CandidateScore,
) -> tuple[FeatureSensitivity, ...]:
    active_keys = {item.raw_feature_id for item in (*base.point_evidence, *base.pessimistic_evidence)}
    sensitivities: list[FeatureSensitivity] = []
    for key in sorted(active_keys):
        if key not in _FEATURES:
            raise InvalidFeature(f"undeclared_raw_feature:{key}")
        observed = _read(feat, key)
        if observed is None:
            continue
        sensitivity = _feature_sensitivity(key, observed, feat, policy, base)
        if sensitivity is not None:
            sensitivities.append(sensitivity)
    return tuple(sensitivities)


def _summarize_sensitivities(
    sensitivities: Sequence[FeatureSensitivity],
) -> tuple[tuple[tuple[str, float], ...], int, float | None, tuple[str, ...]]:
    group_values: dict[str, float] = {}
    for sensitivity in sensitivities:
        group_values[sensitivity.evidence_group_id] = group_values.get(sensitivity.evidence_group_id, 0.0) + abs(
            sensitivity.local_score_sensitivity
        )

    observed_signal_groups = {
        item.evidence_group_id
        for item in sensitivities
        if item.evidence_group_id in _SIGNAL_GROUPS
        and (abs(item.local_score_sensitivity) > 1e-12 or abs(item.score_delta_to_neutral or 0.0) > 1e-12)
    }
    signal_values = [value for key, value in group_values.items() if key in _SIGNAL_GROUPS and value > 1e-12]
    signal_total = sum(signal_values)
    concentration = max(signal_values) / signal_total if signal_total > 0 else None

    flags: set[str] = set()
    if len(observed_signal_groups) < 2:
        flags.add("single_source_evidence")
    elif concentration is not None and concentration > 0.50:
        flags.add("evidence_concentrated")
    if any(item.budget_exceeded for item in sensitivities):
        flags.add("raw_feature_budget_exceeded")
    if any(not item.neutral_comparison_valid for item in sensitivities):
        flags.add("neutral_comparison_unavailable")
    return (
        tuple(sorted(group_values.items())),
        len(observed_signal_groups),
        concentration,
        tuple(sorted(flags)),
    )


def _group_evidence(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
    base: CandidateScore,
    sensitivities: Sequence[FeatureSensitivity],
) -> tuple[EvidenceGroupSensitivity, ...]:
    groups: dict[str, list[FeatureSensitivity]] = {}
    for sensitivity in sensitivities:
        groups.setdefault(sensitivity.evidence_group_id, []).append(sensitivity)

    results: list[EvidenceGroupSensitivity] = []
    for group_id, members in sorted(groups.items()):
        raw_feature_ids = tuple(sorted(item.raw_feature_id for item in members))
        local_total = sum(abs(item.local_score_sensitivity) for item in members)
        comparison_valid = all(item.neutral_comparison_valid for item in members)
        group_delta: float | None = None
        if comparison_valid:
            neutral_feat = dict(feat)
            for item in members:
                neutral_feat[item.raw_feature_id] = _neutral_value(item.raw_feature_id, policy)
            neutral = _candidate_core(neutral_feat, policy)
            comparison_valid = neutral.valid
            if comparison_valid:
                group_delta = abs(base.point_score - neutral.point_score)
        results.append(
            EvidenceGroupSensitivity(
                evidence_group_id=group_id,
                raw_feature_ids=raw_feature_ids,
                aggregate_abs_local_score_sensitivity=local_total,
                score_delta_to_joint_neutral=group_delta,
                neutral_comparison_valid=comparison_valid,
            )
        )
    return tuple(results)


def _joint_neutral_diagnostics(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy,
    base: CandidateScore,
    sensitivities: Sequence[FeatureSensitivity],
    group_evidence: Sequence[EvidenceGroupSensitivity],
) -> tuple[float | None, float | None]:
    if not sensitivities:
        return 0.0, 0.0
    if any(not sensitivity.neutral_comparison_valid for sensitivity in sensitivities):
        return None, None
    if any(not group.neutral_comparison_valid for group in group_evidence):
        return None, None
    comparable = [
        sensitivity
        for sensitivity in sensitivities
        if sensitivity.neutral_comparison_valid and sensitivity.score_delta_to_neutral is not None
    ]
    if not comparable:
        return None, None
    neutral_feat = dict(feat)
    for sensitivity in comparable:
        neutral_feat[sensitivity.raw_feature_id] = _neutral_value(sensitivity.raw_feature_id, policy)
    joint_neutral = _candidate_core(neutral_feat, policy)
    if not joint_neutral.valid:
        return None, None
    joint_delta = abs(base.point_score - joint_neutral.point_score)
    group_delta_sum = sum(group.score_delta_to_joint_neutral or 0.0 for group in group_evidence)
    return joint_delta, joint_delta - group_delta_sum


def evaluate_continuous_candidate(
    feat: Mapping[str, object],
    policy: ContinuousScoringPolicy | None = None,
) -> CandidateScore:
    """Return a complete pure point/pessimistic candidate audit.

    The function never mutates ``feat`` and has no persistence or authority side
    effect.  Sensitivity perturbs the complete candidate score once per raw
    feature, so repeated consumers are not counted as independent derivatives.
    """

    policy = policy or ContinuousScoringPolicy()
    base = _candidate_core(dict(feat), policy)
    if not base.valid:
        return base

    try:
        sensitivities = _candidate_sensitivities(feat, policy, base)
    except InvalidFeature as exc:
        return replace(base, valid=False, invalid_reason=str(exc))
    group_values, observed_group_count, concentration, flags = _summarize_sensitivities(sensitivities)
    group_evidence = _group_evidence(feat, policy, base, sensitivities)
    if any(not group.neutral_comparison_valid for group in group_evidence):
        flags = tuple(sorted({*flags, "group_neutral_comparison_unavailable"}))
    joint_neutral_delta, interaction_residual = _joint_neutral_diagnostics(
        feat,
        policy,
        base,
        sensitivities,
        group_evidence,
    )
    if interaction_residual is None:
        flags = tuple(sorted({*flags, "interaction_residual_unavailable"}))
    return replace(
        base,
        sensitivities=sensitivities,
        group_sensitivities=group_values,
        group_evidence=group_evidence,
        observed_signal_group_count=observed_group_count,
        concentration_share=concentration,
        flags=flags,
        joint_neutral_score_delta=joint_neutral_delta,
        interaction_residual=interaction_residual,
    )


__all__ = [
    "CONTINUOUS_SCORER_BEHAVIOR_VERSION",
    "CandidateScore",
    "ComponentScore",
    "ContinuousScoringPolicy",
    "EvidenceGroupSensitivity",
    "FactorEvidence",
    "FeatureSensitivity",
    "InvalidFeature",
    "RawFeatureDeclaration",
    "evaluate_continuous_candidate",
    "raw_feature_declarations",
    "score_all_continuous_families",
    "score_continuous_family",
    "shape_preserving_curve",
]
