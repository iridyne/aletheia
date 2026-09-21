"""Native taker acquisition belongs to Data; its trigger remains Core-owned."""

import ast
from pathlib import Path

import pytest


def test_core_features_do_not_call_native_taker_endpoints():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "aletheia/universe/features.py").read_text())
    native = {"fapipublic_get_takerlongshortratio", "public_get_rubik_stat_taker_volume"}
    assert not any(isinstance(node, ast.Attribute) and node.attr in native for node in ast.walk(tree))


@pytest.mark.asyncio
@pytest.mark.parametrize("venue,raw,expected", [
    ("binance", [{"buySellRatio": "2.5"}], 2.5),
    ("binance", [{"buySellRatio": "0"}], None),
    ("binance", [], None),
    ("okx", {"data": [[1, "2", "5"]]}, 2.5),
    ("okx", {"data": [[1, "2", "0"]]}, 0.0),
    ("okx", {"data": [[1, "0", "5"]]}, None),
    ("bitget", None, None),
    ("binanceusdm", None, None),
])
async def test_adapter_preserves_existing_native_ratio_semantics(monkeypatch, venue, raw, expected):
    from extensions.data.ccxt_provider import CCXTProvider

    calls = []

    class Exchange:
        id = venue

        async def fapipublic_get_takerlongshortratio(self, params):
            calls.append(params)
            assert params == {"symbol": "BTCUSDT", "period": "1h", "limit": 1}
            return raw

        async def public_get_rubik_stat_taker_volume(self, params):
            calls.append(params)
            assert params == {"ccy": "BTC", "instType": "CONTRACTS", "period": "1H"}
            return raw

    provider = CCXTProvider(exchange=venue, fallback_exchange="other")
    monkeypatch.setattr(provider, "_ensure_primary", lambda market_type: Exchange())
    monkeypatch.setattr(provider, "_ensure_fallback", lambda *args: pytest.fail("taker must not cross venue"))
    assert await provider.get_taker_buy_ratio("BTC/USDT") == expected
    assert len(calls) == (1 if venue in {"binance", "okx"} else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("market,move,mode,called", [
    ("perp", 4, "present", True),
    ("perp", -4, "present", True),
    ("perp", 3, "present", False),
    ("spot", 4, "present", False),
    ("perp", 4, "absent", False),
    ("perp", 4, "error", True),
])
async def test_core_owns_taker_trigger_and_optional_failure(monkeypatch, market, move, mode, called):
    from datetime import datetime, timedelta, timezone
    from aletheia.universe import features

    now = datetime.now(timezone.utc)
    calls = []

    class Exchange:
        id = "fixture"

        async def fetch_ohlcv(self, *args, **kwargs):
            return [[int((now - timedelta(days=2-i)).timestamp() * 1000), 100, 110, 90, 100 + i*move, 1000] for i in range(2)]

    async def read(symbol):
        calls.append(symbol)
        if mode == "error":
            raise TimeoutError("optional taker unavailable")
        return 2.5

    from tests.ohlcv_test_helpers import observation_reader
    monkeypatch.setattr(features.repo, "get_latest_feature_snapshot", lambda *args: None)
    exchange = Exchange()
    result = await features._fetch_single_feature(
        exchange, {"id": "fixture", "venue": "fixture", "market_type": market, "symbol": "BTC/USDT"},
        {"bid": 99, "ask": 101}, now, lambda symbol: symbol + ":USDT",
        snapshot_buffer=[], taker_reader=None if mode == "absent" else read,
        ohlcv_reader=observation_reader(exchange),
    )
    assert result is not None
    assert calls == (["BTC/USDT"] if called else [])
    assert result["taker_buy_ratio"] == (2.5 if called and mode == "present" else None)
