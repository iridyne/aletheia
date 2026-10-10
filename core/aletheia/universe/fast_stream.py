"""Venue-neutral fast market stream — protocol DTOs, monitoring projection, and factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
import logging
import time
from typing import Literal, Protocol

logger = logging.getLogger("aletheia.universe.fast_stream")

from pydantic import BaseModel, ConfigDict

from aletheia.config.exchanges import execution_mode_venue
from aletheia.data.symbols import InstrumentId


class FastMarketEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    event_type: Literal["quote", "trade", "depth", "candle"]
    instrument: InstrumentId
    exchange_timestamp_ms: int
    received_timestamp_ms: int
    sequence: int
    stream_generation_id: str | None = None
    subscription_generation_id: str | None = None
    bid: float | None = None
    ask: float | None = None
    price: float | None = None
    size: float | None = None
    side: Literal["buy", "sell"] | None = None
    bids: tuple[tuple[float, float], ...] = ()
    asks: tuple[tuple[float, float], ...] = ()
    candle: tuple[float, float, float, float, float] | None = None


class FastStreamHealth(BaseModel):
    connected: bool = False
    subscriptions_confirmed: bool = False
    reason: str | None = None
    last_received_at_ms: int | None = None


def events_share_generation(*events: FastMarketEvent) -> bool:
    return len(
        {
            (event.stream_generation_id, event.subscription_generation_id)
            for event in events
        }
    ) == 1


class FastMarketStream(Protocol):
    async def connect(self, instruments: set[InstrumentId] | None = None) -> None: ...
    async def set_instruments(self, instruments: set[InstrumentId]) -> None: ...
    def events(self) -> AsyncIterator[FastMarketEvent]: ...
    def health(self) -> FastStreamHealth: ...
    def monitoring_state(
        self,
        instrument: InstrumentId,
        required_channels: tuple[str, ...],
    ) -> dict: ...
    async def close(self) -> None: ...


_CHANNEL_FRESHNESS_MS = {
    "quote": 2_000,
    "trade": 3_000,
    "depth": 2_000,
    "candle": 120_000,
}


def _monitoring_projection(
    *,
    instrument: InstrumentId,
    required_channels: tuple[str, ...],
    connected: bool,
    stream_generation_id: str | None,
    subscription_generation_id: str | None,
    channel_facts: dict[tuple[InstrumentId, str], FastMarketEvent],
    reason: str | None,
) -> dict:
    now_ms = int(time.time() * 1_000)
    channels: dict[str, dict] = {}
    for channel in required_channels:
        fact = channel_facts.get((instrument, channel))
        if fact is None:
            channels[channel] = {
                "state": "unavailable" if not connected else "warming",
                "last_exchange_at": None,
                "last_received_at": None,
                "last_sequence": None,
                "reason": reason if not connected else "channel_event_not_observed",
            }
            continue
        age_ms = now_ms - fact.received_timestamp_ms
        fresh = 0 <= age_ms <= _CHANNEL_FRESHNESS_MS[channel]
        channels[channel] = {
            "state": "ready" if fresh else "degraded",
            "last_exchange_at": fact.exchange_timestamp_ms,
            "last_received_at": fact.received_timestamp_ms,
            "last_sequence": fact.sequence,
            "reason": None if fresh else "channel_event_stale",
        }
    states = {value["state"] for value in channels.values()}
    state = (
        "unavailable"
        if not connected
        else "ready"
        if states == {"ready"}
        else "degraded"
        if "degraded" in states
        else "warming"
    )
    return {
        "version": "entry-plan-monitoring/v1",
        "state": state,
        "instrument": {
            "exchange": instrument.exchange,
            "symbol": instrument.symbol,
            "market_type": instrument.market_type,
        },
        "stream_generation_id": stream_generation_id,
        "subscription_generation_id": subscription_generation_id,
        "required_channels": list(required_channels),
        "channels": channels,
        "updated_at": datetime.fromtimestamp(now_ms / 1_000, tz=timezone.utc).isoformat(),
    }


def _ccxt_pro_stream(exchange_name: str):
    def make():
        from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream
        return CcxtProFastMarketStream(exchange_name)
    return make


# Execution mode -> CCXT Pro exchange id of its public market stream. OKX's
# public `watch_ticker` carries bid/ask like Bitget's (probed 2026-10-10).
_FAST_STREAM_ADAPTERS = {
    "bitget_demo": _ccxt_pro_stream("bitget"),
    "binance_testnet": _ccxt_pro_stream("binanceusdm"),
    "okx_demo": _ccxt_pro_stream("okx"),
}



def fast_market_stream_available(execution_mode: str) -> bool:
    return str(execution_mode or "") in _FAST_STREAM_ADAPTERS


def fast_market_data_venue_for_config(cfg) -> str:
    """Resolve trigger data from the selected external execution account."""

    return execution_mode_venue(cfg.exchange.paper_execution_mode) or "unknown"


def fast_market_stream_available_for_config(cfg) -> bool:
    venue = fast_market_data_venue_for_config(cfg)
    return any(execution_mode_venue(mode) == venue for mode in _FAST_STREAM_ADAPTERS)


def fast_market_data_unavailability_reason(
    cfg,
    venue: str,
    market_type: str | None = None,
) -> str | None:
    """Return a stable token if the exact candidate instrument cannot be monitored.

    Used as a shared exact-instrument invariant across Universe allocation, Entry Planner
    admission, and Fast Trigger Watch validation. Returning a token (not None) means
    no Fast Monitoring engine exists for the candidate's physical venue.

    Stable tokens:
    - ``FAST_TRIGGER_DISABLED``: Fast Monitoring is explicitly disabled.
    - ``FAST_MARKET_DATA_VENUE_MISMATCH``: candidate venue differs from the
      configured fast-monitoring venue.
    - ``FAST_MARKET_DATA_STREAM_UNAVAILABLE``: no adapter covers the configured
      monitoring venue or exact execution market.
    """
    if not getattr(cfg.fast_trigger, "enabled", True):
        return "FAST_TRIGGER_DISABLED"
    configured_venue = fast_market_data_venue_for_config(cfg)
    if str(venue or "").lower() != configured_venue:
        return "FAST_MARKET_DATA_VENUE_MISMATCH"
    if not any(execution_mode_venue(mode) == configured_venue for mode in _FAST_STREAM_ADAPTERS):
        return "FAST_MARKET_DATA_STREAM_UNAVAILABLE"
    if (
        cfg.exchange.paper_execution_mode == "binance_testnet"
        and market_type is not None
        and str(market_type).lower() not in {"perp", "futures", "swap", "usdt_futures"}
    ):
        return "FAST_MARKET_DATA_STREAM_UNAVAILABLE"
    return None


def fast_market_stream_for_config(cfg) -> FastMarketStream:
    """Build the configured exact-venue stream for paper or exchange execution."""

    venue = fast_market_data_venue_for_config(cfg)
    adapter_mode = next(
        (mode for mode in _FAST_STREAM_ADAPTERS if execution_mode_venue(mode) == venue),
        None,
    )
    if adapter_mode is None:
        raise RuntimeError(f"fast_trigger_stream_unavailable:{venue}")
    return fast_market_stream_for_execution_mode(adapter_mode)


def fast_market_stream_for_execution_mode(execution_mode: str) -> FastMarketStream:
    adapter = _FAST_STREAM_ADAPTERS.get(str(execution_mode or ""))
    if adapter is not None:
        return adapter()
    venue = execution_mode_venue(execution_mode) or "unknown"
    raise RuntimeError(f"fast_trigger_stream_unavailable:{venue}")
