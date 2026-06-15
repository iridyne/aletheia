"""Shared test fixtures + safety guards.

`bitget_demo` (now the default `paper_execution_mode`) places REAL orders on Bitget's
demo API. Secrets are read from the OS keyring first, which the per-test `tmp_path`
DATA_DIR monkeypatch does NOT sandbox — so without a guard an unmocked test that runs
paper_trader to a trade could fire a real demo order over the network during pytest.

This autouse guard makes "tests never touch the real exchange" structural: it replaces
`BitgetClient.place_order` with a loud error by default. Tests that exercise the
exchange path provide their own `monkeypatch.setattr(...place_order, fake)`, which
overrides this within the test body. A test that reaches the exchange without a mock
fails loudly instead of silently placing a real order.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_place_order: test exercises BitgetClient.place_order directly with httpx "
        "mocked (no network) — opt out of the place_order guard.",
    )
    config.addinivalue_line(
        "markers",
        "real_llm: test provides its own LLM mock — opt out of the fast-fail guard.",
    )
    config.addinivalue_line(
        "markers",
        "real_network: test needs real network calls — opt out of the market-data guard.",
    )


@pytest.fixture(autouse=True)
def _block_real_exchange_orders(request, monkeypatch):
    # Tests that verify place_order's own body-building mock httpx instead, so they
    # opt out of this guard via @pytest.mark.real_place_order.
    if request.node.get_closest_marker("real_place_order"):
        return
    from aletheia.exchanges.bitget import client as bg

    async def _blocked(self, order):  # noqa: ARG001
        raise RuntimeError(
            "BitgetClient.place_order reached the real exchange in a test. "
            "Mock it with monkeypatch.setattr(BitgetClient, 'place_order', fake) "
            "or route the proposal through paper_internal."
        )

    monkeypatch.setattr(bg.BitgetClient, "place_order", _blocked)


@pytest.fixture(autouse=True)
def _fast_llm_failure(request, monkeypatch):
    """Make LLM calls fail instantly instead of waiting for TCP connect timeout.

    Without this, tests that call run_agent() block ~13s per symbol waiting for
    httpx to timeout connecting to localhost:8000 (no listener). This fixture
    makes the entire test suite ~40s faster.

    Tests that provide their own LLM mock can opt out via @pytest.mark.real_llm.
    """
    if request.node.get_closest_marker("real_llm"):
        return
    import aletheia.model_provider.fallback as fb

    async def _instant_fail(req, *, provider_name=None, provider=None):
        raise ConnectionRefusedError("LLM not available in test environment")

    monkeypatch.setattr(fb, "complete_with_fallback", _instant_fail)


@pytest.fixture(autouse=True)
def _no_network_market_data(request, monkeypatch):
    """Prevent CCXT/exchange network calls from blocking tests.

    Returns empty data instantly instead of waiting for TCP timeouts to
    remote exchanges. Tests that need real market data responses should
    mock the provider themselves at a higher level.
    """
    if request.node.get_closest_marker("real_network"):
        return
    from aletheia.data.ccxt_provider import CCXTProvider

    async def _empty_ohlcv(self, symbol, *, timeframe="1h", limit=100):
        return []

    async def _empty_ticker(self, symbol):
        from aletheia.data.providers import Ticker
        return Ticker(symbol=str(symbol), last_price=0.0,
                      volume_24h=0.0, change_pct_24h=0.0)

    monkeypatch.setattr(CCXTProvider, "get_ohlcv", _empty_ohlcv)
    monkeypatch.setattr(CCXTProvider, "get_ticker", _empty_ticker)
