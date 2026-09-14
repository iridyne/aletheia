from __future__ import annotations

import asyncio
import threading

import pytest

from extensions.exchanges.ccxt.market_data import CCXTProvider
from aletheia.data.cache import get_cache

_GET_OHLCV = CCXTProvider.get_ohlcv


def test_confirmed_history_writer_does_not_hide_required_write_failure(monkeypatch):
    from aletheia.data.market_history import persist_closed_candles
    from aletheia.data.providers import Candle

    def unavailable(**kwargs):
        raise RuntimeError("required history unavailable")

    monkeypatch.setattr("aletheia.storage.repositories.upsert_ohlcv_candles", unavailable)
    with pytest.raises(RuntimeError, match="required history unavailable"):
        persist_closed_candles("BTC/USDT", "1h", [Candle(1, 10, 11, 9, 10, 1)], "fixture", provider="fixture")


@pytest.mark.asyncio
async def test_history_write_failure_does_not_discard_acquired_candles(monkeypatch):
    class Exchange:
        async def fetch_ohlcv(self, *_args, **_kwargs):
            return [[1, 10, 11, 9, 10.5, 100]]

    provider = CCXTProvider("fixture", "fixture")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: Exchange())
    attempts = []

    def unavailable(**kwargs):
        attempts.append(kwargs)
        raise RuntimeError("fixture storage unavailable")

    monkeypatch.setattr("aletheia.storage.repositories.upsert_ohlcv_candles", unavailable)
    result = await _GET_OHLCV(provider, "BTC/USDT", limit=1, since=0)
    assert len(result) == 1
    assert len(attempts) == 1
    assert attempts[0]["provider"] == "ccxt"
    assert attempts[0]["exchange"] == "fixture"


@pytest.mark.asyncio
async def test_get_ohlcv_persists_outside_scheduler_event_loop(monkeypatch):
    class FakeExchange:
        async def fetch_ohlcv(self, *_args, **_kwargs):
            return [[1, 10, 11, 9, 10.5, 100]]

    provider = CCXTProvider(exchange="fixture", fallback_exchange="fallback")
    fake_exchange = FakeExchange()
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: fake_exchange)

    event_loop_thread = threading.get_ident()
    persist_threads: list[int] = []

    def fake_persist(*_args, **_kwargs):
        persist_threads.append(threading.get_ident())

    monkeypatch.setattr("aletheia.data.market_history.persist_closed_candles", fake_persist)

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


@pytest.mark.real_network
@pytest.mark.asyncio
@pytest.mark.parametrize("since", [None, 0])
async def test_observation_ohlcv_does_not_persist_or_change_venue_health(monkeypatch, since):
    class FakeExchange:
        async def fetch_ohlcv(self, *_args, **_kwargs):
            return [[1, 10, 11, 9, 10.5, 100]]

    provider = CCXTProvider(exchange="fixture", fallback_exchange="fallback")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: FakeExchange())
    monkeypatch.setattr(
        "extensions.exchanges.ccxt.market_data.record_venue_success",
        lambda _name: pytest.fail("observation fetch must not clear venue health"),
    )
    monkeypatch.setattr(
        "extensions.exchanges.ccxt.market_data.record_venue_failure",
        lambda *_args: pytest.fail("observation fetch must not change venue health"),
    )
    monkeypatch.setattr(
        "aletheia.data.market_history.persist_closed_candles",
        lambda *_args, **_kwargs: pytest.fail("observation fetch must not persist OHLCV"),
    )
    cache = get_cache()
    monkeypatch.setattr(cache, "get", lambda *_a, **_kw: pytest.fail("observation must not read shared cache"))
    monkeypatch.setattr(cache, "set", lambda *_a, **_kw: pytest.fail("observation must not write shared cache"))

    candles = await provider.get_ohlcv_observation(
        "BTC/USDT", timeframe="1h", limit=1, since=since
    )

    assert len(candles) == 1
    assert candles[0].exchange == "fixture"


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
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: primary)
    monkeypatch.setattr(provider, "_ensure_fallback", lambda _market_type=None: fallback)
    monkeypatch.setattr("aletheia.data.market_history.persist_closed_candles", lambda *_args, **_kwargs: None)

    first = await _GET_OHLCV(provider, "BTC/USDT", timeframe="1h", limit=1)
    second = await _GET_OHLCV(provider, "BTC/USDT", timeframe="1h", limit=1)

    assert first[0].exchange == "fallback"
    assert second == first
    assert second is not first
    assert first.source == "provider_fetch"
    assert second.source == "memory_cache"
    assert cache.get(
        "BTC/USDT", "1h", 1, market_type="spot", exchange="primary",
    ) is None
    assert cache.get(
        "BTC/USDT", "1h", 1, market_type="spot", exchange="fallback",
    ) is first
    assert primary.calls == 2
    assert fallback.calls == 1
    cache.invalidate()


@pytest.mark.asyncio
async def test_fresh_ohlcv_bypasses_and_does_not_replace_cache(monkeypatch):
    class FakeExchange:
        calls = 0

        async def fetch_ohlcv(self, *_args, **_kwargs):
            self.calls += 1
            return [[1, 10, 12, 9, 10.5 + self.calls, 100]]

    cache = get_cache()
    cache.invalidate()
    cache.set(
        "BTC/USDT",
        "1h",
        2,
        [[1, 10, 12, 9, 10.5, 100]],
        exchange="fixture",
    )
    exchange = FakeExchange()
    provider = CCXTProvider(exchange="fixture", fallback_exchange="fallback")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: exchange)
    monkeypatch.setattr("aletheia.data.market_history.persist_closed_candles", lambda *_args, **_kwargs: None)

    result = await _GET_OHLCV(
        provider,
        "BTC/USDT",
        timeframe="1h",
        limit=2,
        use_cache=False,
    )

    assert result[0].close == 11.5
    assert result.source == "provider_fetch"
    assert exchange.calls == 1
    assert cache.get("BTC/USDT", "1h", 2, exchange="fixture")[0][4] == 10.5
    cache.invalidate()
