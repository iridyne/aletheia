"""Tests for OKX broker symbol filter and execution routing."""

import importlib
import types
from unittest.mock import patch, MagicMock

import pytest

from tests.helpers.async_test_utils import closing_coro_runner


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level cache between tests."""
    import aletheia.execution.venue_capabilities as mod
    mod._demo_capabilities_cache.clear()
    yield
    mod._demo_capabilities_cache.clear()


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
        client = self._client()
        await client.close()
        assert client._exchange is None, "close on a never-used client must stay a no-op"


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
                mod.load_venue_capabilities("okx_demo")

        assert mod._capability_cache_key("okx_demo") not in mod._demo_capabilities_cache

    def test_filter_with_populated_set_blocks_unsupported(self):
        """Symbols not in usdt_futures frozenset are blocked for perp orders."""
        import time
        import aletheia.execution.venue_capabilities as mod

        mod._demo_capabilities_cache[mod._capability_cache_key("okx_demo")] = (
            {
                "usdt_futures": frozenset({"BTC/USDT", "ETH/USDT"}),
                "spot": frozenset({"BTC/USDT", "ETH/USDT", "DOGE/USDT"}),
            },
            time.time(),
        )
        futures_result = mod.get_supported_symbols("okx_demo", "usdt_futures")
        assert futures_result is not None
        assert "BTC/USDT" in futures_result
        assert "DOGE/USDT" not in futures_result  # DOGE is spot-only
        spot_result = mod.get_supported_symbols("okx_demo", "spot")
        assert "DOGE/USDT" in spot_result

    def test_filter_is_frozenset_o1_lookup(self):
        """Confirm the filter returns a frozenset for O(1) membership testing."""
        import time
        import aletheia.execution.venue_capabilities as mod

        mod._demo_capabilities_cache[mod._capability_cache_key("okx_demo")] = (
            {"usdt_futures": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}), "spot": frozenset({"BTC/USDT"})},
            time.time(),
        )
        assert isinstance(mod.get_supported_symbols("okx_demo", "usdt_futures"), frozenset)

    def test_refresh_builds_dict_of_frozensets_from_markets(self):
        """refresh_okx_demo_symbols builds {"usdt_futures": frozenset, "spot": frozenset}."""
        import aletheia.execution.venue_capabilities as mod

        fake_result = {
            "usdt_futures": frozenset({"BTC/USDT", "ETH/USDT", "SOL/USDT"}),
            "spot": frozenset({"BTC/USDT", "ETH/USDT"}),
        }
        with patch(
            "aletheia.execution.venue_capabilities.run_coro_from_sync",
            side_effect=closing_coro_runner(fake_result),
        ):
            count = mod.refresh_okx_demo_symbols()

        assert count == 5  # 3 usdt_futures + 2 spot
        assert mod._demo_capabilities_cache[mod._capability_cache_key("okx_demo")][0] == fake_result

    def test_capability_cache_identity_is_stable_and_profile_bound(self):
        """#3475: the cache key carries the resolved profile class and must be
        *stable* across calls; a key that moved per call would silently disable
        caching instead."""
        import aletheia.execution.venue_capabilities as mod

        key = mod._capability_cache_key("bitget_demo")

        assert key == mod._capability_cache_key("bitget_demo")
        assert key.startswith("bitget_demo|"), key
        assert "BitgetUTAV3Profile" in key, key
        assert key != mod._capability_cache_key("okx_demo")

    def test_contended_refresh_reuses_newly_fresh_cache(self):
        import aletheia.execution.venue_capabilities as mod

        fake_result = {"usdt_futures": frozenset({"BTC/USDT"}), "spot": frozenset({"BTC/USDT"})}
        runner = MagicMock(side_effect=closing_coro_runner(
            fake_result, RuntimeError("redundant refresh failed"),
        ))
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

        assert mod.get_supported_symbols("okx_demo", "usdt_futures") == frozenset()
        assert mod.get_supported_symbols("okx_demo", "spot") == frozenset()

    def test_scheduler_records_refresh_failure(self, monkeypatch):
        from aletheia.agent_runtime import scheduler

        failures = []
        monkeypatch.setattr(
            scheduler, "_record_job_failure",
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
