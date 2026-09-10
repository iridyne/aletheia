"""Registered deterministic TriggerSpec contracts; never interpret model prose."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable


@dataclass(frozen=True)
class TriggerSpecContract:
    spec_id: str
    data_requirements: tuple[str, ...]
    freshness_ms: int
    validate_params: Callable[[dict], dict]


ANCHOR_GEOMETRY_VERSION = "anchor-retrace-reclaim-geometry/v1"
TRIGGER_RULES = {
    "reclaim_confirmation_ms": 300,
    "trade_window_ms": 10_000,
    "quote_depth_max_age_ms": 1_000,
    "trade_max_age_ms": 2_000,
    "depth_mid_fraction": 0.001,
    "long_min_buy_ratio": 0.60,
    "short_max_buy_ratio": 0.40,
}

ANCHOR_PARAM_KEYS = {
    "breakout_level", "pullback_low", "pullback_high", "entry_low", "entry_high",
    "invalidation_price", "sweep_low", "sweep_high", "atr_abs", "geometry_version",
    "geometry_fingerprint",
}


def _anchor_retrace_params(raw: dict) -> dict:
    if set(raw) != ANCHOR_PARAM_KEYS:
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
    if raw["geometry_version"] != ANCHOR_GEOMETRY_VERSION:
        raise ValueError("anchor_geometry_version_invalid")
    fingerprint = raw["geometry_fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError("momentum_geometry_fingerprint_invalid")
    return {**params, "geometry_version": raw["geometry_version"], "geometry_fingerprint": fingerprint}


def trigger_contract_material(spec: TriggerSpecContract) -> dict:
    """Consumed parameters and rules; callable layout is not a permission."""
    return {
        "id": spec.spec_id, "channels": spec.data_requirements,
        "freshness_ms": spec.freshness_ms,
        "parameter_keys": sorted(ANCHOR_PARAM_KEYS),
        "geometry_version": ANCHOR_GEOMETRY_VERSION,
        "rules": TRIGGER_RULES,
    }


_REGISTRY = {
    "anchor-retrace-reclaim": TriggerSpecContract(
        spec_id="anchor-retrace-reclaim",
        data_requirements=("quote", "trade", "books5", "candle1m"),
        freshness_ms=2_000,
        validate_params=_anchor_retrace_params,
    ),
}


def registered_trigger_specs() -> tuple[TriggerSpecContract, ...]:
    return tuple(_REGISTRY.values())


def require_registered_trigger_spec(
    spec_id: str,
    params: dict,
) -> tuple[TriggerSpecContract, dict]:
    try:
        contract = _REGISTRY[spec_id]
    except KeyError as exc:
        raise ValueError("trigger_spec_not_registered") from exc
    return contract, contract.validate_params(params)
