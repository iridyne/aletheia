"""Phase 8: CCXT Pro FastMarketStream adapter — deterministic transport contract tests.

Acceptance checks for #3699 Phase 8:
- Channel not ready before first fact; ready after a fresh fact
- Readiness is facts-based, no dependency on protocol ACK
- Local sequence strictly increasing per (instrument, channel)
- Reconnect (on_connected) produces a new stream_generation_id and clears channel facts
- Instrument change (set_instruments) produces a new subscription_generation_id
- Facts from old generation do not make a channel ready after reconnect
- Stale/malformed watch return fails closed (no event emitted, no channel readiness)
- Missing/malformed source timestamp fails closed — receive time is never exchange time
- Quote source is per venue: binanceusdm uses watch_bids_asks, bitget keeps watch_ticker
- Binance depth gap (watch_order_book raising) clears depth channel readiness and re-warms
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.data.symbols import InstrumentId
from aletheia.universe.fast_stream import FastMarketEvent


def _instrument(exchange="bitget", symbol="BTC/USDT", market_type="perp"):
    return InstrumentId.from_parts(exchange, symbol, market_type)


def _ticker(bid=100.0, ask=100.1, ts=1_000_000):
    return {"bid": bid, "ask": ask, "timestamp": ts}


def _trade(price=100.0, amount=1.0, side="buy", ts=1_000_000):
    return {"price": price, "amount": amount, "side": side, "timestamp": ts}


def _orderbook(bids=None, asks=None, ts=1_000_000):
    return {
        "bids": bids or [[100.0, 1.0], [99.9, 2.0]],
        "asks": asks or [[100.1, 1.0], [100.2, 2.0]],
        "timestamp": ts,
    }


def _ohlcv(o=1, h=2, l=1, c=2, v=10, ts=60_000):
    return [ts, o, h, l, c, v]


def _blocking_watch():
    """AsyncMock that blocks forever after the first call (prevents tight spin loops)."""
    call_count = 0

    async def _inner(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None  # first call returns None (will fail-close)
        await asyncio.sleep(3600)  # block all subsequent calls

    return _inner


def _make_exchange_mock():
    """Exchange mock with blocking watches so background tasks don't spin."""
    ex = MagicMock()
    ex.on_connected = MagicMock()
    ex.ws_dial_backoff = {}
    ex.watch_ticker = _blocking_watch()
    ex.watch_bids_asks = _blocking_watch()
    ex.watch_trades = _blocking_watch()
    ex.watch_order_book = _blocking_watch()
    ex.watch_ohlcv = _blocking_watch()
    return ex


async def _connect_no_instruments(adapter, exchange=None, session=None):
    """Connect adapter without starting any watch tasks."""
    if exchange is None:
        exchange = _make_exchange_mock()
    if session is None:
        session = MagicMock()
    with patch(
        "extensions.data.fast_stream_ccxt_pro.make_ccxt_pro",
        return_value=(exchange, session),
    ):
        await adapter.connect()  # no instruments → no tasks spawned
    return exchange, session


def _fresh_fact(inst, channel, now_ms=None):
    """Create a fresh FastMarketEvent for (inst, channel)."""
    now_ms = now_ms or int(time.time() * 1_000)
    mapping = {"quote": "quote", "trade": "trade", "depth": "depth", "candle": "candle"}
    event_type = mapping[channel]
    kwargs = dict(
        event_type=event_type,
        instrument=inst,
        exchange_timestamp_ms=now_ms,
        received_timestamp_ms=now_ms,
        sequence=1,
    )
    if event_type == "quote":
        kwargs.update(bid=100.0, ask=100.1)
    elif event_type == "trade":
        kwargs.update(price=100.0, size=1.0, side="buy")
    elif event_type == "depth":
        kwargs.update(bids=((100.0, 1.0),), asks=((100.1, 1.0),))
    elif event_type == "candle":
        kwargs.update(candle=(1.0, 2.0, 1.0, 2.0, 10.0))
    return FastMarketEvent(**kwargs)


# ── Channel readiness ─────────────────────────────────────────────────────────

