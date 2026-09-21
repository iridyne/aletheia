"""Core standard market reads use the existing normalized provider contract."""

import ast
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest


@pytest.mark.parametrize("module", ["server/routes/data.py", "trading/market_context.py", "trading/market_data.py", "universe/outcome_recorder.py", "portfolio/position_path_observer.py", "universe/registry.py", "universe/features.py", "data/price_feed.py", "data/history_downloader.py"])
def test_standard_core_consumers_do_not_import_concrete_data(module):
    tree = ast.parse((Path(__file__).resolve().parents[1] / "aletheia" / module).read_text())
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "extensions.data.ccxt_provider"
        for node in ast.walk(tree)
    )


def test_feature_scans_do_not_bootstrap_raw_exchange_clients():
    source = (Path(__file__).resolve().parents[1] / "aletheia" / "universe" / "features.py").read_text()
    assert "_ensure_primary" not in source
    assert ".load_markets" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("symbol, market", [("BTC/USDT", "spot"), ("BTC/USDT", "perp"), ("AAPLON/USDT", "spot")])
@pytest.mark.parametrize("quote_fails", [False, True])
async def test_price_refresh_uses_exact_bound_provider(monkeypatch, symbol, market, quote_fails):
    from extensions.data import ccxt_provider
    from aletheia.data import price_feed, providers
    from aletheia.data.symbols import InstrumentId

    instrument = InstrumentId.from_parts("bitget", symbol, market)
    calls, closed = [], []
    monkeypatch.setattr(price_feed, "get_watched_instruments", lambda **kwargs: [instrument])
    monkeypatch.setattr(price_feed, "_price_cache", {})
    monkeypatch.setattr(price_feed, "_instrument_price_cache", {})
    monkeypatch.setattr(price_feed, "_instrument_refresh_attempted", set())
    monkeypatch.setattr(price_feed, "_price_cache_owner_process", False)

    class Provider:
        async def get_ticker(self, target):
            assert target == instrument
            if quote_fails:
                raise RuntimeError("quote unavailable")
            return providers.Ticker(symbol, 100, 1000, 0, provider="bitget")

        async def close(self):
            closed.append(True)

    def factory(exchange, *, exact_venue, fallback_venue):
        calls.append((exchange, exact_venue, fallback_venue))
        return Provider()

    def forbidden(*args, **kwargs):
        raise AssertionError("concrete provider bypass")

    monkeypatch.setattr(providers, "_provider_factory", factory)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", forbidden)
    monkeypatch.setattr(ccxt_provider, "CCXTProvider", forbidden)
    result = await price_feed.refresh_watched_prices()
    assert calls == [("bitget", True, None)]
    assert closed == [True]
    assert result["refreshed"] == (0 if quote_fails else 1)
    assert bool(result["errors"]) is quote_fails
    mark = price_feed._instrument_price_cache.get(("bitget", symbol, market))
    assert (mark is None) is quote_fails
    if mark:
        assert (mark["price"], mark["exchange"], mark["market_type"]) == (100, "bitget", market)


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch_fails", [False, True])
async def test_backfill_uses_exact_bound_provider_and_keeps_writer(monkeypatch, fetch_fails):
    from extensions.data import ccxt_provider
    from aletheia.data import history_downloader, providers
    from aletheia.data.symbols import InstrumentId
    from aletheia.storage import repositories as repo

    instrument = InstrumentId.from_parts("okx", "BTC/USDT", "perp")
    calls, writes, closed = [], [], []
    monkeypatch.setattr(history_downloader.time, "time", lambda: 1800000000)
    monkeypatch.setattr(repo, "get_ohlcv_newest_ts", lambda *args, **kwargs: None)
    monkeypatch.setattr(repo, "get_ohlcv_coverage", lambda *args, **kwargs: {})
    monkeypatch.setattr(repo, "upsert_ohlcv_candles", lambda *args, **kwargs: writes.append((args, kwargs)))

    class Provider:
        async def get_ohlcv(self, target, timeframe, **kwargs):
            assert target == instrument and timeframe == "1h"
            if fetch_fails:
                raise RuntimeError("history unavailable")
            return [providers.Candle(1799996400000, 1, 2, 0.5, 1.5, 10)]

        async def close(self):
            closed.append(True)

    def factory(exchange, *, exact_venue, fallback_venue):
        calls.append((exchange, exact_venue, fallback_venue))
        return Provider()

    def forbidden(*args, **kwargs):
        raise AssertionError("concrete provider bypass")

    monkeypatch.setattr(providers, "_provider_factory", factory)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", forbidden)
    result = await history_downloader.download_symbol_history("BTC/USDT", "1h", exchange="okx", market_type="perp")
    assert calls == [("okx", True, None)]
    assert closed == [True]
    assert result.bars_fetched == (0 if fetch_fails else 1)
    assert bool(result.error) is fetch_fails
    assert len(writes) == (0 if fetch_fails else 1)
    if writes:
        args, kwargs = writes[0]
        assert args[:2] == ("BTC/USDT", "1h")
        assert kwargs == {"exchange": "okx", "market_type": "perp"}
        assert args[2][0].timestamp == 1799996400000


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [False, True])
@pytest.mark.parametrize("quote_fails", [False, True])
async def test_snapshot_collection_uses_bound_port_and_closes_provider(monkeypatch, token, quote_fails):
    from extensions.data import ccxt_provider
    from aletheia.data import providers
    from aletheia.data.symbols import InstrumentId
    from aletheia.trading import market_data

    instrument = InstrumentId.from_parts("bitget", "AAPLON/USDT" if token else "BTC/USDT", "spot")
    calls, writes, closed = [], [], []
    monkeypatch.setattr(market_data, "load_config", lambda: object())
    monkeypatch.setattr(market_data, "_build_dynamic_instruments", lambda cfg: [] if token else [instrument])
    monkeypatch.setattr(market_data, "stock_tokens_from_config", lambda cfg: [instrument] if token else [])
    monkeypatch.setattr(market_data, "_xstock_failure_counts", {})
    monkeypatch.setattr(market_data, "insert_market_snapshot_batch", lambda rows: writes.extend(rows) or len(rows))

    async def unavailable_listing(*args):
        return None

    monkeypatch.setattr("aletheia.data.venue_listing_cache.get_venue_listing", unavailable_listing)
    monkeypatch.setattr(market_data, "_listed_symbols_on_exchange", unavailable_listing)

    class Provider:
        async def get_ticker(self, target):
            assert target == instrument
            if quote_fails:
                raise RuntimeError("quote unavailable")
            return providers.Ticker(target.symbol, 100, 1000, 0, provider=target.exchange)

        async def close(self):
            closed.append(True)

    def factory(exchange, *, exact_venue, fallback_venue):
        calls.append((exchange, exact_venue, fallback_venue))
        return Provider()

    def forbidden(*args, **kwargs):
        raise AssertionError("concrete acquisition must remain behind configured Data port")

    monkeypatch.setattr(providers, "_provider_factory", factory)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", forbidden)
    monkeypatch.setattr(ccxt_provider, "CCXTProvider", forbidden)
    result = await market_data.collect_market_snapshots()
    assert calls == [("bitget", True, None)]
    assert closed == [True]
    assert bool(result["errors"]) is quote_fails
    assert len(writes) == (0 if quote_fails else 1)
    if writes:
        assert (writes[0].exchange, writes[0].symbol, writes[0].market_type) == (
            instrument.exchange, instrument.symbol, instrument.market_type,
        )


