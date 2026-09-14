"""Frozen Binance fast-market stream adapter; not registered by product startup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import math
import os
import time
from typing import Literal
from uuid import uuid4

import aiohttp

from aletheia.data.symbols import InstrumentId, to_binance
from aletheia.universe.fast_stream import (
    FastMarketEvent, FastStreamHealth, _CHANNEL_FRESHNESS_MS,
    _monitoring_projection, _nonnegative_int,
)

from aletheia.net import aiohttp_session, apply_proxy_env


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
