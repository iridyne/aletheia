"""Shared deterministic market trigger state for EntryPlan watchers."""

from __future__ import annotations


from pydantic import BaseModel, Field

from aletheia.data.symbols import InstrumentId
from aletheia.universe.fast_stream import (
    FastMarketEvent,
    fast_market_data_venue_for_config,
    fast_market_stream_available_for_config,
)


class TriggerPolicy(BaseModel):
    max_spread_bps: float = Field(gt=0)
    min_depth_10bps_usd: float = Field(gt=0)


class TriggerSnapshot(BaseModel):
    observed_at: str
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    spread_bps: float = Field(ge=0)
    depth_10bps_usd: float = Field(ge=0)
    taker_buy_ratio: float = Field(ge=0, le=1)
    quote_exchange_timestamp_ms: int = Field(ge=0)
    quote_received_timestamp_ms: int = Field(ge=0)
    trade_exchange_timestamp_ms: int = Field(ge=0)
    trade_received_timestamp_ms: int = Field(ge=0)
    depth_exchange_timestamp_ms: int = Field(ge=0)
    depth_received_timestamp_ms: int = Field(ge=0)
    quote_sequence: int = Field(ge=0)
    trade_sequence: int = Field(ge=0)
    depth_sequence: int = Field(ge=0)
    stream_generation_id: str | None = None
    subscription_generation_id: str | None = None
    depth_bids: tuple[tuple[float, float], ...] = ()
    depth_asks: tuple[tuple[float, float], ...] = ()
    entry_trigger: dict = Field(default_factory=dict)
    market_structure: dict = Field(default_factory=dict)


class TriggerReady(TriggerSnapshot):
    entry_price: float
    activation_events: tuple[FastMarketEvent, ...] = ()


def validate_fast_trigger_stream(cfg) -> str | None:
    if not getattr(cfg.fast_trigger, "enabled", True):
        return "fast_trigger_disabled"
    if cfg.agent.permission_mode not in {"Paper", "Full"}:
        return "entry_plan_permission_revoked"
    mode = str(cfg.exchange.paper_execution_mode or "")
    if "live" in mode:
        return "fast_trigger_live_forbidden"
    if not fast_market_stream_available_for_config(cfg):
        venue = fast_market_data_venue_for_config(cfg)
        return f"fast_trigger_stream_unavailable:{venue}"
    return None