@pytest.mark.parametrize("exact_venue", [False, True])
def test_factory_preserves_policy_and_request_ownership(monkeypatch, exact_venue):
    from extensions.data import ccxt_provider
    from aletheia.data import providers

    first, second = object(), object()
    factory = Mock(side_effect=[first, second])
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", factory)
    monkeypatch.setattr(providers, "_provider_factory", ccxt_provider.create_market_data_provider)
    assert providers.get_market_data_provider("bitget", exact_venue=exact_venue) is first
    assert providers.get_market_data_provider("bitget", exact_venue=exact_venue) is second
    expected = call("bitget", native_ccxt=True, fallback_exchange="bitget") if exact_venue else call("bitget")
    assert factory.call_args_list == [expected, expected]


def test_exact_provider_requires_venue_before_factory(monkeypatch):
    from aletheia.data import providers

    factory = Mock()
    monkeypatch.setattr(providers, "_provider_factory", factory)
    with pytest.raises(ValueError, match="requires a venue"):
        providers.get_market_data_provider(exact_venue=True)
    factory.assert_not_called()


def test_exact_provider_preserves_explicit_fallback_construction(monkeypatch):
    from aletheia.data import providers

    factory = Mock(return_value=object())
    monkeypatch.setattr(providers, "_provider_factory", factory)

    providers.get_market_data_provider(
        "bitget", exact_venue=True, fallback_venue="okx",
    )

    factory.assert_called_once_with(
        "bitget", exact_venue=True, fallback_venue="okx",
    )


def test_core_provider_port_is_absent_safe_without_concrete_imports():
    script = '''
import sys
attempted = []
class MissingAdapter:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "extensions.data.ccxt_provider":
            attempted.append(fullname)
            raise ModuleNotFoundError("isolated missing adapter", name=fullname)
sys.meta_path.insert(0, MissingAdapter())
from aletheia.data.providers import get_market_data_provider
from aletheia.data import price_feed, history_downloader
from aletheia.server.routes import data
from aletheia.trading import market_context
from aletheia.universe import outcome_recorder
from aletheia.universe import registry
from aletheia.portfolio import position_path_observer
for exact in (False, True):
    try:
        get_market_data_provider("bitget", exact_venue=exact)
    except RuntimeError as exc:
        assert str(exc) == "market data provider unavailable"
    else:
        raise AssertionError("unbound factory must be unavailable")
assert not attempted, attempted
import asyncio
from fastapi import Response
from aletheia.data import providers
class FakeProvider:
    closed = False
    async def get_ohlcv(self, instrument, **kwargs):
        assert instrument.exchange == "bitget"
        return [providers.Candle(timestamp=1700000000000, open=1, high=2, low=0.5, close=1.5, volume=10)]
    async def close(self):
        self.closed = True
fake = FakeProvider()
providers.configure_market_data_factory(lambda exchange, **kwargs: fake)
response = Response()
rows = asyncio.run(data.get_market_ohlcv(response, exchange="bitget", limit=99))
assert response.headers["X-Aletheia-Data-Source"] == "provider_fetch"
assert rows[0]["close"] == 1.5 and fake.closed
assert not attempted, attempted
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
