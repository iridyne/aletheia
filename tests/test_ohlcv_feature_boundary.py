from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from extensions.data.ccxt_provider import CCXTProvider
from aletheia.data.providers import Candle, OHLCVResult, parse_ohlcv_candle
from aletheia.data.symbols import InstrumentId


def test_universe_features_does_not_call_native_ohlcv() -> None:
    source = Path(__file__).resolve().parents[1] / "core/aletheia/universe/features.py"
    tree = ast.parse(source.read_text())
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "fetch_ohlcv"
        for node in ast.walk(tree)
    )


def test_feature_observation_rows_exclude_forming_candles() -> None:
    from aletheia.universe.features import _observed_candle_rows

    closed = Candle(1_700_000_000_000, 100, 101, 99, 100.5, 10,
                    provider="fixture", exchange="okx")
    forming = Candle(1_700_003_600_000, 100.5, 102, 100, 101.5, 8,
                     is_closed=False, provider="fixture", exchange="okx")

    rows = _observed_candle_rows([closed, forming], venue="okx")

    assert [row[0] for row in rows] == [closed.timestamp]


@pytest.mark.asyncio
async def test_ccxt_ohlcv_observation_is_fresh_primary_only(monkeypatch) -> None:
    calls: list[tuple[str, str, int]] = []

    class Exchange:
        async def fetch_ohlcv(self, symbol, *, timeframe, since=None, limit=None):
            calls.append((symbol, timeframe, limit))
            return [[1_700_000_000_000, 100, 101, 99, 100.5, 10]]

    exchange = Exchange()
    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: exchange)
    monkeypatch.setattr(
        provider,
        "_ensure_fallback",
        lambda *_args: pytest.fail("feature OHLCV observation must not use fallback"),
    )
    monkeypatch.setattr(
        provider,
        "_persist",
        lambda *_args, **_kwargs: pytest.fail("observation must not persist"),
    )

    result = await provider.get_ohlcv_observation(
        InstrumentId.from_parts("okx", "BTC/USDT", "spot"),
        timeframe="1h",
        limit=1,
        use_cache=False,
    )

    assert result[0].exchange == "okx"
    assert result[0].provider == "ccxt"
    assert calls == [("BTC/USDT", "1h", 1)]


@pytest.mark.asyncio
async def test_ccxt_ohlcv_observation_honors_primary_cooldown(monkeypatch) -> None:
    import extensions.data.ccxt_provider as ccxt_module
    from aletheia.data.venue_health import VenueCoolingDownError

    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(ccxt_module, "is_venue_available", lambda _name: False)
    monkeypatch.setattr(
        provider,
        "_ensure_primary",
        lambda *_args: pytest.fail("cooling primary must not construct an exchange"),
    )

    with pytest.raises(VenueCoolingDownError) as exc_info:
        await provider.get_ohlcv_observation(
            InstrumentId.from_parts("okx", "BTC/USDT", "spot"),
            timeframe="1h",
            limit=1,
            use_cache=False,
        )

    assert exc_info.value.ohlcv_attempts[0].provider == "okx"


@pytest.mark.asyncio
async def test_feature_ohlcv_reader_receives_exact_instrument_and_no_raw_exchange_call(
    tmp_path, monkeypatch,
) -> None:
    from tests.test_entry_logic import setup_env
    from aletheia.universe import features

    setup_env(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc)
    calls = []

    class Exchange:
        id = "okx"

        async def fetch_ohlcv(self, *_args, **_kwargs):
            pytest.fail("Core must use the provider observation reader")

    def rows(timeframe: str, limit: int) -> list[list[float]]:
        interval = {"15m": 900_000, "1h": 3_600_000, "4h": 14_400_000}[timeframe]
        end = int(now.timestamp() * 1000) // interval * interval - interval
        return [
            [end - (limit - 1 - index) * interval, 100, 101, 99, 100 + index / 100, 10]
            for index in range(limit)
        ]

    async def reader(instrument, *, timeframe, limit, use_cache):
        calls.append((instrument, timeframe, limit, use_cache))
        candles = [
            candle
            for row in rows(timeframe, limit)
            if (candle := parse_ohlcv_candle(
                row, provider="fixture", exchange=instrument.exchange,
            )) is not None
        ]
        return OHLCVResult(candles, requested=limit)

    exchange = Exchange()
    feature = await features._fetch_single_feature(
        exchange,
        {"id": "asset-btc", "venue": "okx", "market_type": "spot", "symbol": "BTC/USDT"},
        {"quoteVolume": 1_000_000, "bid": 99, "ask": 101},
        now,
        lambda symbol: symbol,
        snapshot_buffer=[],
        ohlcv_reader=reader,
    )

    assert feature is not None
    assert [call[1] for call in calls] == ["15m", "1h", "4h"]
    assert all(call[0] == InstrumentId.from_parts("okx", "BTC/USDT", "spot") for call in calls)
    assert all(call[3] is False for call in calls)
