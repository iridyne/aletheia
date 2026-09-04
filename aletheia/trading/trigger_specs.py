"""Registered deterministic TriggerSpec contracts; never interpret model prose."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable


@dataclass(frozen=True)
class TriggerSpecContract:
    spec_id: str
    candidate_families: tuple[str, ...]
    data_requirements: tuple[str, ...]
    freshness_ms: int
    validate_params: Callable[[dict], dict]
    planner_visible: bool = True


def _momentum_params(raw: dict) -> dict:
    allowed = {
        "breakout_level",
        "pullback_low",
        "pullback_high",
        "entry_low",
        "entry_high",
        "invalidation_price",
    }
    if set(raw) != allowed:
        raise ValueError("momentum_trigger_params_incomplete")
    try:
        params = {key: float(value) for key, value in raw.items()}
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("momentum_trigger_params_invalid") from exc
    if any(not math.isfinite(value) or value <= 0 for value in params.values()):
        raise ValueError("momentum_trigger_params_invalid")
    if params["pullback_low"] > params["pullback_high"]:
        raise ValueError("momentum_pullback_band_invalid")
    if params["entry_low"] > params["entry_high"]:
        raise ValueError("momentum_entry_zone_invalid")
    return params


def _momentum_geometry_params(raw: dict) -> dict:
    allowed = {
        "breakout_level", "pullback_low", "pullback_high", "entry_low", "entry_high",
        "invalidation_price", "sweep_low", "sweep_high", "atr_abs", "geometry_version",
        "geometry_fingerprint",
    }
    if set(raw) != allowed:
        raise ValueError("momentum_trigger_params_incomplete")
    try:
        params = {
            key: float(value)
            for key, value in raw.items()
            if key not in {"geometry_version", "geometry_fingerprint"}
        }
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("momentum_trigger_params_invalid") from exc
    if any(not math.isfinite(value) or value <= 0 for value in params.values()):
        raise ValueError("momentum_trigger_params_invalid")
    if params["pullback_low"] > params["pullback_high"]:
        raise ValueError("momentum_pullback_band_invalid")
    if params["entry_low"] > params["entry_high"]:
        raise ValueError("momentum_entry_zone_invalid")
    if params["sweep_low"] > params["sweep_high"]:
        raise ValueError("momentum_sweep_band_invalid")
    if raw["geometry_version"] != "momentum-entry-geometry/v2":
        raise ValueError("momentum_geometry_version_invalid")
    fingerprint = raw["geometry_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError("momentum_geometry_fingerprint_invalid")
    return {**params, "geometry_version": raw["geometry_version"], "geometry_fingerprint": fingerprint}


def _anchor_retrace_params(raw: dict) -> dict:
    if raw.get("geometry_version") != "anchor-retrace-reclaim-geometry/v1":
        raise ValueError("anchor_geometry_version_invalid")
    current = {key: value for key, value in raw.items() if key != "geometry_version"}
    params = _momentum_geometry_params(
        {**current, "geometry_version": "momentum-entry-geometry/v2"}
    )
    params["geometry_version"] = raw["geometry_version"]
    return params


_REGISTRY = {
    "momentum-retrace-reclaim": TriggerSpecContract(
        spec_id="momentum-retrace-reclaim",
        candidate_families=("momentum",),
        data_requirements=("quote", "trade", "books5", "candle1m"),
        freshness_ms=2_000,
        validate_params=_momentum_params,
    ),
    "momentum-retrace-reclaim-geometry": TriggerSpecContract(
        spec_id="momentum-retrace-reclaim-geometry",
        candidate_families=("momentum",),
        data_requirements=("quote", "trade", "books5", "candle1m"),
        freshness_ms=2_000,
        validate_params=_momentum_geometry_params,
        planner_visible=False,
    ),
    "anchor-retrace-reclaim": TriggerSpecContract(
        spec_id="anchor-retrace-reclaim",
        candidate_families=("momentum",),
        data_requirements=("quote", "trade", "books5", "candle1m"),
        freshness_ms=2_000,
        validate_params=_anchor_retrace_params,
        planner_visible=False,
    ),
}


def registered_trigger_specs() -> tuple[TriggerSpecContract, ...]:
    return tuple(_REGISTRY.values())


def compatible_trigger_specs(candidate_family: str) -> tuple[TriggerSpecContract, ...]:
    family = str(candidate_family)
    return tuple(
        contract
        for contract in _REGISTRY.values()
        if family in contract.candidate_families and contract.planner_visible
    )


def require_compatible_trigger_spec(
    candidate_family: str,
    spec_id: str,
) -> TriggerSpecContract:
    try:
        contract = _REGISTRY[spec_id]
    except KeyError as exc:
        raise ValueError("trigger_spec_not_registered") from exc
    if str(candidate_family) not in contract.candidate_families:
        raise ValueError("trigger_spec_candidate_family_unsupported")
    if not contract.planner_visible:
        raise ValueError("trigger_spec_not_planner_visible")
    return contract


def require_registered_trigger_spec(
    spec_id: str,
    params: dict,
) -> tuple[TriggerSpecContract, dict]:
    try:
        contract = _REGISTRY[spec_id]
    except KeyError as exc:
        raise ValueError("trigger_spec_not_registered") from exc
    return contract, contract.validate_params(params)
