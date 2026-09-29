"""CCXT Pro FastMarketStream adapter — thin wrapper over ccxt.pro WebSocket transport.

CCXT Pro owns: WebSocket transport, subscribe/unsubscribe, reconnect, venue parsing,
ticker/trade/OHLCV/orderbook, Binance snapshot/delta protocol.

This adapter owns: FastMarketEvent mapping, stream_generation_id,
subscription_generation_id, local monotonic sequence, receive timestamp,
current-generation channel readiness, Aletheia monitoring projection.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

from aletheia.data.symbols import InstrumentId
from aletheia.net import close_ccxt, make_ccxt_pro
from aletheia.universe.fast_stream import (
    FastMarketEvent,
    FastStreamHealth,
    _monitoring_projection,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("aletheia.universe.fast_stream_ccxt_pro")

_CHANNELS = ("ticker", "trades", "orderbook", "ohlcv")
_CHANNEL_TO_EVENT = {"ticker": "quote", "trades": "trade", "orderbook": "depth", "ohlcv": "candle"}


def _source_ms(value) -> int | None:
    """CCXT-provided source time in milliseconds, or None when unusable.

    Receive time is NEVER substituted for exchange time. Entry activation trusts
    ``exchange_timestamp_ms`` as the exchange's own clock, so inventing it from
    the local clock would turn an unproven fact into a fresh one.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return milliseconds if milliseconds > 0 else None


def _drop_without_source_time(instrument: InstrumentId, channel: str) -> None:
    logger.debug(
        "fast_stream_ccxt_pro: %s %s has no source timestamp — dropped, "
        "exchange time not invented from local clock",
        instrument.symbol,
        channel,
    )


def _ccxt_symbol(instrument: InstrumentId) -> str:
    """Map InstrumentId to CCXT unified symbol."""
    base = instrument.symbol  # e.g. "BTC/USDT"
    if instrument.market_type in {"perp", "futures", "swap", "usdt_futures"}:
        return f"{base}:USDT"
    return base