async def test_channel_not_ready_before_first_fact():
    """No channel is ready immediately after connect, before any event arrives."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    state = adapter.monitoring_state(inst, ("quote", "depth"))
    assert state["state"] == "warming"  # connected but no events observed yet
    assert state["channels"]["quote"]["state"] == "warming"
    assert state["channels"]["depth"]["state"] == "warming"


async def test_channel_ready_after_fresh_fact():
    """A channel becomes ready after a fresh fact event is recorded for it."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    adapter._channel_facts[(inst, "quote")] = _fresh_fact(inst, "quote")

    state = adapter.monitoring_state(inst, ("quote",))
    assert state["channels"]["quote"]["state"] == "ready"


async def test_unavailable_after_disconnect():
    """After close(), state is unavailable and health.connected is False."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    with patch("extensions.data.fast_stream_ccxt_pro.close_ccxt", new_callable=AsyncMock):
        await adapter.close()

    assert adapter.health().connected is False
    state = adapter.monitoring_state(inst, ("quote",))
    assert state["state"] == "unavailable"


# ── Local sequence ────────────────────────────────────────────────────────────

def test_local_sequence_strictly_increasing():
    """_next_seq produces strictly increasing integers per (instrument, channel)."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    seqs = [adapter._next_seq(inst, "quote") for _ in range(5)]
    assert seqs == list(range(1, 6))


def test_sequence_independent_per_channel():
    """Sequence counters are independent across channels for the same instrument."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    q1 = adapter._next_seq(inst, "quote")
    d1 = adapter._next_seq(inst, "depth")
    q2 = adapter._next_seq(inst, "quote")
    assert q1 == 1 and d1 == 1 and q2 == 2


def test_sequence_independent_across_instruments():
    """Sequence counters are independent per instrument."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst_a = _instrument(symbol="BTC/USDT")
    inst_b = _instrument(symbol="ETH/USDT")
    adapter = CcxtProFastMarketStream("bitget")
    a1 = adapter._next_seq(inst_a, "quote")
    b1 = adapter._next_seq(inst_b, "quote")
    a2 = adapter._next_seq(inst_a, "quote")
    assert a1 == 1 and b1 == 1 and a2 == 2


# ── Stream generation ─────────────────────────────────────────────────────────

async def test_reconnect_produces_new_stream_generation_id():
    """on_connected override bumps stream_generation_id on each WS connection."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    adapter = CcxtProFastMarketStream("bitget")
    exchange, _ = await _connect_no_instruments(adapter)

    gen_before = adapter._stream_generation_id
    assert gen_before is not None

    fake_client = MagicMock()
    fake_client.url = "wss://ws.bitget.com/v2/ws/public"
    exchange.on_connected(fake_client)

    gen_after = adapter._stream_generation_id
    assert gen_after is not None
    assert gen_after != gen_before


async def test_reconnect_clears_channel_facts():
    """on_connected wipe clears all channel_facts so stale facts don't survive reconnect."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    exchange, _ = await _connect_no_instruments(adapter)

    adapter._channel_facts[(inst, "quote")] = _fresh_fact(inst, "quote")
    assert (inst, "quote") in adapter._channel_facts

    fake_client = MagicMock()
    fake_client.url = "wss://ws.bitget.com/v2/ws/public"
    exchange.on_connected(fake_client)

    assert adapter._channel_facts == {}
    state = adapter.monitoring_state(inst, ("quote",))
    assert state["channels"]["quote"]["state"] == "warming"


async def test_old_generation_fact_cleared_on_reconnect():
    """After reconnect a fact written before reconnect no longer makes the channel ready."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    exchange, _ = await _connect_no_instruments(adapter)

    # Record a fresh fact in old generation
    adapter._channel_facts[(inst, "quote")] = _fresh_fact(inst, "quote")
    old_gen = adapter._stream_generation_id

    # Reconnect — bumps generation and clears facts
    fake_client = MagicMock()
    fake_client.url = "wss://ws.bitget.com/v2/ws/public"
    exchange.on_connected(fake_client)

    assert adapter._stream_generation_id != old_gen
    assert (inst, "quote") not in adapter._channel_facts
    state = adapter.monitoring_state(inst, ("quote",))
    assert state["channels"]["quote"]["state"] == "warming"


# ── Subscription generation ───────────────────────────────────────────────────

async def test_set_instruments_produces_new_subscription_generation_id():
    """set_instruments bumps subscription_generation_id on instrument change."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst1 = _instrument(symbol="BTC/USDT")
    inst2 = _instrument(symbol="ETH/USDT")
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    await adapter.set_instruments({inst1})
    sub_gen_1 = adapter._subscription_generation_id
    assert sub_gen_1 is not None

    await adapter.set_instruments({inst1, inst2})
    sub_gen_2 = adapter._subscription_generation_id

    assert sub_gen_2 is not None
    assert sub_gen_2 != sub_gen_1

    # Cancel spawned tasks before exiting
    for tasks in adapter._instrument_tasks.values():
        for t in tasks:
            t.cancel()


