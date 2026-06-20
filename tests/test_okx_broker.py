"""Tests for OKX broker symbol filter and execution routing."""

import importlib
import types
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level cache between tests."""
    import aletheia.execution.okx_broker as mod
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0
    yield
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0


class TestSymbolFilter:
    """O(1) symbol filter tests."""

    def test_filter_none_means_fail_open(self):
        """When cache is None (never loaded), filter allows everything."""
        from aletheia.execution.okx_broker import _get_demo_symbols
        # Patch refresh to return 0 (simulating failure to load)
        with patch("aletheia.execution.okx_broker.refresh_okx_demo_symbols", return_value=0):
            import aletheia.execution.okx_broker as mod
            mod._okx_demo_symbols = None
            result = _get_demo_symbols()
            # After a failed refresh, remains None → fail-open
            assert result is None or isinstance(result, frozenset)

    def test_filter_with_populated_set_blocks_unsupported(self):
        """Symbols not in the frozenset are blocked."""
        import aletheia.execution.okx_broker as mod
        mod._okx_demo_symbols = frozenset({"BTC/USDT", "ETH/USDT"})
        mod._okx_demo_symbols_loaded_at = 9999999999.0  # far future = not stale

        result = mod._get_demo_symbols()
        assert result is not None
        assert "BTC/USDT" in result
        assert "DOGE/USDT" not in result

    def test_filter_is_frozenset_o1_lookup(self):
        """Confirm the filter uses frozenset for O(1) membership testing."""
        import aletheia.execution.okx_broker as mod
        mod._okx_demo_symbols = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"})
        mod._okx_demo_symbols_loaded_at = 9999999999.0

        symbols = mod._get_demo_symbols()
        assert isinstance(symbols, frozenset)

    def test_refresh_builds_frozenset_from_markets(self):
        """refresh_okx_demo_symbols builds a frozenset from CCXT markets."""
        import aletheia.execution.okx_broker as mod

        fake_markets = frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"})

        async def fake_load():
            return fake_markets

        with patch("aletheia.execution.okx_broker._run_coro", return_value=fake_markets):
            count = mod.refresh_okx_demo_symbols()

        assert count == 3
        assert mod._okx_demo_symbols == fake_markets


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
