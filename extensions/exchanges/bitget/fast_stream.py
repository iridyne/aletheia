"""Credential-free Bitget fast-market stream adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
import time
from uuid import uuid4

import aiohttp

from aletheia.data.symbols import InstrumentId
from aletheia.universe.fast_stream import (
    FastMarketEvent, FastStreamHealth, _CHANNEL_FRESHNESS_MS,
    _monitoring_projection, _nonnegative_int,
)

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


def register_fast_market_stream() -> None:
    from aletheia.universe.fast_stream import configure_fast_market_stream

    configure_fast_market_stream("bitget_demo", BitgetFastMarketStream,
        market_types=frozenset({"spot", "perp", "futures", "swap", "usdt_futures"}))
