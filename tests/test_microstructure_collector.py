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
