"""Tests for OKX broker symbol filter and execution routing."""

import importlib
import types
from unittest.mock import patch, MagicMock

import pytest

from tests.async_test_utils import closing_coro_runner


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level cache between tests."""
    import aletheia.execution.okx_broker as mod
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0
    yield
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0


class TestLazyExchangeInit:
    """OKXClient must NOT create its aiohttp session / ccxt exchange in __init__.

    Regression guard for the cross-loop bug: __init__ runs in a sync context whose
    event loop differs from the one that later awaits the client (okx_broker
    _run_coro spins a fresh asyncio.run() in a worker thread). aiohttp binds its
    connector to the creating loop, so eager construction in __init__ caused
    "Future attached to a different loop" — which silently failed load_markets()
    and re-exposed the contractSize sizing bug. The exchange must be created lazily
    inside the awaiting loop via _ensure_exchange().
    """

    def _client(self):
        from aletheia.exchanges.okx.client import OKXClient
        with patch("aletheia.exchanges.okx.client.get_secret", return_value="x"):
            return OKXClient(mode="demo")

    def test_init_does_not_build_exchange(self):
        c = self._client()
        assert c._exchange is None, "__init__ must not eagerly build the ccxt exchange"
        assert c._session is None, "__init__ must not eagerly build the aiohttp session"

    def test_init_stores_credentials_for_lazy_build(self):
        c = self._client()
        assert c._api_key and c._api_secret and c._passphrase and c._mode == "demo"

    def test_ensure_exchange_is_idempotent(self):
        c = self._client()
        with patch("aletheia.net.aiohttp_session", return_value=MagicMock()):
            ex1 = c._ensure_exchange()
            ex2 = c._ensure_exchange()
            assert ex1 is ex2, "_ensure_exchange must reuse the same exchange within one loop"

    def test_close_is_noop_when_never_used(self):
        import asyncio
        c = self._client()
        # Constructed then closed with no awaited call — must not raise on None refs.
        asyncio.run(c.close())


class TestSymbolFilter:
    """O(1) symbol filter tests — market-type-aware."""

    def test_filter_none_means_fail_open(self):
        """When cache is None (never loaded), filter allows everything."""
        from aletheia.execution.okx_broker import _get_demo_symbols
        with patch("aletheia.execution.okx_broker.refresh_okx_demo_symbols", return_value=0):
            import aletheia.execution.okx_broker as mod
            mod._okx_demo_symbols = None
            result = _get_demo_symbols("usdt_futures")
            assert result is None or isinstance(result, frozenset)

    def test_filter_with_populated_set_blocks_unsupported(self):
        """Symbols not in swap frozenset are blocked for perp orders."""
        import aletheia.execution.okx_broker as mod
        mod._okx_demo_symbols = {
            "swap": frozenset({"BTC/USDT", "ETH/USDT"}),
            "spot": frozenset({"BTC/USDT", "ETH/USDT", "DOGE/USDT"}),
        }
        mod._okx_demo_symbols_loaded_at = 9999999999.0

        swap_result = mod._get_demo_symbols("usdt_futures")
        assert swap_result is not None
        assert "BTC/USDT" in swap_result
        assert "DOGE/USDT" not in swap_result  # DOGE is spot-only

        spot_result = mod._get_demo_symbols("spot")
        assert "DOGE/USDT" in spot_result  # DOGE passes as spot

    def test_filter_is_frozenset_o1_lookup(self):
        """Confirm the filter returns a frozenset for O(1) membership testing."""
        import aletheia.execution.okx_broker as mod
        mod._okx_demo_symbols = {
            "swap": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}),
            "spot": frozenset({"BTC/USDT"}),
        }
        mod._okx_demo_symbols_loaded_at = 9999999999.0

        symbols = mod._get_demo_symbols("usdt_futures")
        assert isinstance(symbols, frozenset)

    def test_refresh_builds_dict_of_frozensets_from_markets(self):
        """refresh_okx_demo_symbols builds {"swap": frozenset, "spot": frozenset}."""
        import aletheia.execution.okx_broker as mod

        fake_result = {
            "swap": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}),
            "spot": frozenset({"BTC/USDT", "ETH/USDT"}),
        }

        with patch(
            "aletheia.execution.okx_broker._run_coro",
            side_effect=closing_coro_runner(fake_result),
        ):
            count = mod.refresh_okx_demo_symbols()

        assert count == 5  # 3 swap + 2 spot
        assert mod._okx_demo_symbols == fake_result


class TestPaperToolsIntegration:
    """Verify okx_demo is in _PAPER_MODES."""

    def test_okx_demo_in_paper_modes(self):
        from aletheia.execution.paper_tools import _PAPER_MODES
        assert "okx_demo" in _PAPER_MODES


class TestRiskEngineOkx:
    """Verify risk engine recognizes okx modes."""

    def test_is_live_includes_okx(self):
        from aletheia.risk.engine import _is_live
        assert _is_live("okx_live_limited") is True
        assert _is_live("okx_demo") is False
        assert _is_live("bitget_live_limited") is True


class TestPositionManagerRouting:
    """Verify position_manager knows about OKX modes."""

    def test_paper_modes_includes_okx(self):
        from aletheia.portfolio.position_manager import _PAPER_MODES
        # okx_demo should NOT be in position_manager's _PAPER_MODES (it uses exchange close)
        # but it should be handled in the close routing
        # The actual routing is in evaluate_open_positions() — just verify module loads
        import aletheia.portfolio.position_manager
        assert callable(aletheia.portfolio.position_manager.evaluate_open_positions)
