"""Persisted observation-only options-volatility contract."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VERSION = "deribit_mark_iv_forward_delta_v1"
DELTA_CONVENTION = "black_undiscounted_forward_non_premium_adjusted_act365.25"


class OptionsVolatilitySnapshot(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    symbol: Literal["BTC", "ETH"]
    source: Literal["deribit_public"] = "deribit_public"
    authority: Literal["observation_only"] = "observation_only"
    derivation_version: str = VERSION
    delta_convention: str = DELTA_CONVENTION
    acquired_at: str
    source_time: str | None = None
    tenors: dict = Field(default_factory=dict)
    atm_iv_7d: float | None = None
    atm_iv_30d: float | None = None
    atm_iv_60d: float | None = None
    term_slope_7d_30d: float | None = None
    term_slope_30d_60d: float | None = None
    call_25d_iv_30d: float | None = None
    put_25d_iv_30d: float | None = None
    risk_reversal_25d_30d: float | None = None
    skew_25d_30d: float | None = None
    volatility_regime: str = "unavailable"
    coverage: dict = Field(default_factory=dict)
    inputs: list[dict] = Field(default_factory=list)
