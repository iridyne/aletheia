"""Features receives normalized orderbooks; Data owns native acquisition."""

import ast
from pathlib import Path

import pytest


def test_features_do_not_call_orderbook_endpoint_or_collector_parser():
    source = (Path(__file__).resolve().parents[1] / "aletheia/universe/features.py").read_text()
    tree = ast.parse(source)
    assert "data.collectors.microstructure" not in source
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "fetch_order_book"
        for node in ast.walk(tree)
    )


def test_planner_uses_normalized_provider_not_optional_collector():
    source = (Path(__file__).resolve().parents[1] / "aletheia/agent_shell/entry_planner.py").read_text()
    tree = ast.parse(source)
    assert "data.collectors.microstructure" not in source
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"fetch_order_book", "_ensure_primary"}
        for node in ast.walk(tree)
    )


@pytest.mark.asyncio
async def test_provider_returns_exact_normalized_orderbook_without_fallback(monkeypatch):
    from extensions.data.ccxt_provider import CCXTProvider

    calls = []

    class Exchange:
        id = "okx"
        options = {}
        markets = {
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "contract": True,
                "spot": False,
                "contractSize": 0.01,
            }
        }

        async def load_markets(self):
            calls.append("load_markets")
            return self.markets

        def market(self, symbol):
            calls.append(("market", symbol, self.options.get("defaultType")))
            return self.markets[symbol]

        async def fetch_order_book(self, symbol, limit):
            calls.append(("book", symbol, limit))
            return {
                "symbol": symbol,
                "bids": [[99.99, 100]],
                "asks": [[100.01, 100]],
            }

    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", lambda market_type: Exchange())
    monkeypatch.setattr(
        provider,
        "_ensure_fallback",
        lambda *args: pytest.fail("orderbook observation must not use fallback"),
    )

    row = await provider.get_orderbook_observation("BTC/USDT", market_type="perp")

    assert calls == [
        "load_markets",
        ("market", "BTC/USDT:USDT", "swap"),
        ("book", "BTC/USDT:USDT", 20),
    ]
    assert row["symbol"] == "BTC/USDT"
    assert row["exchange"] == "okx"
    assert row["market_type"] == "perp"
    assert row["total_depth_10bps_usd"] == pytest.approx(200.0)
    assert "bids" not in row and "asks" not in row


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["provider_setup", "load_markets", "market_metadata", "fetch_order_book"])
async def test_orderbook_failure_preserves_safe_acquisition_phase(monkeypatch, phase):
    from extensions.data.ccxt_provider import CCXTProvider
    from aletheia.data.providers import MarketDataObservationError

    def fail_at(current):
        if phase == current:
            raise ConnectionError("private provider response")

    class Exchange:
        options = {}
        async def load_markets(self):
            fail_at("load_markets")
        def market(self, symbol):
            fail_at("market_metadata")
            return {"symbol": symbol, "contract": True, "contractSize": 1}
        async def fetch_order_book(self, symbol, limit):
            fail_at("fetch_order_book")

    def primary(market_type):
        fail_at("provider_setup")
        return Exchange()

    provider = CCXTProvider("okx", "bitget")
    monkeypatch.setattr(provider, "_ensure_primary", primary)
    with pytest.raises(MarketDataObservationError) as caught:
        await provider.get_orderbook_observation("BTC/USDT", market_type="perp")
    assert caught.value.phase == phase
    assert caught.value.exception_type == "ConnectionError"
    assert "private provider response" not in str(caught.value)
