"""Shared deterministic market trigger state for EntryPlan watchers."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from aletheia.data.symbols import InstrumentId
from aletheia.universe.fast_stream import (
    FastMarketEvent,
    events_share_generation,
    fast_market_data_venue_for_config,
    fast_market_stream_available_for_config,
)

TRIGGER_SPEC_VERSION = "momentum-retrace-reclaim/v1"


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


class TriggerEngine:
    """One in-memory state machine. It never writes SQLite or calls an LLM."""

    def __init__(
        self,
        *,
        instrument: InstrumentId,
        direction: Literal["long", "short"],
        breakout_level: float,
        pullback_band: tuple[float, float],
        entry_zone: tuple[float, float],
        policy: TriggerPolicy,
    ):
        self.instrument = instrument
        self.direction = direction
        self.breakout_level = float(breakout_level)
        self.pullback_band = pullback_band
        self.entry_zone = entry_zone
        self.policy = policy
        self.state = "armed"
        self.subscription_confirmed = False
        self.health_reason: str | None = None
        self.final_requirement_reason: str | None = None
        self._sequences: dict[str, int] = {}
        self._quote: FastMarketEvent | None = None
        self._depth: FastMarketEvent | None = None
        self._trades: deque[FastMarketEvent] = deque()
        self._first_reclaim_quote_ms: int | None = None
        self._first_reclaim_quote_sequence: int | None = None
        self._pullback_seen_ms: int | None = None
        self._closed_candles_1m: deque[dict] = deque(maxlen=120)
        self._current_candle_1m: dict | None = None

    def preload_closed_candles(self, candles: list[dict]) -> None:
        """Warm observation-only structure state without touching trigger state."""

        self._closed_candles_1m.clear()
        for candle in candles[-120:]:
            self._closed_candles_1m.append(dict(candle))

    def confirm_subscription(self) -> None:
        self.subscription_confirmed = True
        self.health_reason = None

    def observe(self, event: FastMarketEvent) -> TriggerReady | None:
        if event.instrument != self.instrument:
            self.health_reason = "fast_trigger_instrument_mismatch"
            return None
        previous = self._sequences.get(event.event_type)
        if previous is not None and event.sequence <= previous:
            self.health_reason = f"sequence_anomaly:{event.event_type}"
            return None
        self._sequences[event.event_type] = event.sequence
        if self.health_reason is not None:
            return None
        if event.event_type == "trade":
            self._trades.append(event)
            self._prune_trades(event.received_timestamp_ms)
        elif event.event_type == "depth":
            self._depth = event
        elif event.event_type == "candle" and event.candle is not None:
            open_, high, low, close, volume = event.candle
            candle = {
                "ts": event.exchange_timestamp_ms,
                "open": open_, "high": high, "low": low,
                "close": close, "volume": volume,
            }
            if (
                self._current_candle_1m is not None
                and candle["ts"] > self._current_candle_1m["ts"]
            ):
                self._closed_candles_1m.append(self._current_candle_1m)
            if self._current_candle_1m is None or candle["ts"] >= self._current_candle_1m["ts"]:
                self._current_candle_1m = candle
        elif event.event_type == "quote":
            self._quote = event
            mid = (float(event.bid) + float(event.ask)) / 2
            crossed_to_pullback_side = (
                mid <= self.breakout_level
                if self.direction == "long"
                else mid >= self.breakout_level
            )
            if (
                self.state == "armed"
                and self.pullback_band[0] <= mid <= self.pullback_band[1]
                and crossed_to_pullback_side
            ):
                self.state = "pullback_seen"
                self._pullback_seen_ms = event.received_timestamp_ms
            if self.state == "pullback_seen":
                reclaimed = (
                    mid > self.breakout_level
                    if self.direction == "long"
                    else mid < self.breakout_level
                )
                if not reclaimed:
                    self._first_reclaim_quote_ms = None
                    self._first_reclaim_quote_sequence = None
                elif self._first_reclaim_quote_ms is None:
                    self._first_reclaim_quote_ms = event.received_timestamp_ms
                    self._first_reclaim_quote_sequence = event.sequence
                elif event.received_timestamp_ms - self._first_reclaim_quote_ms >= 300:
                    return self.evaluate(event.received_timestamp_ms)
        return None

    def _prune_trades(self, now_ms: int) -> None:
        cutoff = now_ms - 10_000
        while self._trades and self._trades[0].received_timestamp_ms < cutoff:
            self._trades.popleft()

    def evaluate(self, now_ms: int) -> TriggerReady | None:
        if self.health_reason or not self.subscription_confirmed or self.state != "pullback_seen":
            return None
        quote, depth = self._quote, self._depth
        if quote is None or depth is None or not self._trades:
            self.final_requirement_reason = "required_market_fact_missing"
            return None
        self._prune_trades(now_ms)
        if not self._trades:
            self.final_requirement_reason = "trade_window_empty"
            return None
        if not events_share_generation(quote, depth, *self._trades):
            self.health_reason = "market_evidence_generation_mismatch"
            return None
        if (
            now_ms - quote.received_timestamp_ms > 1_000
            or now_ms - depth.received_timestamp_ms > 1_000
            or now_ms - self._trades[-1].received_timestamp_ms > 2_000
        ):
            self.final_requirement_reason = "market_fact_stale"
            return None
        bid, ask = float(quote.bid), float(quote.ask)
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid * 10_000
        if spread > self.policy.max_spread_bps:
            self.final_requirement_reason = "spread"
            return None
        depth_value = sum(
            price * size
            for price, size in depth.bids + depth.asks
            if abs(price / mid - 1.0) <= 0.001
        )
        if depth_value < self.policy.min_depth_10bps_usd:
            self.final_requirement_reason = "depth"
            return None
        buy = sum(float(item.price) * float(item.size) for item in self._trades if item.side == "buy")
        total = sum(float(item.price) * float(item.size) for item in self._trades)
        if total <= 0:
            self.final_requirement_reason = "order_flow"
            return None
        ratio = buy / total
        if (self.direction == "long" and ratio < 0.60) or (
            self.direction == "short" and ratio > 0.40
        ):
            self.final_requirement_reason = "order_flow"
            return None
        entry = ask if self.direction == "long" else bid
        if not self.entry_zone[0] <= entry <= self.entry_zone[1]:
            self.final_requirement_reason = "entry_zone"
            return None
        self.final_requirement_reason = None
        observed = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat()
        from aletheia.universe.market_structure import evaluate_market_structure

        structure = evaluate_market_structure(
            list(self._closed_candles_1m), direction=self.direction
        )
        return TriggerReady(
            observed_at=observed,
            bid=bid,
            ask=ask,
            spread_bps=spread,
            depth_10bps_usd=depth_value,
            taker_buy_ratio=ratio,
            quote_exchange_timestamp_ms=quote.exchange_timestamp_ms,
            quote_received_timestamp_ms=quote.received_timestamp_ms,
            trade_exchange_timestamp_ms=self._trades[-1].exchange_timestamp_ms,
            trade_received_timestamp_ms=self._trades[-1].received_timestamp_ms,
            depth_exchange_timestamp_ms=depth.exchange_timestamp_ms,
            depth_received_timestamp_ms=depth.received_timestamp_ms,
            quote_sequence=quote.sequence,
            trade_sequence=self._trades[-1].sequence,
            depth_sequence=depth.sequence,
            stream_generation_id=quote.stream_generation_id,
            subscription_generation_id=quote.subscription_generation_id,
            depth_bids=depth.bids,
            depth_asks=depth.asks,
            entry_price=entry,
            entry_trigger={
                "trigger_spec_version": TRIGGER_SPEC_VERSION,
                "breakout_level": self.breakout_level,
                "pullback_seen_at_ms": self._pullback_seen_ms,
                "first_reclaim_quote_at_ms": self._first_reclaim_quote_ms,
                "first_reclaim_quote_sequence": self._first_reclaim_quote_sequence,
                "confirming_reclaim_quote_at_ms": quote.received_timestamp_ms,
                "confirming_reclaim_quote_sequence": quote.sequence,
                "minimum_confirmation_spacing_ms": 300,
            },
            market_structure=structure,
        )


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
