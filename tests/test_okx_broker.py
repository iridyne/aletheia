"""Tests for OKX broker symbol filter and execution routing."""

import importlib
import types
from unittest.mock import patch, MagicMock

import pytest

from tests.async_test_utils import closing_coro_runner


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level cache between tests."""
    import aletheia.execution.venue_capabilities as mod
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0
    yield
    mod._okx_demo_symbols = None
    mod._okx_demo_symbols_loaded_at = 0.0


class TestLazyExchangeInit:
    """The current UTA client is lazy, loop-owned and closed deterministically."""

    def _client(self):
        from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
        from tests.uta.test_ccxt_adapter import _identity, _profile
        profile = _profile("okx")
        return CCXTUTAAdapter(_identity(profile), profile)

    def test_init_does_not_build_exchange(self):
        client = self._client()
        assert client._exchange is None
        assert client._session is None

    def test_init_keeps_selected_account_identity(self):
        client = self._client()
        assert client.identity.venue == "okx"
        assert client.identity.environment == "demo"

    @pytest.mark.asyncio
    async def test_connect_is_idempotent(self, monkeypatch):
        from tests.uta.test_ccxt_adapter import FakeExchange, _install
        exchange = FakeExchange()
        session, calls = _install(monkeypatch, exchange)
        client = self._client()
        try:
            await client.connect()
            await client.connect()
            assert len(calls) == 1
            assert client._exchange is exchange
        finally:
            await client.close()
        assert exchange.closed and session.closed

    @pytest.mark.asyncio
    async def test_close_is_noop_when_never_used(self):
        await self._client().close()


class TestSymbolFilter:
    """O(1) symbol filter tests — market-type-aware."""

    def test_initial_refresh_failure_fails_closed(self):
        """An unavailable demo whitelist must stop pre-order validation."""
        import aletheia.execution.venue_capabilities as mod

        with patch(
            "aletheia.execution.venue_capabilities.run_coro_from_sync",
            side_effect=closing_coro_runner(RuntimeError("market load unavailable")),
        ):
            with pytest.raises(RuntimeError, match="market load unavailable"):
                mod._get_demo_symbols("usdt_futures")

        assert mod._okx_demo_symbols is None
        assert mod._okx_demo_symbols_loaded_at == 0.0

    def test_filter_with_populated_set_blocks_unsupported(self):
        """Symbols not in swap frozenset are blocked for perp orders."""
        import aletheia.execution.venue_capabilities as mod
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
        import aletheia.execution.venue_capabilities as mod
        mod._okx_demo_symbols = {
            "swap": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}),
            "spot": frozenset({"BTC/USDT"}),
        }
        mod._okx_demo_symbols_loaded_at = 9999999999.0

        symbols = mod._get_demo_symbols("usdt_futures")
        assert isinstance(symbols, frozenset)

    def test_refresh_builds_dict_of_frozensets_from_markets(self):
        """refresh_okx_demo_symbols builds {"swap": frozenset, "spot": frozenset}."""
        import aletheia.execution.venue_capabilities as mod

        fake_result = {
            "swap": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}),
            "spot": frozenset({"BTC/USDT", "ETH/USDT"}),
        }

        with patch(
            "aletheia.execution.venue_capabilities.run_coro_from_sync",
            side_effect=closing_coro_runner(fake_result),
        ):
            count = mod.refresh_okx_demo_symbols()

        assert count == 5  # 3 swap + 2 spot
        assert mod._okx_demo_symbols == fake_result

    def test_contended_refresh_reuses_newly_fresh_cache(self):
        import aletheia.execution.venue_capabilities as mod

        fake_result = {
            "swap": frozenset({"BTC/USDT"}),
            "spot": frozenset({"BTC/USDT"}),
        }
        runner = MagicMock(
            side_effect=closing_coro_runner(
                fake_result,
                RuntimeError("redundant refresh failed"),
            )
        )

        with patch("aletheia.execution.venue_capabilities.run_coro_from_sync", runner):
            first = mod.refresh_okx_demo_symbols()
            second = mod.refresh_okx_demo_symbols()

        assert first == second == 2
        assert runner.call_count == 1

    def test_empty_refresh_is_known_loaded_absence(self):
        import aletheia.execution.venue_capabilities as mod

        with patch(
            "aletheia.execution.venue_capabilities.run_coro_from_sync",
            side_effect=closing_coro_runner({}),
        ):
            assert mod.refresh_okx_demo_symbols() == 0

        assert mod._get_demo_symbols("usdt_futures") == frozenset()
        assert mod._get_demo_symbols("spot") == frozenset()

    def test_scheduler_records_refresh_failure(self, monkeypatch):
        from aletheia.agent_runtime import scheduler

        failures = []
        monkeypatch.setattr(
            scheduler,
            "_record_job_failure",
            lambda job_id, error: failures.append((job_id, error)),
        )
        with patch(
            "aletheia.execution.venue_capabilities.run_coro_from_sync",
            side_effect=closing_coro_runner(RuntimeError("market load unavailable")),
        ):
            scheduler._run_okx_demo_symbols()

        assert failures == [("okx_demo_symbols", "market load unavailable")]




class TestRiskEngineOkx:
    """Verify risk engine recognizes okx modes."""

    def test_is_live_includes_okx(self):
        from aletheia.risk.engine import _is_live
        assert _is_live("okx_live_limited") is True
        assert _is_live("okx_demo") is False
        assert _is_live("bitget_live_limited") is True