# ── Event mapping ─────────────────────────────────────────────────────────────

def test_map_ticker_produces_quote_event():
    """_map_ticker produces a valid quote FastMarketEvent with bid/ask."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    event = adapter._map_ticker(inst, _ticker(bid=99.9, ask=100.1, ts=12345))
    assert event is not None
    assert event.event_type == "quote"
    assert event.bid == pytest.approx(99.9)
    assert event.ask == pytest.approx(100.1)
    assert event.exchange_timestamp_ms == 12345


def test_map_ticker_returns_none_when_bid_missing():
    """_map_ticker returns None when bid or ask is absent (fail closed)."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    assert adapter._map_ticker(inst, {"ask": 100.0}) is None
    assert adapter._map_ticker(inst, {"bid": 100.0}) is None
    assert adapter._map_ticker(inst, {}) is None


def test_map_trade_produces_trade_event():
    """_map_trade produces a valid trade FastMarketEvent."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    event = adapter._map_trade(inst, _trade(price=100.5, amount=2.0, side="sell", ts=9999))
    assert event is not None
    assert event.event_type == "trade"
    assert event.price == pytest.approx(100.5)
    assert event.size == pytest.approx(2.0)
    assert event.side == "sell"


def test_map_orderbook_produces_depth_event():
    """_map_orderbook produces a valid depth FastMarketEvent capped at 5 levels."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    ob = _orderbook(
        bids=[[100.0, 1.0], [99.9, 2.0], [99.8, 3.0], [99.7, 4.0], [99.6, 5.0], [99.5, 6.0]],
        asks=[[100.1, 1.0]],
    )
    event = adapter._map_orderbook(inst, ob)
    assert event is not None
    assert event.event_type == "depth"
    assert len(event.bids) == 5
    assert event.bids[0] == (pytest.approx(100.0), pytest.approx(1.0))


def test_map_ohlcv_produces_candle_event():
    """_map_ohlcv produces a valid candle FastMarketEvent."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    event = adapter._map_ohlcv(inst, _ohlcv(o=10, h=20, l=5, c=15, v=100, ts=60_000))
    assert event is not None
    assert event.event_type == "candle"
    assert event.candle == pytest.approx((10, 20, 5, 15, 100))
    assert event.exchange_timestamp_ms == 60_000


def test_map_orderbook_returns_none_when_empty():
    """_map_orderbook returns None when bids or asks are empty (fail closed)."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    assert adapter._map_orderbook(inst, {"bids": [], "asks": [[100.0, 1.0]]}) is None
    assert adapter._map_orderbook(inst, {"bids": [[100.0, 1.0]], "asks": []}) is None
    assert adapter._map_orderbook(inst, {}) is None


def test_mappers_fail_closed_when_source_timestamp_unusable():
    """A missing/malformed source time drops the fact instead of stamping local now.

    Gate 3 requires source time "without inventing": ``exchange_timestamp_ms`` is
    the exchange's own clock, so promoting the receive clock into it would make an
    unproven fact look fresh to entry activation.
    """
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")

    ticker = _ticker()
    trade = _trade()
    orderbook = _orderbook()
    del ticker["timestamp"]
    del trade["timestamp"]
    del orderbook["timestamp"]

    assert adapter._map_ticker(inst, ticker) is None  # missing timestamp key
    assert adapter._map_trade(inst, trade) is None
    assert adapter._map_orderbook(inst, orderbook) is None
    assert adapter._map_ohlcv(inst, [None, 1, 2, 1, 2, 10]) is None  # bar[0] missing

    assert adapter._map_ticker(inst, {**_ticker(), "timestamp": 0}) is None  # non-positive
    assert adapter._map_trade(inst, {**_trade(), "timestamp": "not-a-time"}) is None  # non-numeric
    assert adapter._map_orderbook(inst, {**_orderbook(), "timestamp": None}) is None  # explicit None


