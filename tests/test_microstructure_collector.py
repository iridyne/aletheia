"""Focused regression tests for orderbook normalization."""

import asyncio
import pytest


def test_parse_orderbook_accepts_ccxt_count_column_and_contract_size():
    from aletheia.data.collectors.microstructure import _parse_orderbook

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


def test_futures_instrument_uses_derivatives_orderbook_and_keeps_identity(monkeypatch):
    from aletheia.data.collectors.microstructure import MicrostructureCollector
    from aletheia.data import ccxt_provider
    from aletheia.data.symbols import InstrumentId

    class Exchange:
        options = {}

        async def load_markets(self):
            return None

        def market(self, symbol):
            assert symbol == "BTC/USDT:USDT"
            assert self.options["defaultType"] == "swap"
            return {"contractSize": 1.0}

        async def fetch_order_book(self, symbol, limit):
            assert symbol == "BTC/USDT:USDT"
            assert limit == 20
            return {"bids": [[99.9, 1.0]], "asks": [[100.1, 1.0]]}

    class Provider:
        _primary_name = "binance"

        def _ensure_primary(self):
            return Exchange()

        async def close(self):
            return None

    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", lambda: Provider())
    result = asyncio.run(
        MicrostructureCollector().collect(
            [InstrumentId("binance", "BTC/USDT", "futures")]
        )
    )
    assert result["errors"] == []
    assert result["snapshots"][0].market_type == "futures"


@pytest.mark.parametrize(
    "orderbook",
    [
        {"bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[float("nan"), 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, float("inf")]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, -1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, 1.0], [99.5, 1.0]], "asks": [[100.0, 1.0]]},
        {"bids": [[99.0, 1.0]], "asks": [[100.0, 1.0], [99.5, 1.0]]},
    ],
)
def test_parse_orderbook_rejects_impossible_geometry(orderbook):
    from aletheia.data.collectors.microstructure import _parse_orderbook

    assert _parse_orderbook("BTC/USDT", "binance", orderbook, latency_ms=0) is None


def test_parse_orderbook_rejects_invalid_contract_size():
    from aletheia.data.collectors.microstructure import _contract_size

    with pytest.raises(ValueError, match="invalid contract size"):
        _contract_size({"contract": True, "contractSize": float("nan")})


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
