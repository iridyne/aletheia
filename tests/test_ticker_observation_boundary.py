from __future__ import annotations

import inspect

import pytest

from extensions.data.ccxt_provider import CCXTProvider


def test_universe_features_does_not_call_native_ticker_batch() -> None:
    from aletheia.universe import features

    source = inspect.getsource(features)
    assert "fetch_tickers" not in source
    assert "_chunked_fetch_tickers" not in source


@pytest.mark.asyncio
async def test_ccxt_ticker_observation_is_exact_market_and_primary_only(monkeypatch) -> None:
    calls: list[tuple[str, tuple[str, ...], str | None]] = []

    class Exchange:
        options: dict[str, str] = {}
        markets = {
            "BTC/USDT": {"symbol": "BTC/USDT", "spot": True},
            "ETH/USDT:USDT": {
                "symbol": "ETH/USDT:USDT", "contract": True, "contractSize": 1,
            },
        }

        async def fetch_tickers(self, symbols):
            calls.append(("fetch", tuple(symbols), self.options.get("defaultType")))
            return {
                symbol: {
                    "symbol": symbol,
                    "last": 100.0,
                    "bid": 99.0,
                    "ask": 101.0,
                    "quoteVolume": 1_000_000.0,
                    "info": {"fundingRate": "0.001"},
                }
                for symbol in symbols
            }

    exchange = Exchange()
    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: exchange)
    monkeypatch.setattr(
        provider,
        "_ensure_fallback",
        lambda *_args: pytest.fail("feature ticker observation must not use fallback"),
    )

    spot = await provider.get_tickers_observation(
        ["BTC/USDT", "MISSING/USDT", "龙虾/USDT"], market_type="spot",
    )
    perp = await provider.get_tickers_observation(["ETH/USDT"], market_type="perp")

    assert list(spot) == ["BTC/USDT"]
    assert spot["BTC/USDT"]["info"]["fundingRate"] == "0.001"
    assert list(perp) == ["ETH/USDT:USDT"]
    assert calls == [
        ("fetch", ("BTC/USDT",), None),
        ("fetch", ("ETH/USDT:USDT",), "swap"),
    ]
    assert exchange.options == {}


@pytest.mark.asyncio
async def test_ccxt_ticker_observation_keeps_bounded_batch_chunks(monkeypatch) -> None:
    calls: list[int] = []

    class Exchange:
        markets = {f"COIN{i}/USDT": {"symbol": f"COIN{i}/USDT", "spot": True} for i in range(101)}
        options: dict[str, str] = {}

        async def fetch_tickers(self, symbols):
            calls.append(len(symbols))
            return {symbol: {"quoteVolume": 1.0} for symbol in symbols}

    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: Exchange())

    tickers = await provider.get_tickers_observation(
        [f"COIN{i}/USDT" for i in range(101)],
    )

    assert calls == [100, 1]
    assert len(tickers) == 101