class CcxtProFastMarketStream:
    """FastMarketStream implementation backed by ccxt.pro WebSocket transport."""

    def __init__(self, exchange_name: str) -> None:
        self._exchange_name = exchange_name
        self._exchange = None
        self._session = None
        self._instruments: set[InstrumentId] = set()
        self._stream_generation_id: str | None = None
        self._subscription_generation_id: str | None = None
        self._channel_facts: dict[tuple[InstrumentId, str], FastMarketEvent] = {}
        self._sequences: dict[tuple[InstrumentId, str], int] = {}
        self._instrument_tasks: dict[InstrumentId, list[asyncio.Task]] = {}
        self._event_queue: asyncio.Queue[FastMarketEvent] = asyncio.Queue(maxsize=2048)
        self._health = FastStreamHealth(connected=False)
        self._connected = False

    # ── FastMarketStream Protocol ──────────────────────────────────────────────

    async def connect(self, instruments: set[InstrumentId] | None = None) -> None:
        self._exchange, self._session = make_ccxt_pro(self._exchange_name)

        self._stream_generation_id = str(uuid4())
        original_on_connected = self._exchange.on_connected

        adapter = self

        def _on_ws_connected(client):
            original_on_connected(client)  # clears ws_dial_backoff, preserves default behavior
            new_gen = str(uuid4())
            logger.debug("ws reconnect detected url=%s new_stream_gen=%s", client.url, new_gen)
            adapter._stream_generation_id = new_gen
            # Clear all channel facts on reconnect — they belong to the old generation
            adapter._channel_facts.clear()

        self._exchange.on_connected = _on_ws_connected

        self._connected = True
        self._health = FastStreamHealth(connected=True)

        if instruments:
            await self.set_instruments(instruments)

    async def set_instruments(self, instruments: set[InstrumentId]) -> None:
        added = instruments - self._instruments
        removed = self._instruments - instruments

        for inst in removed:
            await self._cancel_instrument_tasks(inst)
            self._channel_facts = {k: v for k, v in self._channel_facts.items() if k[0] != inst}

        self._instruments = set(instruments)
        self._subscription_generation_id = str(uuid4())

        for inst in added:
            self._instrument_tasks[inst] = [
                asyncio.ensure_future(self._watch_loop(inst, ch))
                for ch in _CHANNELS
            ]

    def events(self) -> AsyncIterator[FastMarketEvent]:
        return self._event_iter()

    def health(self) -> FastStreamHealth:
        return self._health

    def monitoring_state(
        self,
        instrument: InstrumentId,
        required_channels: tuple[str, ...],
    ) -> dict:
        return _monitoring_projection(
            instrument=instrument,
            required_channels=required_channels,
            connected=self._connected,
            stream_generation_id=self._stream_generation_id,
            subscription_generation_id=self._subscription_generation_id,
            channel_facts=self._channel_facts,
            reason=self._health.reason if not self._connected else None,
        )

    async def close(self) -> None:
        self._connected = False
        self._health = FastStreamHealth(connected=False)
        for tasks in self._instrument_tasks.values():
            for t in tasks:
                t.cancel()
        self._instrument_tasks.clear()
        if self._exchange is not None:
            await close_ccxt(self._exchange, self._session)
            self._exchange = None
            self._session = None

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _cancel_instrument_tasks(self, instrument: InstrumentId) -> None:
        for t in self._instrument_tasks.pop(instrument, []):
            t.cancel()

    async def _event_iter(self) -> AsyncIterator[FastMarketEvent]:  # type: ignore[override]
        while True:
            event = await self._event_queue.get()
            yield event

    def _next_seq(self, instrument: InstrumentId, channel: str) -> int:
        key = (instrument, channel)
        self._sequences[key] = self._sequences.get(key, 0) + 1
        return self._sequences[key]

    async def _watch_loop(self, instrument: InstrumentId, channel: str) -> None:
        symbol = _ccxt_symbol(instrument)
        assert self._exchange is not None
        exchange = self._exchange
        event_type = _CHANNEL_TO_EVENT[channel]

        while True:
            try:
                if channel == "ticker":
                    data = await exchange.watch_ticker(symbol)
                    event = self._map_ticker(instrument, data)
                elif channel == "trades":
                    data = await exchange.watch_trades(symbol)
                    # Returns list of trades; take most recent
                    if not data:
                        continue
                    event = self._map_trade(instrument, data[-1])
                elif channel == "orderbook":
                    try:
                        data = await exchange.watch_order_book(symbol, 5)
                        event = self._map_orderbook(instrument, data)
                    except Exception as depth_err:
                        # CCXT Pro fail-closed: gap detected, orderbook reset
                        logger.debug(
                            "depth gap/reset for %s: %s", instrument.symbol, depth_err
                        )
                        # Clear depth channel readiness; loop will re-warm on next event
                        self._channel_facts.pop((instrument, "depth"), None)
                        await asyncio.sleep(0.1)
                        continue
                elif channel == "ohlcv":
                    data = await exchange.watch_ohlcv(symbol, "1m")
                    # Returns list of OHLCV bars; take most recent
                    if not data:
                        continue
                    event = self._map_ohlcv(instrument, data[-1])
                else:
                    break

                if event is None:
                    continue

                received_gen = self._stream_generation_id
                # Stamp the event with current generation IDs captured after the await
                event = event.model_copy(
                    update={
                        "stream_generation_id": received_gen,
                        "subscription_generation_id": self._subscription_generation_id,
                    }
                )

                self._channel_facts[(instrument, event_type)] = event
                self._health = FastStreamHealth(
                    connected=True,
                    subscriptions_confirmed=True,
                    last_received_at_ms=event.received_timestamp_ms,
                )
                try:
                    self._event_queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.debug(
                        "fast_stream: queue full, dropping %s/%s event",
                        instrument.symbol,
                        event_type,
                    )

            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "fast_stream_ccxt_pro: %s %s error: %s",
                    instrument.symbol,
                    channel,
                    exc,
                )
                await asyncio.sleep(1.0)

    def _ts_now(self) -> int:
        return int(time.time() * 1_000)

    def _map_ticker(self, instrument: InstrumentId, ticker: dict) -> FastMarketEvent | None:
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is None or ask is None:
            return None
        source_ms = _source_ms(ticker.get("timestamp"))
        if source_ms is None:
            _drop_without_source_time(instrument, "ticker")
            return None
        return FastMarketEvent(
            event_type="quote",
            instrument=instrument,
            exchange_timestamp_ms=source_ms,
            received_timestamp_ms=self._ts_now(),
            sequence=self._next_seq(instrument, "quote"),
            bid=float(bid),
            ask=float(ask),
        )

    def _map_trade(self, instrument: InstrumentId, trade: dict) -> FastMarketEvent | None:
        price = trade.get("price")
        amount = trade.get("amount")
        if price is None:
            return None
        side_raw = str(trade.get("side") or "").lower()
        side = "buy" if side_raw == "buy" else "sell" if side_raw == "sell" else None
        source_ms = _source_ms(trade.get("timestamp"))
        if source_ms is None:
            _drop_without_source_time(instrument, "trades")
            return None
        return FastMarketEvent(
            event_type="trade",
            instrument=instrument,
            exchange_timestamp_ms=source_ms,
            received_timestamp_ms=self._ts_now(),
            sequence=self._next_seq(instrument, "trade"),
            price=float(price),
            size=float(amount) if amount is not None else None,
            side=side,
        )

    def _map_orderbook(self, instrument: InstrumentId, ob: dict) -> FastMarketEvent | None:
        bids_raw = ob.get("bids") or []
        asks_raw = ob.get("asks") or []
        if not bids_raw or not asks_raw:
            return None
        bids = tuple((float(p), float(s)) for p, s in bids_raw[:5])
        asks = tuple((float(p), float(s)) for p, s in asks_raw[:5])
        source_ms = _source_ms(ob.get("timestamp"))
        if source_ms is None:
            _drop_without_source_time(instrument, "orderbook")
            return None
        return FastMarketEvent(
            event_type="depth",
            instrument=instrument,
            exchange_timestamp_ms=source_ms,
            received_timestamp_ms=self._ts_now(),
            sequence=self._next_seq(instrument, "depth"),
            bids=bids,
            asks=asks,
        )

    def _map_ohlcv(self, instrument: InstrumentId, bar: list) -> FastMarketEvent | None:
        # bar = [timestamp_ms, open, high, low, close, volume]
        if not bar or len(bar) < 6:
            return None
        source_ms = _source_ms(bar[0])
        if source_ms is None:
            _drop_without_source_time(instrument, "ohlcv")
            return None
        o, h, l, c, v = float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]), float(bar[5])
        return FastMarketEvent(
            event_type="candle",
            instrument=instrument,
            exchange_timestamp_ms=source_ms,
            received_timestamp_ms=self._ts_now(),
            sequence=self._next_seq(instrument, "candle"),
            candle=(o, h, l, c, v),
        )
