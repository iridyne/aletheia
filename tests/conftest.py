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
def _fast_network_timeout(request, monkeypatch):
    """Set very short httpx connect timeout so external calls fail fast.

    Does NOT block ASGI TestClient (which uses transport, not real TCP).
    LLM/exchange calls that go to real hosts fail in <50ms instead of 60s.
    Also disables model-discovery network call inside fallback logic.
    """
    if request.node.get_closest_marker("real_network"):
        return
    from aletheia import net as _net
    import httpx as _httpx

    _original_httpx_client = _net.httpx_client

    def _fast_httpx_client(timeout: float = 30, **kwargs):
        fast_timeout = _httpx.Timeout(0.05, connect=0.05)
        return _original_httpx_client(timeout=fast_timeout, **kwargs)

    monkeypatch.setattr(_net, "httpx_client", _fast_httpx_client)

    # Disable list_models() network discovery in fallback to avoid extra round-trips.
    # Tests that provide their own LLM mock (real_llm marker) keep full fallback logic.
    if not request.node.get_closest_marker("real_llm"):
        import aletheia.model_provider.fallback as _fb

        async def _fast_candidates(provider_name, provider):
            primary = str(getattr(provider, "model", "") or "")
            return [primary] if primary else []

        monkeypatch.setattr(_fb, "_candidate_models", _fast_candidates)

    # Eliminate retry delays in startup/resume LLM gate (avoids 15s×2 sleeps).
    # Only skips long sleeps (>2s); tests with explicit short delays (1s) still work.
    import aletheia.agent_runtime.startup_gate as _sg

    _real_asyncio_sleep = _sg.asyncio.sleep

    async def _skip_long_sleep(seconds):
        if seconds > 2:
            return
        await _real_asyncio_sleep(seconds)

    monkeypatch.setattr(_sg.asyncio, "sleep", _skip_long_sleep)


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
        return Ticker(symbol=str(symbol), last_price=50000.0,
                      volume_24h=1_000_000.0, change_pct_24h=1.5)

    async def _noop_close(self):
        pass

    monkeypatch.setattr(CCXTProvider, "get_ohlcv", _empty_ohlcv)
    monkeypatch.setattr(CCXTProvider, "get_ticker", _empty_ticker)
    monkeypatch.setattr(CCXTProvider, "close", _noop_close)