# ── Quote source per venue ────────────────────────────────────────────────────

def _quote_payload(ts):
    return {"bid": 100.0, "ask": 100.1, "timestamp": ts}


async def _run_quote_loop_briefly(adapter, inst):
    """Run the quote watch loop briefly, delivering one payload, then block."""
    calls = {"ticker": 0, "bids_asks": 0}
    payload = _quote_payload(int(time.time() * 1_000))

    async def watch_bids_asks(symbols, *_args, **_kwargs):
        calls["bids_asks"] += 1
        if calls["bids_asks"] == 1:
            return {symbols[0]: payload}
        await asyncio.sleep(3600)

    async def watch_ticker(*_args, **_kwargs):
        calls["ticker"] += 1
        if calls["ticker"] == 1:
            return payload
        await asyncio.sleep(3600)

    adapter._exchange = _make_exchange_mock()
    adapter._exchange.watch_bids_asks = watch_bids_asks
    adapter._exchange.watch_ticker = watch_ticker

    task = asyncio.ensure_future(adapter._watch_loop(inst, "ticker"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return calls


async def test_binanceusdm_quote_comes_from_book_ticker():
    """binanceusdm's bookless watch_ticker is never used for the quote fact."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument(exchange="binance", symbol="BTC/USDT", market_type="perp")
    adapter = CcxtProFastMarketStream("binanceusdm")
    await _connect_no_instruments(adapter)

    calls = await _run_quote_loop_briefly(adapter, inst)

    assert calls["ticker"] == 0
    assert calls["bids_asks"] >= 1
    fact = adapter._channel_facts[(inst, "quote")]
    assert fact.bid == pytest.approx(100.0)
    assert fact.ask == pytest.approx(100.1)

    class _NoSuchSymbol:
        async def watch_bids_asks(self, _symbols):
            return {}

    assert await adapter._watch_quote(_NoSuchSymbol(), "BTC/USDT:USDT") == {}


async def test_bitget_quote_still_comes_from_watch_ticker():
    """bitget's watch_ticker already carries bid/ask; keep that quote source."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument(exchange="bitget", symbol="BTC/USDT", market_type="perp")
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    calls = await _run_quote_loop_briefly(adapter, inst)

    assert calls["bids_asks"] == 0
    assert calls["ticker"] >= 1
    assert adapter._channel_facts[(inst, "quote")].bid == pytest.approx(100.0)


# ── Depth gap handling ────────────────────────────────────────────────────────
async def test_depth_gap_clears_channel_readiness():
    """When watch_order_book raises, depth channel fact is cleared (fail-closed)."""
    from extensions.data.fast_stream_ccxt_pro import CcxtProFastMarketStream

    inst = _instrument()
    adapter = CcxtProFastMarketStream("bitget")
    await _connect_no_instruments(adapter)

    async def raising_then_block(symbol, limit=None):
        raise Exception("depth gap: sequence discontinuity")

    adapter._exchange = _make_exchange_mock()
    adapter._exchange.watch_order_book = raising_then_block

    # Pre-load a stale depth fact
    adapter._channel_facts[(inst, "depth")] = _fresh_fact(inst, "depth")
    assert (inst, "depth") in adapter._channel_facts

    # Run one iteration of the depth watch loop
    task = asyncio.ensure_future(adapter._watch_loop(inst, "orderbook"))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Depth fact should be cleared after gap detection
    assert (inst, "depth") not in adapter._channel_facts
    state = adapter.monitoring_state(inst, ("depth",))
    assert state["channels"]["depth"]["state"] == "warming"


# ── Symbol mapping ────────────────────────────────────────────────────────────

def test_ccxt_symbol_perp_appends_usdt_suffix():
    """Perp instruments map to CCXT 'BASE/QUOTE:USDT' format."""
    from extensions.data.fast_stream_ccxt_pro import _ccxt_symbol

    perp = _instrument(symbol="BTC/USDT", market_type="perp")
    assert _ccxt_symbol(perp) == "BTC/USDT:USDT"


def test_ccxt_symbol_spot_unchanged():
    """Spot instruments use the plain CCXT symbol."""
    from extensions.data.fast_stream_ccxt_pro import _ccxt_symbol

    spot = _instrument(symbol="BTC/USDT", market_type="spot")
    assert _ccxt_symbol(spot) == "BTC/USDT"
