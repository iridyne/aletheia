"""Focused regression tests for orderbook normalization."""

from extensions.exchanges.ccxt.market_data import CCXTProvider
import asyncio
import pytest


@pytest.mark.asyncio
async def test_missing_orderbook_adapter_returns_no_fact_and_never_opens_provider(monkeypatch):
    from aletheia.data.collectors import microstructure

    def forbidden(*args, **kwargs):
        raise AssertionError("missing adapter must not open a provider")

    monkeypatch.setattr(microstructure, "_orderbook_collect", None)
    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", forbidden)
    assert not hasattr(microstructure, "MicrostructureCollector")
    assert await microstructure.collect_orderbooks(["BTC/USDT"], venue="fixture") == {
        "snapshots": [], "errors": ["provider_setup:RuntimeError"],
    }


def test_shared_orderbook_projection_does_not_require_collector(monkeypatch):
    import importlib
    import sys
    from aletheia.data import orderbook

    monkeypatch.setitem(sys.modules, "aletheia.data.collectors.microstructure", None)
    importlib.reload(orderbook)
    snapshot = orderbook.parse_orderbook(
        "BTC/USDT", "bitget", {"bids": [[99.99, 2]], "asks": [[100.01, 3]]},
        latency_ms=0, market_type="perp",
    )
    assert snapshot is not None
    assert snapshot.market_type == "perp"


def test_parse_orderbook_accepts_ccxt_count_column_and_contract_size():
    from aletheia.data.orderbook import parse_orderbook as _parse_orderbook

    snapshot = _parse_orderbook(
        "TEST/USDT",
        "okx",
        {
            "bids": [[99.95, 2.0, 7], [99.80, 4.0, 3]],
            "asks": [[100.05, 3.0, 8], [100.20, 5.0, 2]],
        },
        latency_ms=12,
        contract_size=10.0,
    )

    assert snapshot is not None
    assert snapshot.bid_depth_10bps_usd == pytest.approx(1_999.0)
    assert snapshot.ask_depth_10bps_usd == pytest.approx(3_001.5)
    assert snapshot.total_depth_10bps_usd == pytest.approx(5_000.5)
    assert snapshot.imbalance_ratio == pytest.approx(-0.1429)


@pytest.mark.parametrize("failure", [None, "load_markets", "fetch_order_book", "parse", "market_symbol", "market_type", "response_symbol", "provider_venue"])
def test_futures_instrument_uses_derivatives_orderbook_and_keeps_identity(monkeypatch, failure):
    from extensions.exchanges.ccxt.orderbook import MicrostructureCollector
    from aletheia.data import ccxt_provider
    from aletheia.data.symbols import InstrumentId

    closed = []
    class Exchange:
        options = {}

        async def load_markets(self):
            if failure == "load_markets":
                raise TimeoutError("private provider detail")
            return None

        def market(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            assert self.options["defaultType"] == "swap"
            return {
                "symbol": "ETH/USDT:USDT" if failure == "market_symbol" else symbol,
                "contract": failure != "market_type",
                "spot": failure == "market_type",
                "contractSize": 1.0,
            }

        async def fetch_order_book(self, symbol, limit):
            if failure == "fetch_order_book":
                raise ConnectionError("private provider detail")
            if failure == "parse":
                return {"bids": [], "asks": []}
            assert symbol == "BTC/USDT:USDT"
            assert limit == 20
            return {
                "symbol": "ETH/USDT:USDT" if failure == "response_symbol" else symbol,
                "bids": [[99.9, 1.0]], "asks": [[100.1, 1.0]],
            }

    class Provider:
        _primary_name = "okx" if failure == "provider_venue" else "binance"

        _market_type_context = CCXTProvider._market_type_context

        def _ensure_primary(self, market_type):
            assert market_type == "futures"
            return Exchange()

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", lambda **kwargs: Provider())
    result = asyncio.run(
        MicrostructureCollector().collect(
            [InstrumentId("binance", "BTC/USDT", "futures")]
        )
    )
    assert closed == [True]
    if failure:
        assert result == {"snapshots": [], "errors": [{
            "load_markets": "load_markets:TimeoutError",
            "fetch_order_book": "fetch_order_book:ConnectionError",
            "parse": "parse:invalid_orderbook",
            "market_symbol": "market_metadata:ValueError",
            "market_type": "market_metadata:ValueError",
            "response_symbol": "parse:ValueError",
            "provider_venue": "provider_setup:ValueError",
        }[failure]]}
        return
    assert result["errors"] == []
    assert result["snapshots"][0].market_type == "futures"


@pytest.mark.parametrize(
    "orderbook",
    [
        {"bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[float("nan"), 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, float("inf")]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, -1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[True, 1.0]], "asks": [[1.001, 1.0]]},
        {"bids": [[99.0, True]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, 1.0]], "asks": [[100.0, False]]},
        {"bids": [[99.0, 1.0], [99.5, 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, 1.0]], "asks": [[100.0, 1.0], [99.5, 1.0]]},
    ],
)
def test_parse_orderbook_rejects_impossible_geometry(orderbook):
    from aletheia.data.orderbook import parse_orderbook as _parse_orderbook

    assert _parse_orderbook("BTC/USDT", "binance", orderbook, latency_ms=0) is None


@pytest.mark.parametrize("size", [float("nan"), True, False])
def test_parse_orderbook_rejects_invalid_contract_size(size):
    from aletheia.data.orderbook import contract_size_from_market

    with pytest.raises(ValueError, match="invalid contract size"):
        contract_size_from_market({"contract": True, "contractSize": size})


def test_orderbook_model_rejects_crossed_prices():
    from pydantic import ValidationError

    from aletheia.data.models import OrderbookSnapshot

    with pytest.raises(ValidationError, match="crossed orderbook"):
        OrderbookSnapshot(
            symbol="BTC/USDT",
            exchange="binance",
            market_type="spot",
            bid_price=101.0,
            ask_price=100.0,
            spread_bps=0.0,
            bid_depth_10bps_usd=1.0,
            ask_depth_10bps_usd=1.0,
            total_depth_10bps_usd=2.0,
            observed_at="2026-08-29T00:00:00+00:00",
        )
