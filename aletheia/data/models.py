"""Domain models for the 9-domain data collection layer.

All models are Pydantic BaseModel — serialization-ready for SQLite JSON columns,
API responses, and LLM context injection. Fields use snake_case; observed_at is
always an ISO-8601 UTC string.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrderbookSnapshot(BaseModel):
    symbol: str
    exchange: str
    bid_price: float
    ask_price: float
    spread_bps: float
    bid_depth_10bps_usd: float
    ask_depth_10bps_usd: float
    total_depth_10bps_usd: float
    imbalance_ratio: float | None = None
    observed_at: str
    fetch_latency_ms: int | None = None


class TradabilityScore(BaseModel):
    symbol: str
    exchange: str
    spread_bps: float
    depth_10bps_usd: float
    volume_24h_usd: float
    score: float = Field(ge=0, le=100)
    execution_cost_estimate_bps: float
    is_tradable: bool
    observed_at: str


class FundingRateSnapshot(BaseModel):
    symbol: str
    exchange: str
    funding_rate: float
    funding_rate_annualized: float
    next_funding_time: str | None = None
    mark_price: float | None = None
    observed_at: str


class OpenInterestSnapshot(BaseModel):
    symbol: str
    exchange: str
    open_interest_usd: float
    open_interest_coin: float | None = None
    oi_change_1h_pct: float | None = None
    long_short_ratio: float | None = None
    observed_at: str


class LiquidationEvent(BaseModel):
    symbol: str
    exchange: str
    side: str  # "long" | "short"
    quantity_usd: float
    price: float
    event_time: str
    observed_at: str


class LeverageState(BaseModel):
    symbol: str
    funding_rate: float | None = None
    funding_annualized: float | None = None
    open_interest_usd: float | None = None
    oi_delta_1h_pct: float | None = None
    long_short_ratio: float | None = None
    liquidation_1h_usd: float | None = None
    crowding_score: float = Field(default=0, ge=0, le=100)
    funding_opportunity: bool = False
    observed_at: str = ""


class MacroSnapshot(BaseModel):
    btc_dominance: float | None = None
    total_market_cap_usd: float | None = None
    total_market_cap_change_24h_pct: float | None = None
    spy_close: float | None = None
    vix_close: float | None = None
    dxy_close: float | None = None
    nasdaq_close: float | None = None
    risk_regime: str = "neutral"  # risk_on / neutral / risk_off
    observed_at: str = ""


class MarketContextSnapshot(BaseModel):
    id: str
    symbols: list[str] = Field(default_factory=list)
    tradability: dict[str, dict] = Field(default_factory=dict)
    leverage: dict[str, dict] = Field(default_factory=dict)
    macro: dict = Field(default_factory=dict)
    data_completeness: float = 0.0
    stale_domains: list[str] = Field(default_factory=list)
    observed_at: str = ""
