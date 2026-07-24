from __future__ import annotations

import asyncio
import threading

import pytest

from aletheia.data.ccxt_provider import CCXTProvider

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
