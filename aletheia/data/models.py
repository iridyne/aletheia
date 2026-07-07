"""Domain models for the market data collection layer.

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


class PerpStructureSnapshot(BaseModel):
    """Observation-only perp positioning map from a whitelisted aggregate feed."""
    symbol: str
    venue: str = "hyperliquid"
    source: str = "vergex"
    as_of_time: str
    observed_at: str
    data_quality: str = "unknown"
    coverage_note: str | None = None
    poc: float | None = None
    value_area_low: float | None = None
    value_area_high: float | None = None
    support: float | None = None
    resistance: float | None = None
    magnet: float | None = None
    short_liq_above_usd: float | None = None
    long_liq_below_usd: float | None = None
    long_unrealized_pnl_usd: float | None = None
    short_unrealized_pnl_usd: float | None = None
    top10_concentration: float | None = None
    top50_concentration: float | None = None
    holder_count: int | None = None
    long_holder_count: int | None = None
    short_holder_count: int | None = None
    cgo_direction: str | None = None
    vap_direction: str | None = None
    lfa_direction: str | None = None
    cascade_risk: float | None = None
    convexity: float | None = None
    vendor_bias: str | None = None
    vendor_rank: int | None = None
    vendor_universe_size: int | None = None
    raw_json: str = "{}"


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
    # Round 2 macro signals (Step 2 Fear & Greed + Step 3 CoinGecko top movers).
    fng_value: int | None = None  # 0-100 Alt.me index
    fng_classification: str | None = None  # "Extreme Fear" / "Fear" / "Neutral" / "Greed" / "Extreme Greed"
    top_movers_summary: str | None = None  # short pre-formatted "Top Gainers: ... | Top Losers: ..."
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
