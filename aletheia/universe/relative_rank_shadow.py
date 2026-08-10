"""Pure, non-authoritative cross-sectional Universe challenger."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from aletheia.config.schema import UniverseConfig
from aletheia.universe.allocator import allocate_universe
from aletheia.universe.continuous_scoring import (
    ContinuousScoringPolicy,
    score_all_continuous_families,
)
from aletheia.universe.filters import apply_fine_tradability_filters
from aletheia.universe.twin_scoring import TwinShadowInput


RELATIVE_RANK_BEHAVIOR_VERSION = "universe-relative-rank-shadow-v1"
RELATIVE_RANK_RECORD_VERSION = "universe-relative-rank-record-v1"
DEFAULT_MIN_CANDIDATES = 20
_DERIVATIVE_MARKETS = frozenset({"perp", "swap", "futures", "usdt_futures"})
_FAMILY_ORDER = (
    "momentum",
    "mean_reversion",
    "volatility",
    "derivatives",
    "event",
    "short_bias",
)


@dataclass(frozen=True)
class RelativeRankRecord:
    scanner_run_id: str
    candidate_key: str
    feature_snapshot_id: str | None
    asset_id: str | None
    symbol: str
    market_type: str
    family: str | None
    direction: str | None
    authority_total_score: float
    family_score: float | None
    opportunity_rank: float | None = None
    executability_rank: float | None = None
    robustness_rank: float | None = None
    positioning_rank: float | None = None
    relative_score: float | None = None
    eligible: bool = False
    invalid_reason: str | None = None
    authority_selected: bool | None = None
    relative_selected: bool | None = None


@dataclass(frozen=True)
class RelativeRankBatch:
    scanner_run_id: str
    record_version: str
    behavior_version: str
    min_candidate_count: int
    candidate_count: int
    eligible_count: int
    available: bool
    unavailable_reason: str | None
    records: tuple[RelativeRankRecord, ...]


def _finite(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or (positive and parsed <= 0):
        return None
    return parsed


def _candidate_key(scanner_run_id: str, feature: Mapping[str, object]) -> str:
    source_id = feature.get("feature_snapshot_id") or feature.get("asset_id") or "missing"
    return (
        f"relative-rank:{scanner_run_id}:{source_id}:"
        f"{feature.get('market_type') or 'spot'}:{feature.get('symbol') or 'missing'}"
    )


def _percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """Return bounded empirical percentiles with average ranks for ties."""

    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    if len(ordered) == 1:
        return {ordered[0][0]: 50.0}
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = ((index + 1) + end) / 2.0
        percentile = 100.0 * (average_rank - 1.0) / (len(ordered) - 1)
        for key, _value in ordered[index:end]:
            result[key] = percentile
        index = end
    return result


def _mean(*values: float | None) -> float | None:
    observed = [value for value in values if value is not None]
    return sum(observed) / len(observed) if observed else None


def _base_record(
    item: TwinShadowInput,
    *,
    scanner_run_id: str,
    policy: ContinuousScoringPolicy,
) -> tuple[RelativeRankRecord, dict[str, float]]:
    feature = item.feature
    symbol = str(feature.get("symbol") or "")
    market_type = str(feature.get("market_type") or "spot").lower()
    candidate_key = _candidate_key(scanner_run_id, feature)
    identity = {
        "scanner_run_id": scanner_run_id,
        "candidate_key": candidate_key,
        "feature_snapshot_id": (
            str(feature["feature_snapshot_id"]) if feature.get("feature_snapshot_id") is not None else None
        ),
        "asset_id": str(feature["asset_id"]) if feature.get("asset_id") is not None else None,
        "symbol": symbol,
        "market_type": market_type,
        "authority_total_score": float(item.authority_total_score),
    }
    required = {
        "quote_volume_24h": _finite(feature.get("quote_volume_24h"), positive=True),
        "spread_bps": _finite(feature.get("spread_bps")),
        "depth_10bps_usd": _finite(feature.get("depth_10bps_usd"), positive=True),
        "data_freshness_sec": _finite(feature.get("data_freshness_sec")),
    }
    missing = next((key for key, value in required.items() if value is None), None)
    if not symbol or missing is not None:
        return RelativeRankRecord(
            **identity,
            family=None,
            direction=None,
            family_score=None,
            invalid_reason="missing_identity" if not symbol else f"missing_{missing}",
        ), {}
    if required["spread_bps"] < 0 or required["data_freshness_sec"] < 0:
        return RelativeRankRecord(
            **identity,
            family=None,
            direction=None,
            family_score=None,
            invalid_reason="invalid_tradability_observation",
        ), {}
    if required["quote_volume_24h"] < policy.min_volume_24h_usdt:
        return RelativeRankRecord(
            **identity,
            family=None,
            direction=None,
            family_score=None,
            invalid_reason="below_minimum_volume",
        ), {}
    if required["depth_10bps_usd"] < 0.1 * policy.min_depth_10bps_usd:
        return RelativeRankRecord(
            **identity,
            family=None,
            direction=None,
            family_score=None,
            invalid_reason="critically_shallow_depth",
        ), {}

    family_feature = dict(feature)
    if market_type in _DERIVATIVE_MARKETS:
        for field in (
            "funding_rate",
            "open_interest",
            "oi_delta_1h",
            "long_short_ratio",
            "top_trader_position_ratio",
        ):
            family_feature.pop(field, None)
    families = [
        component
        for component in score_all_continuous_families(family_feature)
        if component.valid and component.direction is not None
    ]
    if not families:
        return RelativeRankRecord(
            **identity,
            family=None,
            direction=None,
            family_score=None,
            invalid_reason="no_valid_directional_family",
        ), {}
    order = {family: index for index, family in enumerate(_FAMILY_ORDER)}
    family = min(
        families,
        key=lambda component: (
            -component.pessimistic_score,
            order.get(component.component_id, len(order)),
            component.component_id,
        ),
    )
    values = {
        "family": family.pessimistic_score,
        "volume": math.log1p(required["quote_volume_24h"]),
        "spread": -required["spread_bps"],
        "depth": math.log1p(required["depth_10bps_usd"]),
        "freshness": -required["data_freshness_sec"],
    }
    if market_type in _DERIVATIVE_MARKETS:
        derivatives = {
            "funding_rate": _finite(feature.get("funding_rate")),
            "open_interest": _finite(feature.get("open_interest"), positive=True),
            "oi_delta_1h": _finite(feature.get("oi_delta_1h")),
            "long_short_ratio": _finite(feature.get("long_short_ratio"), positive=True),
            "top_trader_position_ratio": _finite(feature.get("top_trader_position_ratio"), positive=True),
        }
        missing_derivative = next((key for key, value in derivatives.items() if value is None), None)
        if missing_derivative is not None:
            return RelativeRankRecord(
                **identity,
                family=family.component_id,
                direction=family.direction,
                family_score=family.pessimistic_score,
                invalid_reason=f"missing_{missing_derivative}",
            ), {}
        direction = 1.0 if family.direction == "long" else -1.0
        values.update(
            {
                "open_interest": math.log1p(derivatives["open_interest"]),
                "funding_alignment": -direction * derivatives["funding_rate"],
                "oi_participation": derivatives["oi_delta_1h"],
                "global_crowding_alignment": (-direction * math.log(derivatives["long_short_ratio"])),
                "top_position_alignment": (direction * math.log(derivatives["top_trader_position_ratio"])),
            }
        )
    return RelativeRankRecord(
        **identity,
        family=family.component_id,
        direction=family.direction,
        family_score=family.pessimistic_score,
        eligible=True,
    ), values


def build_relative_rank_batch(
    inputs: Sequence[TwinShadowInput],
    *,
    scanner_run_id: str,
    policy: ContinuousScoringPolicy | None = None,
    min_candidate_count: int = DEFAULT_MIN_CANDIDATES,
) -> RelativeRankBatch:
    """Build one frozen scanner-run cross-section without authority effects."""

    if min_candidate_count < 2:
        raise ValueError("min_candidate_count must be at least 2")
    policy = policy or ContinuousScoringPolicy()
    prepared = [_base_record(item, scanner_run_id=scanner_run_id, policy=policy) for item in inputs]
    eligible = [(record, values) for record, values in prepared if record.eligible]
    if len(eligible) < min_candidate_count:
        return RelativeRankBatch(
            scanner_run_id=scanner_run_id,
            record_version=RELATIVE_RANK_RECORD_VERSION,
            behavior_version=RELATIVE_RANK_BEHAVIOR_VERSION,
            min_candidate_count=min_candidate_count,
            candidate_count=len(inputs),
            eligible_count=len(eligible),
            available=False,
            unavailable_reason="minimum_candidate_count_not_met",
            records=tuple(record for record, _values in prepared),
        )

    metrics = sorted({key for _record, values in eligible for key in values})
    ranks = {
        metric: _percentiles({record.candidate_key: values[metric] for record, values in eligible if metric in values})
        for metric in metrics
    }
    completed: dict[str, RelativeRankRecord] = {}
    for record, values in eligible:
        key = record.candidate_key
        family_rank = ranks["family"][key]
        positioning_rank = None
        if record.market_type in _DERIVATIVE_MARKETS:
            positioning_rank = _mean(
                ranks["funding_alignment"].get(key),
                ranks["oi_participation"].get(key),
                ranks["global_crowding_alignment"].get(key),
                ranks["top_position_alignment"].get(key),
            )
        opportunity_rank = _mean(family_rank, positioning_rank)
        executability_rank = _mean(
            ranks["volume"].get(key),
            ranks["spread"].get(key),
            ranks["depth"].get(key),
            ranks.get("open_interest", {}).get(key),
        )
        robustness_rank = ranks["freshness"][key]
        relative_score = 0.50 * opportunity_rank + 0.30 * executability_rank + 0.20 * robustness_rank
        completed[key] = replace(
            record,
            opportunity_rank=opportunity_rank,
            executability_rank=executability_rank,
            robustness_rank=robustness_rank,
            positioning_rank=positioning_rank,
            relative_score=relative_score,
        )
    return RelativeRankBatch(
        scanner_run_id=scanner_run_id,
        record_version=RELATIVE_RANK_RECORD_VERSION,
        behavior_version=RELATIVE_RANK_BEHAVIOR_VERSION,
        min_candidate_count=min_candidate_count,
        candidate_count=len(inputs),
        eligible_count=len(eligible),
        available=True,
        unavailable_reason=None,
        records=tuple(completed.get(record.candidate_key, record) for record, _values in prepared),
    )


def replay_relative_rank_allocation(
    batch: RelativeRankBatch,
    inputs: Sequence[TwinShadowInput],
    *,
    universe_config: UniverseConfig,
    app_config: Any,
    family_tolerances: Mapping[str, float] | None,
    regime_state: str | None,
    allocator_random_state: object,
    dedupe: Callable[[list[dict], Any], list[dict]],
) -> RelativeRankBatch:
    """Replay the existing allocator on copied relative-rank candidates."""

    if not batch.available:
        return batch
    by_key = {record.candidate_key: record for record in batch.records}
    candidates: list[dict] = []
    for item in inputs:
        key = _candidate_key(batch.scanner_run_id, item.feature)
        record = by_key.get(key)
        if record is None or record.relative_score is None:
            continue
        candidates.append(
            {
                **dict(item.feature),
                **dict(item.authority_scanner_result),
                "scanner_family": record.family,
                "scanner_direction": record.direction,
                "total_score": record.relative_score,
                "_relative_rank_candidate_key": key,
            }
        )
    fine_filtered = apply_fine_tradability_filters(
        candidates,
        universe_config,
        family_tolerances=dict(family_tolerances or {}),
        scanner_run_id=None,
    )
    unique = dedupe(fine_filtered, app_config)
    replay_rng = random.Random()
    replay_rng.setstate(allocator_random_state)
    selected = allocate_universe(
        unique,
        universe_config,
        regime_state=regime_state,
        explore_sampler=replay_rng.sample,
        decorate_selected=False,
        log_result=False,
    )
    selected_keys = {str(item.get("_relative_rank_candidate_key")) for item in selected}
    return replace(
        batch,
        records=tuple(
            replace(record, relative_selected=record.candidate_key in selected_keys) for record in batch.records
        ),
    )


def annotate_relative_authority_selection(
    batch: RelativeRankBatch,
    authority_selected: Sequence[Mapping[str, object]],
) -> RelativeRankBatch:
    selected = {
        (str(item.get("asset_id") or ""), str(item.get("market_type") or "spot"), str(item.get("symbol") or ""))
        for item in authority_selected
    }
    return replace(
        batch,
        records=tuple(
            replace(
                record,
                authority_selected=(record.asset_id or "", record.market_type, record.symbol) in selected,
            )
            for record in batch.records
        ),
    )


__all__ = [
    "DEFAULT_MIN_CANDIDATES",
    "RELATIVE_RANK_BEHAVIOR_VERSION",
    "RELATIVE_RANK_RECORD_VERSION",
    "RelativeRankBatch",
    "RelativeRankRecord",
    "annotate_relative_authority_selection",
    "build_relative_rank_batch",
    "replay_relative_rank_allocation",
]
