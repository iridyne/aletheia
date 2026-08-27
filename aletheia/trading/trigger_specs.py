"""Registered deterministic TriggerSpec contracts; never interpret model prose."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TriggerSpecContract:
    spec_id: str
    version: str
    data_requirements: tuple[str, ...]
    freshness_ms: int
    validate_params: Callable[[dict], dict]


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
    params = {key: float(value) for key, value in raw.items()}
    if any(value <= 0 for value in params.values()):
        raise ValueError("momentum_trigger_params_invalid")
    if params["pullback_low"] > params["pullback_high"]:
        raise ValueError("momentum_pullback_band_invalid")
    if params["entry_low"] > params["entry_high"]:
        raise ValueError("momentum_entry_zone_invalid")
    return params


_REGISTRY = {
    ("momentum-retrace-reclaim", "v1"): TriggerSpecContract(
        spec_id="momentum-retrace-reclaim",
        version="v1",
        data_requirements=("quote", "trade", "books5", "candle1m"),
        freshness_ms=2_000,
        validate_params=_momentum_params,
    ),
}


def registered_trigger_specs() -> tuple[TriggerSpecContract, ...]:
    return tuple(_REGISTRY.values())


def require_registered_trigger_spec(
    spec_id: str,
    version: str,
    params: dict,
) -> tuple[TriggerSpecContract, dict]:
    try:
        contract = _REGISTRY[(spec_id, version)]
    except KeyError as exc:
        raise ValueError("trigger_spec_not_registered") from exc
    return contract, contract.validate_params(params)
