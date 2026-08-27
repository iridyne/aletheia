"""Venue-neutral fast market stream plus the first Bitget public adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time
from typing import Literal, Protocol

import aiohttp
from pydantic import BaseModel, ConfigDict

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, LIVE_EXECUTION_MODES
from aletheia.data.symbols import InstrumentId


class FastMarketEvent(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    event_type: Literal["quote", "trade", "depth", "candle"]
    instrument: InstrumentId
    exchange_timestamp_ms: int
    received_timestamp_ms: int
    sequence: int
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


class FastMarketStream(Protocol):
    async def connect(self) -> None: ...
    async def set_instruments(self, instruments: set[InstrumentId]) -> None: ...
    def events(self) -> AsyncIterator[FastMarketEvent]: ...
    def health(self) -> FastStreamHealth: ...
    async def close(self) -> None: ...


_BITGET_PUBLIC_WS = "wss://ws.bitget.com/v2/ws/public"
_CHANNELS = ("ticker", "trade", "books5", "candle1m")


def _bitget_arg(instrument: InstrumentId, channel: str) -> dict[str, str]:
    return {
        "instType": "SPOT" if instrument.market_type == "spot" else "USDT-FUTURES",
        "channel": channel,
        "instId": instrument.to_bitget_symbol(),
    }


def _instrument_from_arg(arg: dict, known: set[InstrumentId]) -> InstrumentId | None:
    inst_id = str(arg.get("instId") or "").upper()
    inst_type = str(arg.get("instType") or "").upper()
    if inst_type == "SPOT":
        market_type = "spot"
    elif inst_type == "USDT-FUTURES":
        market_type = "perp"
    else:
        return None
    return next(
        (
            item for item in known
            if item.market_type == market_type
            and item.symbol.replace("/", "").upper() == inst_id
        ),
        None,
    )


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_bitget_message(
    message: dict,
    instruments: set[InstrumentId],
    *,
    received_timestamp_ms: int | None = None,
) -> list[FastMarketEvent]:
    """Parse one Bitget data message; control/invalid messages yield no events."""

    arg = message.get("arg")
    data = message.get("data")
    if not isinstance(arg, dict) or not isinstance(data, list):
        return []
    instrument = _instrument_from_arg(arg, instruments)
    if instrument is None:
        return []
    channel = str(arg.get("channel") or "")
    received = received_timestamp_ms or int(time.time() * 1000)
    events: list[FastMarketEvent] = []
    rows = data
    if channel == "trade":
        try:
            rows = sorted(data, key=lambda item: int(item.get("ts") or 0))
        except (AttributeError, TypeError, ValueError):
            return []
    for index, row in enumerate(rows):
        if not isinstance(row, (dict, list, tuple)):
            continue
        message_ts = int(message.get("ts") or received)
        if isinstance(row, dict):
            ts = int(row.get("ts") or message_ts)
            sequence = int(
                row.get("seq") or row.get("seqId") or message_ts * 1000 + index
            )
        else:
            ts = int(row[0]) if row else received
            # Candle rows repeat their bar-open timestamp throughout the minute;
            # use the envelope update timestamp as the event sequence identity.
            sequence = message_ts * 1000 + index
        common = {
            "instrument": instrument,
            "exchange_timestamp_ms": ts,
            "received_timestamp_ms": received,
            "sequence": sequence,
        }
        if channel == "ticker" and isinstance(row, dict):
            bid = _positive(row.get("bidPr") or row.get("bid1Price"))
            ask = _positive(row.get("askPr") or row.get("ask1Price"))
            if bid is not None and ask is not None and ask >= bid:
                events.append(FastMarketEvent(event_type="quote", bid=bid, ask=ask, **common))
        elif channel == "trade" and isinstance(row, dict):
            price = _positive(row.get("price"))
            size = _positive(row.get("size"))
            side = str(row.get("side") or "").lower()
            if price is not None and size is not None and side in {"buy", "sell"}:
                events.append(
                    FastMarketEvent(
                        event_type="trade", price=price, size=size, side=side, **common
                    )
                )
        elif channel == "books5" and isinstance(row, dict):
            try:
                bids = tuple((float(level[0]), float(level[1])) for level in row.get("bids", ()))
                asks = tuple((float(level[0]), float(level[1])) for level in row.get("asks", ()))
            except (TypeError, ValueError, IndexError):
                continue
            if bids and asks and all(price > 0 and size >= 0 for price, size in bids + asks):
                events.append(FastMarketEvent(event_type="depth", bids=bids, asks=asks, **common))
        elif channel == "candle1m" and isinstance(row, (list, tuple)) and len(row) >= 6:
            try:
                candle = tuple(float(value) for value in row[1:6])
            except (TypeError, ValueError):
                continue
            if all(value > 0 for value in candle[:4]) and candle[4] >= 0:
                events.append(FastMarketEvent(event_type="candle", candle=candle, **common))
    return events


class BitgetFastMarketStream:
    """Credential-free Bitget public WebSocket stream."""

    def __init__(self, endpoint: str = _BITGET_PUBLIC_WS):
        self.endpoint = endpoint
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._instruments: set[InstrumentId] = set()
        self._confirmed: set[tuple[str, str, str]] = set()
        self._health = FastStreamHealth()
        self._ping_task: asyncio.Task | None = None
        self._pong_event: asyncio.Event | None = None

    async def connect(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(self.endpoint, heartbeat=None)
        except Exception:
            await self._session.close()
            self._session = None
            self._health = FastStreamHealth(reason="fast_trigger_stream_connect_failed:bitget")
            raise
        self._confirmed.clear()
        self._health = FastStreamHealth(connected=True)
        self._pong_event = asyncio.Event()
        self._ping_task = asyncio.create_task(self._ping_loop(), name="bitget-fast-stream-ping")

    async def _ping_loop(self) -> None:
        try:
            while self._ws is not None and not self._ws.closed:
                await asyncio.sleep(30)
                if self._pong_event is None:
                    return
                self._pong_event.clear()
                await self._ws.send_str("ping")
                try:
                    await asyncio.wait_for(self._pong_event.wait(), timeout=10)
                except TimeoutError:
                    self._health = self._health.model_copy(
                        update={
                            "connected": False,
                            "subscriptions_confirmed": False,
                            "reason": "fast_trigger_stream_pong_timeout:bitget",
                        }
                    )
                    await self._ws.close()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._health = self._health.model_copy(
                update={"connected": False, "subscriptions_confirmed": False,
                        "reason": "fast_trigger_stream_disconnected:bitget"}
            )

    async def set_instruments(self, instruments: set[InstrumentId]) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("fast_trigger_stream_not_connected:bitget")
        if any(item.exchange != "bitget" for item in instruments):
            raise ValueError("fast_trigger_stream_venue_mismatch:bitget")
        previous = self._instruments
        if previous:
            await self._ws.send_json({
                "op": "unsubscribe",
                "args": [_bitget_arg(item, channel) for item in previous for channel in _CHANNELS],
            })
        self._instruments = set(instruments)
        self._confirmed.clear()
        if instruments:
            await self._ws.send_json({
                "op": "subscribe",
                "args": [_bitget_arg(item, channel) for item in instruments for channel in _CHANNELS],
            })
        self._health = self._health.model_copy(update={"subscriptions_confirmed": False})

    def _record_ack(self, payload: dict) -> None:
        if payload.get("event") != "subscribe" or not isinstance(payload.get("arg"), dict):
            return
        arg = payload["arg"]
        self._confirmed.add((
            str(arg.get("instType") or ""),
            str(arg.get("channel") or ""),
            str(arg.get("instId") or ""),
        ))
        expected = {
            (value["instType"], value["channel"], value["instId"])
            for item in self._instruments
            for value in (_bitget_arg(item, channel) for channel in _CHANNELS)
        }
        self._health = self._health.model_copy(
            update={"subscriptions_confirmed": bool(expected) and expected <= self._confirmed}
        )

    async def events(self) -> AsyncIterator[FastMarketEvent]:
        if self._ws is None:
            raise RuntimeError("fast_trigger_stream_not_connected:bitget")
        async for message in self._ws:
            received = int(time.time() * 1000)
            if message.type == aiohttp.WSMsgType.TEXT:
                if message.data == "pong":
                    if self._pong_event is not None:
                        self._pong_event.set()
                    continue
                try:
                    payload = json.loads(message.data)
                except (TypeError, json.JSONDecodeError):
                    continue
                self._record_ack(payload)
                self._health = self._health.model_copy(update={"last_received_at_ms": received})
                for event in parse_bitget_message(
                    payload, self._instruments, received_timestamp_ms=received
                ):
                    yield event
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                break
        self._health = self._health.model_copy(
            update={"connected": False, "subscriptions_confirmed": False,
                    "reason": "fast_trigger_stream_disconnected:bitget"}
        )

    def health(self) -> FastStreamHealth:
        return self._health.model_copy()

    async def close(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            await asyncio.gather(self._ping_task, return_exceptions=True)
            self._ping_task = None
        self._pong_event = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._health = FastStreamHealth(reason="fast_trigger_stream_closed:bitget")


_FAST_STREAM_ADAPTERS = {"bitget_demo": BitgetFastMarketStream}


def execution_venue_for_mode(execution_mode: str) -> str:
    """Resolve venue identity from the existing execution-mode authority map."""

    mode = str(execution_mode or "")
    for venue, (_, paper_mode) in EXCHANGE_UNIFICATION_MAP.items():
        if mode == paper_mode or mode == LIVE_EXECUTION_MODES.get(venue):
            return venue
    return mode.split("_", 1)[0] or "unknown"


def fast_market_stream_available(execution_mode: str) -> bool:
    return str(execution_mode or "") in _FAST_STREAM_ADAPTERS


def fast_market_data_venue_for_config(cfg) -> str:
    """Resolve the venue supplying trigger data, including local-paper mode."""

    if cfg.exchange.paper_execution_mode == "paper_internal":
        return "bitget"
    return execution_venue_for_mode(cfg.exchange.paper_execution_mode)


def fast_market_stream_available_for_config(cfg) -> bool:
    venue = fast_market_data_venue_for_config(cfg)
    return any(
        execution_venue_for_mode(mode) == venue
        for mode in _FAST_STREAM_ADAPTERS
    )


def fast_market_stream_for_config(cfg) -> FastMarketStream:
    """Build the configured exact-venue stream for paper or exchange execution."""

    venue = fast_market_data_venue_for_config(cfg)
    adapter_mode = next(
        (
            mode for mode in _FAST_STREAM_ADAPTERS
            if execution_venue_for_mode(mode) == venue
        ),
        None,
    )
    if adapter_mode is None:
        raise RuntimeError(f"fast_trigger_stream_unavailable:{venue}")
    return fast_market_stream_for_execution_mode(adapter_mode)


def fast_market_stream_for_execution_mode(execution_mode: str) -> FastMarketStream:
    adapter = _FAST_STREAM_ADAPTERS.get(str(execution_mode or ""))
    if adapter is not None:
        return adapter()
    venue = execution_venue_for_mode(execution_mode)
    raise RuntimeError(f"fast_trigger_stream_unavailable:{venue}")
