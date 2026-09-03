"""Venue-neutral fast market stream plus the first Bitget public adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
import json
import math
import os
import time
from typing import Literal, Protocol
from uuid import uuid4

import aiohttp
from pydantic import BaseModel, ConfigDict

from aletheia.config.exchanges import EXCHANGE_UNIFICATION_MAP, LIVE_EXECUTION_MODES
from aletheia.data.symbols import InstrumentId, to_binance
from aletheia.net import aiohttp_session, apply_proxy_env


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
        (item for item in known if item.market_type == market_type and item.symbol.replace("/", "").upper() == inst_id),
        None,
    )


def _positive(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, str) and (not value or not value.isascii() or not value.isdecimal()):
        return None
    number = int(value)
    return number if 0 <= number <= 2**63 - 1 else None


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
    received = received_timestamp_ms if received_timestamp_ms is not None else int(time.time() * 1000)
    raw_message_ts = message.get("ts")
    message_ts = received if raw_message_ts is None else _nonnegative_int(raw_message_ts)
    if message_ts is None:
        return []
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
        if isinstance(row, dict):
            raw_ts = row.get("ts")
            ts = message_ts if raw_ts is None else _nonnegative_int(raw_ts)
            raw_sequence = row.get("seq")
            if raw_sequence is None:
                raw_sequence = row.get("seqId")
            sequence = (
                message_ts * 1000 + index
                if raw_sequence is None
                else _nonnegative_int(raw_sequence)
            )
        else:
            ts = _nonnegative_int(row[0]) if row else received
            # Candle rows repeat their bar-open timestamp throughout the minute;
            # use the envelope update timestamp as the event sequence identity.
            sequence = message_ts * 1000 + index
        if ts is None or sequence is None:
            continue
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
                events.append(FastMarketEvent(event_type="trade", price=price, size=size, side=side, **common))
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
        self._stream_generation_id: str | None = None
        self._subscription_generations: dict[InstrumentId, str] = {}
        self._channel_facts: dict[tuple[InstrumentId, str], FastMarketEvent] = {}

    async def connect(self, instruments: set[InstrumentId] | None = None) -> None:
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
        self._stream_generation_id = uuid4().hex
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
                update={
                    "connected": False,
                    "subscriptions_confirmed": False,
                    "reason": "fast_trigger_stream_disconnected:bitget",
                }
            )

    async def set_instruments(self, instruments: set[InstrumentId]) -> None:
        if self._ws is None or self._ws.closed:
            raise RuntimeError("fast_trigger_stream_not_connected:bitget")
        if any(item.exchange != "bitget" for item in instruments):
            raise ValueError("fast_trigger_stream_venue_mismatch:bitget")
        previous = self._instruments
        removed = previous - instruments
        added = instruments - previous
        if removed:
            await self._ws.send_json(
                {
                    "op": "unsubscribe",
                    "args": [_bitget_arg(item, channel) for item in removed for channel in _CHANNELS],
                }
            )
        self._instruments = set(instruments)
        for instrument in removed:
            self._subscription_generations.pop(instrument, None)
            for channel in _CHANNEL_FRESHNESS_MS:
                self._channel_facts.pop((instrument, channel), None)
        for instrument in added:
            self._subscription_generations[instrument] = uuid4().hex
        if added:
            await self._ws.send_json(
                {
                    "op": "subscribe",
                    "args": [_bitget_arg(item, channel) for item in added for channel in _CHANNELS],
                }
            )
        self._health = self._health.model_copy(update={"subscriptions_confirmed": False})

    def _record_ack(self, payload: dict) -> None:
        if payload.get("event") != "subscribe" or not isinstance(payload.get("arg"), dict):
            return
        arg = payload["arg"]
        self._confirmed.add(
            (
                str(arg.get("instType") or ""),
                str(arg.get("channel") or ""),
                str(arg.get("instId") or ""),
            )
        )
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
                for event in parse_bitget_message(payload, self._instruments, received_timestamp_ms=received):
                    event = event.model_copy(
                        update={
                            "stream_generation_id": self._stream_generation_id,
                            "subscription_generation_id": self._subscription_generations.get(
                                event.instrument
                            ),
                        }
                    )
                    self._channel_facts[(event.instrument, event.event_type)] = event
                    yield event
            elif message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            }:
                break
        self._health = self._health.model_copy(
            update={
                "connected": False,
                "subscriptions_confirmed": False,
                "reason": "fast_trigger_stream_disconnected:bitget",
            }
        )

    def health(self) -> FastStreamHealth:
        return self._health.model_copy()

    def monitoring_state(
        self,
        instrument: InstrumentId,
        required_channels: tuple[str, ...],
    ) -> dict:
        return _monitoring_projection(
            instrument=instrument,
            required_channels=required_channels,
            connected=bool(self._ws is not None and not self._ws.closed),
            stream_generation_id=self._stream_generation_id,
            subscription_generation_id=self._subscription_generations.get(instrument),
            channel_facts=self._channel_facts,
            reason=self._health.reason,
        )

    async def close(self) -> None:
        if self._ping_task is not None:
            self._ping_task.cancel()
            await asyncio.gather(self._ping_task, return_exceptions=True)
            self._ping_task = None
        self._pong_event = None
        self._stream_generation_id = None
        self._subscription_generations.clear()
        self._channel_facts.clear()
        if self._ws is not None:
            await self._ws.close()
            self._ws = None
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._health = FastStreamHealth(reason="fast_trigger_stream_closed:bitget")


_BINANCE_SPOT_WS = "wss://stream.binance.com:9443/ws"
_BINANCE_USDM_WS = "wss://fstream.binance.com/ws"
_BINANCE_SPOT_DEPTH = "https://api.binance.com/api/v3/depth"


def _binance_instrument(symbol: object, market_type: str, known: set[InstrumentId]) -> InstrumentId | None:
    token = str(symbol or "").upper()
    return next(
        (
            item
            for item in known
            if item.exchange == "binance"
            and item.market_type == market_type
            and to_binance(item.symbol) == token
        ),
        None,
    )


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def parse_binance_message(
    message: dict,
    instruments: set[InstrumentId],
    *,
    market_type: Literal["spot", "perp"],
    received_timestamp_ms: int | None = None,
    sequence: int = 1,
) -> FastMarketEvent | None:
    """Parse one Binance public event other than Spot diff-depth."""

    event_name = str(message.get("e") or "")
    instrument = _binance_instrument(message.get("s"), market_type, instruments)
    if instrument is None:
        return None
    received = received_timestamp_ms or int(time.time() * 1000)
    try:
        exchange_ts = int(message["E"])
    except (KeyError, TypeError, ValueError):
        return None
    if exchange_ts <= 0:
        return None
    common = {
        "instrument": instrument,
        "exchange_timestamp_ms": exchange_ts,
        "received_timestamp_ms": received,
        "sequence": sequence,
    }
    if event_name in {"24hrTicker", "bookTicker"}:
        bid = _finite(message.get("b"), positive=True)
        ask = _finite(message.get("a"), positive=True)
        if bid is not None and ask is not None and ask >= bid:
            return FastMarketEvent(event_type="quote", bid=bid, ask=ask, **common)
    elif event_name == "aggTrade":
        price = _finite(message.get("p"), positive=True)
        size = _finite(message.get("q"), positive=True)
        if price is not None and size is not None and isinstance(message.get("m"), bool):
            side = "sell" if message["m"] else "buy"
            return FastMarketEvent(event_type="trade", price=price, size=size, side=side, **common)
    elif event_name == "kline" and isinstance(message.get("k"), dict):
        row = message["k"]
        bar_open = _nonnegative_int(row.get("t"))
        if bar_open is None or bar_open <= 0:
            return None
        values = tuple(_finite(row.get(key), nonnegative=(key == "v"), positive=(key != "v")) for key in "ohlcv")
        if all(value is not None for value in values):
            common["exchange_timestamp_ms"] = bar_open
            return FastMarketEvent(event_type="candle", candle=values, **common)
    elif event_name == "depthUpdate" and market_type == "perp":
        levels = _binance_levels(message)
        if levels is not None:
            bids = tuple(sorted(levels[0], reverse=True))
            asks = tuple(sorted(levels[1]))
            if bids and asks and asks[0][0] >= bids[0][0]:
                return FastMarketEvent(event_type="depth", bids=bids[:5], asks=asks[:5], **common)
    return None


def _binance_levels(payload: dict) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]] | None:
    parsed: list[tuple[tuple[float, float], ...]] = []
    for key in ("b", "a"):
        levels = []
        raw = payload.get(key)
        if not isinstance(raw, list):
            return None
        for level in raw:
            if not isinstance(level, (list, tuple)) or len(level) < 2:
                return None
            price = _finite(level[0], positive=True)
            size = _finite(level[1], nonnegative=True)
            if price is None or size is None:
                return None
            levels.append((price, size))
        parsed.append(tuple(levels))
    return parsed[0], parsed[1]


class _SpotBook:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: int | None = None
        self.buffer: list[dict] = []
        self.synced = False

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.last_update_id = None
        self.buffer.clear()
        self.synced = False

    def install_snapshot(self, payload: dict) -> bool:
        try:
            update_id = int(payload["lastUpdateId"])
            levels = _binance_levels({"b": payload["bids"], "a": payload["asks"]})
        except (KeyError, TypeError, ValueError):
            return False
        if levels is None:
            return False
        self.bids = {price: size for price, size in levels[0] if size > 0}
        self.asks = {price: size for price, size in levels[1] if size > 0}
        if not self.bids or not self.asks or min(self.asks) < max(self.bids):
            self.reset()
            return False
        self.last_update_id = update_id
        bridged = self._bridge()
        if not bridged and self.buffer:
            try:
                if min(int(row.get("U", -1)) for row in self.buffer) > update_id + 1:
                    buffered = list(self.buffer)
                    self.reset()
                    self.buffer = buffered
            except (TypeError, ValueError):
                self.reset()
        return bridged

    def add(self, payload: dict) -> bool:
        if self.synced:
            try:
                expected = (self.last_update_id or 0) + 1
                if int(payload.get("U", -1)) != expected:
                    self.reset()
                    return False
            except (TypeError, ValueError):
                self.reset()
                return False
        self.buffer.append(payload)
        return self._bridge() if self.last_update_id is not None else False

    def _bridge(self) -> bool:
        assert self.last_update_id is not None
        try:
            pending = [row for row in self.buffer if int(row.get("u", -1)) > self.last_update_id]
        except (TypeError, ValueError):
            self.reset()
            return False
        if not pending:
            self.buffer.clear()
            return False
        expected = self.last_update_id + 1
        try:
            first = next(
                (
                    index
                    for index, row in enumerate(pending)
                    if int(row.get("U", -1)) <= expected <= int(row.get("u", -1))
                ),
                None,
            )
        except (TypeError, ValueError):
            self.reset()
            return False
        if first is None:
            self.buffer = pending
            return False
        for index, row in enumerate(pending[first:]):
            try:
                lower, upper = int(row.get("U", -1)), int(row.get("u", -1))
            except (TypeError, ValueError):
                self.reset()
                return False
            continuous = lower <= expected <= upper if index == 0 else lower == expected
            if not continuous:
                self.reset()
                return False
            levels = _binance_levels(row)
            if levels is None:
                self.reset()
                return False
            self._apply(self.bids, levels[0])
            self._apply(self.asks, levels[1])
            self.last_update_id = upper
            expected = upper + 1
        self.buffer.clear()
        self.synced = True
        return True

    @staticmethod
    def _apply(book: dict[float, float], levels: tuple[tuple[float, float], ...]) -> None:
        for price, size in levels:
            if size == 0:
                book.pop(price, None)
            else:
                book[price] = size

    def top5(self) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]] | None:
        bids = tuple(sorted(self.bids.items(), reverse=True)[:5])
        asks = tuple(sorted(self.asks.items())[:5])
        if not bids or not asks or asks[0][0] < bids[0][0]:
            return None
        return bids, asks


class BinanceFastMarketStream:
    """Credential-free Binance Spot and USD-M public market stream."""

    def __init__(
        self,
        spot_endpoint: str = _BINANCE_SPOT_WS,
        perp_endpoint: str = _BINANCE_USDM_WS,
        spot_depth_endpoint: str = _BINANCE_SPOT_DEPTH,
    ):
        self._endpoints = {"spot": spot_endpoint, "perp": perp_endpoint}
        self._spot_depth_endpoint = spot_depth_endpoint
        self._session: aiohttp.ClientSession | None = None
        self._sockets: dict[str, aiohttp.ClientWebSocketResponse] = {}
        self._reader_tasks: list[asyncio.Task] = []
        self._snapshot_tasks: set[asyncio.Task] = set()
        self._instruments: set[InstrumentId] = set()
        self._books: dict[InstrumentId, _SpotBook] = {}
        self._queue: asyncio.Queue[FastMarketEvent | None] = asyncio.Queue()
        self._health = FastStreamHealth()
        self._request_id = 0
        self._pending_acks: dict[int, str] = {}
        self._sequences: dict[tuple[InstrumentId, str], int] = {}
        self._stream_generations: dict[str, str] = {}
        self._subscription_generations: dict[InstrumentId, str] = {}
        self._channel_facts: dict[tuple[InstrumentId, str], FastMarketEvent] = {}
        self._lane_connected = {"spot": False, "perp": False}
        self._lane_reasons: dict[str, str | None] = {"spot": None, "perp": None}

    async def connect(self, instruments: set[InstrumentId] | None = None) -> None:
        if self._sockets:
            return
        if instruments is not None and any(
            item.exchange != "binance" or item.market_type not in self._endpoints
            for item in instruments
        ):
            raise ValueError("fast_trigger_stream_venue_mismatch:binance")
        required_market_types = (
            {item.market_type for item in instruments}
            if instruments
            else set(self._endpoints)
        )
        self._session = aiohttp_session(timeout=aiohttp.ClientTimeout(total=15, connect=10))
        apply_proxy_env()
        configured_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        websocket_proxy = configured_proxy if str(configured_proxy or "").lower().startswith(("http://", "https://")) else None
        last_error: Exception | None = None
        for market_type, endpoint in self._endpoints.items():
            if market_type not in required_market_types:
                continue
            try:
                ws = await self._session.ws_connect(endpoint, heartbeat=30, proxy=websocket_proxy)
                self._sockets[market_type] = ws
                self._reader_tasks.append(asyncio.create_task(self._read_socket(market_type, ws)))
                self._lane_connected[market_type] = True
                self._lane_reasons[market_type] = None
                self._stream_generations[market_type] = uuid4().hex
            except Exception as exc:
                last_error = exc
                self._lane_connected[market_type] = False
                self._lane_reasons[market_type] = (
                    f"fast_trigger_stream_connect_failed:binance:{market_type}"
                )
        if not any(self._lane_connected.values()):
            await self.close()
            self._health = FastStreamHealth(reason="fast_trigger_stream_connect_failed:binance")
            if last_error is not None:
                raise last_error
            raise RuntimeError("fast_trigger_stream_connect_failed:binance")
        self._health = FastStreamHealth(
            connected=True,
            reason=(
                "fast_trigger_stream_partially_available:binance"
                if not all(
                    self._lane_connected[item] for item in required_market_types
                )
                else None
            ),
        )

    @staticmethod
    def _streams(instruments: set[InstrumentId], market_type: str) -> list[str]:
        streams = []
        for item in instruments:
            if item.market_type != market_type:
                continue
            symbol = to_binance(item.symbol).lower()
            if market_type == "spot":
                streams.extend((f"{symbol}@aggTrade", f"{symbol}@ticker", f"{symbol}@kline_1m", f"{symbol}@depth@100ms"))
            else:
                streams.extend((f"{symbol}@aggTrade", f"{symbol}@bookTicker", f"{symbol}@kline_1m", f"{symbol}@depth5@100ms"))
        return streams

    async def _request(self, market_type: str, method: str, params: list[str]) -> None:
        if not params:
            return
        if not self._lane_connected.get(market_type) or market_type not in self._sockets:
            return
        self._request_id += 1
        request_id = self._request_id
        self._pending_acks[request_id] = market_type
        await self._sockets[market_type].send_json({"method": method, "params": params, "id": request_id})

    async def set_instruments(self, instruments: set[InstrumentId]) -> None:
        if not self._sockets:
            raise RuntimeError("fast_trigger_stream_not_connected:binance")
        if any(item.exchange != "binance" or item.market_type not in {"spot", "perp"} for item in instruments):
            raise ValueError("fast_trigger_stream_venue_mismatch:binance")
        previous = self._instruments
        removed = previous - instruments
        added = instruments - previous
        for market_type in ("spot", "perp"):
            await self._request(market_type, "UNSUBSCRIBE", self._streams(removed, market_type))
        self._instruments = set(instruments)
        for instrument in removed:
            self._subscription_generations.pop(instrument, None)
            self._books.pop(instrument, None)
            for channel in _CHANNEL_FRESHNESS_MS:
                self._channel_facts.pop((instrument, channel), None)
        for instrument in added:
            self._subscription_generations[instrument] = uuid4().hex
            if instrument.market_type == "spot":
                self._books[instrument] = _SpotBook()
        for market_type in ("spot", "perp"):
            await self._request(market_type, "SUBSCRIBE", self._streams(added, market_type))
        for instrument in added:
            if instrument.market_type != "spot" or not self._lane_connected["spot"]:
                continue
            task = asyncio.create_task(self._load_spot_snapshot(instrument))
            self._snapshot_tasks.add(task)
            task.add_done_callback(self._snapshot_tasks.discard)
        self._refresh_confirmation()

    async def _load_spot_snapshot(self, instrument: InstrumentId) -> None:
        if self._session is None:
            return
        for _attempt in range(3):
            try:
                async with self._session.get(
                    self._spot_depth_endpoint,
                    params={"symbol": to_binance(instrument.symbol), "limit": 1000},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
            except Exception:
                continue
            book = self._books.get(instrument)
            if book is None:
                return
            book.install_snapshot(payload)
            if book.last_update_id is not None:
                self._refresh_confirmation()
                return
        self._lane_connected["spot"] = False
        self._lane_reasons["spot"] = "fast_trigger_spot_depth_snapshot_failed:binance"
        spot_ws = self._sockets.get("spot")
        if spot_ws is not None:
            await spot_ws.close()
        self._refresh_confirmation()

    def _next_sequence(self, instrument: InstrumentId, event_type: str) -> int:
        key = (instrument, event_type)
        value = self._sequences.get(key, 0) + 1
        self._sequences[key] = value
        return value

    def _refresh_confirmation(self) -> None:
        lane_confirmed = []
        for market_type in ("spot", "perp"):
            lane_instruments = {
                item for item in self._instruments if item.market_type == market_type
            }
            if not lane_instruments or not self._lane_connected[market_type]:
                continue
            pending = market_type in self._pending_acks.values()
            spot_synced = market_type != "spot" or all(
                self._books[item].synced for item in lane_instruments
            )
            lane_confirmed.append(not pending and spot_synced)
        confirmed = any(lane_confirmed)
        self._health = self._health.model_copy(
            update={
                "connected": any(self._lane_connected.values()),
                "subscriptions_confirmed": confirmed,
                "reason": None if confirmed and all(lane_confirmed) else self._health.reason,
            }
        )

    async def _read_socket(self, market_type: str, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            async for message in ws:
                received = int(time.time() * 1000)
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR}:
                        break
                    continue
                try:
                    payload = json.loads(message.data)
                except (TypeError, json.JSONDecodeError):
                    continue
                request_id = payload.get("id")
                if type(request_id) is int and request_id in self._pending_acks:
                    ack_lane = self._pending_acks[request_id]
                    if "error" in payload:
                        self._pending_acks.pop(request_id, None)
                        self._lane_reasons[ack_lane] = (
                            f"fast_trigger_subscription_rejected:binance:{ack_lane}"
                        )
                        self._health = self._health.model_copy(
                            update={
                                "subscriptions_confirmed": False,
                                "reason": "fast_trigger_subscription_rejected:binance",
                            }
                        )
                    elif "result" in payload and payload["result"] is None:
                        self._pending_acks.pop(request_id, None)
                        self._refresh_confirmation()
                    continue
                instrument = _binance_instrument(payload.get("s"), market_type, self._instruments)
                if market_type == "spot" and payload.get("e") == "depthUpdate" and instrument is not None:
                    await self._handle_spot_depth(instrument, payload, received)
                    continue
                if instrument is None:
                    continue
                event_name = str(payload.get("e") or "")
                event_type = {"24hrTicker": "quote", "bookTicker": "quote", "aggTrade": "trade", "kline": "candle", "depthUpdate": "depth"}.get(event_name)
                if event_type is None:
                    continue
                event = parse_binance_message(
                    payload,
                    self._instruments,
                    market_type=market_type,
                    received_timestamp_ms=received,
                    sequence=self._next_sequence(instrument, event_type),
                )
                if event is not None:
                    event = event.model_copy(
                        update={
                            "stream_generation_id": self._stream_generations.get(market_type),
                            "subscription_generation_id": self._subscription_generations.get(
                                event.instrument
                            ),
                        }
                    )
                    self._channel_facts[(event.instrument, event.event_type)] = event
                    self._health = self._health.model_copy(update={"last_received_at_ms": received})
                    await self._queue.put(event)
        finally:
            self._lane_connected[market_type] = False
            self._lane_reasons[market_type] = (
                f"fast_trigger_stream_disconnected:binance:{market_type}"
            )
            self._refresh_confirmation()
            if not any(self._lane_connected.values()):
                await self._queue.put(None)

    async def _handle_spot_depth(self, instrument: InstrumentId, payload: dict, received: int) -> None:
        book = self._books.get(instrument)
        if book is None:
            return
        was_synced = book.synced
        if was_synced:
            expected = (book.last_update_id or 0) + 1
            try:
                continuous = int(payload["U"]) == expected
            except (KeyError, TypeError, ValueError):
                continuous = False
            if not continuous:
                book.reset()
                self._refresh_confirmation()
                task = asyncio.create_task(self._load_spot_snapshot(instrument))
                self._snapshot_tasks.add(task)
                task.add_done_callback(self._snapshot_tasks.discard)
                return
        book.add(payload)
        if not book.synced:
            self._refresh_confirmation()
            return
        top = book.top5()
        try:
            exchange_ts = int(payload["E"])
        except (KeyError, TypeError, ValueError):
            return
        if top is not None:
            event = FastMarketEvent(
                event_type="depth",
                instrument=instrument,
                exchange_timestamp_ms=exchange_ts,
                received_timestamp_ms=received,
                sequence=self._next_sequence(instrument, "depth"),
                bids=top[0],
                asks=top[1],
                stream_generation_id=self._stream_generations.get("spot"),
                subscription_generation_id=self._subscription_generations.get(instrument),
            )
            self._channel_facts[(instrument, "depth")] = event
            self._health = self._health.model_copy(update={"last_received_at_ms": received})
            await self._queue.put(event)
        self._refresh_confirmation()

    async def events(self) -> AsyncIterator[FastMarketEvent]:
        if not self._sockets:
            raise RuntimeError("fast_trigger_stream_not_connected:binance")
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    def health(self) -> FastStreamHealth:
        return self._health.model_copy()

    def monitoring_state(
        self,
        instrument: InstrumentId,
        required_channels: tuple[str, ...],
    ) -> dict:
        lane = instrument.market_type
        return _monitoring_projection(
            instrument=instrument,
            required_channels=required_channels,
            connected=self._lane_connected.get(lane, False),
            stream_generation_id=self._stream_generations.get(lane),
            subscription_generation_id=self._subscription_generations.get(instrument),
            channel_facts=self._channel_facts,
            reason=self._lane_reasons.get(lane),
        )

    async def close(self) -> None:
        for task in self._snapshot_tasks:
            task.cancel()
        await asyncio.gather(*self._snapshot_tasks, return_exceptions=True)
        self._snapshot_tasks.clear()
        for task in self._reader_tasks:
            task.cancel()
        await asyncio.gather(*self._reader_tasks, return_exceptions=True)
        self._reader_tasks.clear()
        for ws in self._sockets.values():
            await ws.close()
        self._sockets.clear()
        if self._session is not None:
            await self._session.close()
            self._session = None
        self._health = FastStreamHealth(reason="fast_trigger_stream_closed:binance")
        self._stream_generations.clear()
        self._subscription_generations.clear()
        self._channel_facts.clear()
        self._pending_acks.clear()
        self._lane_connected = {"spot": False, "perp": False}
        self._lane_reasons = {"spot": None, "perp": None}


_FAST_STREAM_ADAPTERS = {
    "bitget_demo": BitgetFastMarketStream,
    "binance_testnet": BinanceFastMarketStream,
}


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
        return str(cfg.market_data.ccxt_exchange or "").strip().lower() or "unknown"
    return execution_venue_for_mode(cfg.exchange.paper_execution_mode)


def fast_market_stream_available_for_config(cfg) -> bool:
    venue = fast_market_data_venue_for_config(cfg)
    return any(execution_venue_for_mode(mode) == venue for mode in _FAST_STREAM_ADAPTERS)


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
    if not any(execution_venue_for_mode(mode) == configured_venue for mode in _FAST_STREAM_ADAPTERS):
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
        (mode for mode in _FAST_STREAM_ADAPTERS if execution_venue_for_mode(mode) == venue),
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
