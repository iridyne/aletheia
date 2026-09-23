"""Tests for OKX broker execution routing."""

import importlib
import types

import pytest


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


class TestRiskEngineOkx:
    """Verify risk engine recognizes okx modes."""

    def test_is_live_includes_okx(self):
        from aletheia.risk.engine import _is_live
        assert _is_live("okx_live_limited") is True
        assert _is_live("okx_demo") is False
        assert _is_live("bitget_live_limited") is True
