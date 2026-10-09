"""Features receives normalized orderbooks; Data owns native acquisition."""

import ast
from pathlib import Path

import pytest


def test_features_do_not_call_orderbook_endpoint_or_collector_parser():
    source = (Path(__file__).resolve().parents[1] / "core/aletheia/universe/features.py").read_text()
    tree = ast.parse(source)
    assert "data.collectors.microstructure" not in source
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "fetch_order_book"
        for node in ast.walk(tree)
    )


def test_planner_uses_normalized_provider_not_optional_collector():
    source = (Path(__file__).resolve().parents[1] / "core/aletheia/agent_shell/entry_planner.py").read_text()
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


_OKX_SWAP = {
    "id": "BTC-USDT-SWAP", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT",
    "settle": "USDT", "baseId": "BTC", "quoteId": "USDT", "settleId": "USDT",
    "type": "swap", "spot": False, "margin": False, "swap": True, "future": False,
    "option": False, "active": True, "contract": True, "linear": True, "inverse": False,
    "contractSize": 0.01, "precision": {"amount": 0.01, "price": 0.1},
    "limits": {"amount": {"min": 0.01}}, "info": {"instId": "BTC-USDT-SWAP"},
}


@pytest.fixture
def okx_metadata(monkeypatch):
    """Real ccxt okx clients whose two cold-load REST reads are counted, not sent."""
    import ccxt.async_support as ccxt_async

    loads = []

    async def fetch_markets(self, params={}):  # noqa: B006 — ccxt signature
        loads.append(bool(self.isSandboxModeEnabled))
        return [dict(_OKX_SWAP)]

    async def fetch_currencies(self, params={}):  # noqa: B006 — ccxt signature
        return {}

    async def fetch_order_book(self, symbol, limit=None, params={}):  # noqa: B006
        return {"symbol": symbol, "bids": [[99.99, 100]], "asks": [[100.01, 100]]}

    monkeypatch.setattr(ccxt_async.okx, "fetch_markets", fetch_markets)
    monkeypatch.setattr(ccxt_async.okx, "fetch_currencies", fetch_currencies)
    monkeypatch.setattr(ccxt_async.okx, "fetch_order_book", fetch_order_book)
    return loads


async def _observe_okx_perp():
    from extensions.data.ccxt_provider import CCXTProvider

    provider = CCXTProvider("okx", "okx")
    try:
        row = await provider.get_orderbook_observation("BTC/USDT", market_type="perp")
        return row, provider._ensure_primary("perp")._candidate_markets_snapshot[1]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_fresh_provider_reuses_markets_snapshot_and_its_acquisition_time(okx_metadata):
    # Each planner acquisition builds a fresh provider; the cold load must not repeat.
    first_row, first_acquired_at = await _observe_okx_perp()
    second_row, second_acquired_at = await _observe_okx_perp()

    assert okx_metadata == [False]
    assert first_row and second_row
    assert first_row["symbol"] == second_row["symbol"] == "BTC/USDT"
    assert second_acquired_at == first_acquired_at  # restore never re-stamps (#3786)


@pytest.mark.asyncio
async def test_sandbox_client_never_reads_live_cached_markets(okx_metadata):
    from aletheia.net import close_ccxt, make_ccxt
    from extensions.data.ccxt_provider import _install_market_id_guard, _load_markets_cached

    await _observe_okx_perp()
    exchange, session = make_ccxt("okx")
    _install_market_id_guard(exchange, "okx")
    exchange.set_sandbox_mode(True)
    exchange.options["defaultType"] = "swap"  # same options as the live perp read
    try:
        await _load_markets_cached(exchange)
    finally:
        await close_ccxt(exchange, session)

    assert okx_metadata == [False, True]


@pytest.mark.asyncio
async def test_order_rules_always_read_the_venue_not_the_markets_cache(okx_metadata, monkeypatch):
    from extensions.exchanges.ccxt import order_rules

    await _observe_okx_perp()
    monkeypatch.setattr(order_rules, "_listing_view", lambda mode: (False, {}, ""))
    constraints = await order_rules.read_order_constraints(
        "okx_live", "okx", "BTC/USDT", "usdt_futures",
    )

    assert constraints is not None
    assert okx_metadata == [False, False]
