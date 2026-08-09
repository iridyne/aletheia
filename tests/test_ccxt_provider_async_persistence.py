from __future__ import annotations

import asyncio
import threading

import pytest

from aletheia.data.ccxt_provider import CCXTProvider
from aletheia.data.cache import get_cache

_GET_OHLCV = CCXTProvider.get_ohlcv


@pytest.mark.asyncio
async def test_get_ohlcv_persists_outside_scheduler_event_loop(monkeypatch):
    class FakeExchange:
        async def fetch_ohlcv(self, *_args, **_kwargs):
            return [[1, 10, 11, 9, 10.5, 100]]

    provider = CCXTProvider(exchange="fixture", fallback_exchange="fallback")
    fake_exchange = FakeExchange()
    monkeypatch.setattr(provider, "_ensure_primary", lambda: fake_exchange)

    event_loop_thread = threading.get_ident()
    persist_threads: list[int] = []

    def fake_persist(*_args, **_kwargs):
        persist_threads.append(threading.get_ident())

    monkeypatch.setattr(provider, "_persist", fake_persist)

    candles = await _GET_OHLCV(
        provider,
        "BTC/USDT",
        timeframe="1h",
        limit=1,
        since=0,
    )

    assert len(candles) == 1
    assert persist_threads
    assert persist_threads[0] != event_loop_thread
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_fallback_ohlcv_cache_keeps_actual_venue_identity(monkeypatch):
    class FakeExchange:
        def __init__(self, *, fails: bool = False):
            self.fails = fails
            self.calls = 0

        async def fetch_ohlcv(self, *_args, **_kwargs):
            self.calls += 1
            if self.fails:
                raise ConnectionError("primary unavailable")
            return [[1, 10, 11, 9, 10.5, 100]]

    cache = get_cache()
    cache.invalidate()
    primary = FakeExchange(fails=True)
    fallback = FakeExchange()
    provider = CCXTProvider(exchange="primary", fallback_exchange="fallback")
    monkeypatch.setattr(provider, "_ensure_primary", lambda: primary)
    monkeypatch.setattr(provider, "_ensure_fallback", lambda: fallback)
    monkeypatch.setattr(provider, "_persist", lambda *_args, **_kwargs: None)

    first = await _GET_OHLCV(provider, "BTC/USDT", timeframe="1h", limit=1)
    second = await _GET_OHLCV(provider, "BTC/USDT", timeframe="1h", limit=1)

    assert first[0].exchange == "fallback"
    assert second is first
    assert cache.get(
        "BTC/USDT", "1h", 1, market_type="spot", exchange="primary",
    ) is None
    assert cache.get(
        "BTC/USDT", "1h", 1, market_type="spot", exchange="fallback",
    ) is first
    assert primary.calls == 2
    assert fallback.calls == 1
    cache.invalidate()
