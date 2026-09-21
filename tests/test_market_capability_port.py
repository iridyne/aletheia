"""Public sandbox capabilities use the existing exact-provider boundary."""

import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def no_concrete_data_import(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "extensions.data.ccxt_provider":
            raise AssertionError("Core imported concrete Data adapter")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("symbols", [frozenset(), frozenset({"BTC/USDT"})])
@pytest.mark.parametrize("market_type", ["spot", "usdt_futures"])
def test_gateway_uses_exact_provider_and_preserves_known_empty(
    monkeypatch, no_concrete_data_import, symbols, market_type,
):
    from aletheia.data import providers
    from aletheia.execution import gateway, venue_capabilities

    read = AsyncMock(return_value={"spot": symbols, "swap": symbols})
    provider = SimpleNamespace(get_public_market_capabilities=read, close=AsyncMock())
    calls = []

    def factory(exchange, **kwargs):
        calls.append((exchange, kwargs))
        return provider

    monkeypatch.setattr(providers, "_provider_factory", factory)
    monkeypatch.setattr(venue_capabilities, "_okx_demo_symbols", None)
    result = gateway._check_symbol_availability("BTC/USDT", "okx_demo", market_type)
    assert (result is None) is bool(symbols)
    assert calls == [("okx", {"exact_venue": True, "fallback_venue": None})]
    read.assert_awaited_once_with(sandbox=True)
    provider.close.assert_awaited_once()


@pytest.mark.parametrize("unsupported", [False, True])
async def test_failed_or_missing_capability_closes_provider(
    monkeypatch, no_concrete_data_import, unsupported,
):
    from aletheia.data import providers
    from aletheia.execution.venue_capabilities import fetch_venue_capabilities

    provider = SimpleNamespace(close=AsyncMock())
    if not unsupported:
        provider.get_public_market_capabilities = AsyncMock(side_effect=RuntimeError("read failed"))
    monkeypatch.setattr(providers, "_provider_factory", lambda *args, **kwargs: provider)
    with pytest.raises(RuntimeError, match="market capabilities unavailable" if unsupported else "read failed"):
        await fetch_venue_capabilities("okx_demo")
    provider.close.assert_awaited_once()


async def test_unbound_data_is_unavailable_not_empty(monkeypatch, no_concrete_data_import):
    from aletheia.data import providers
    from aletheia.execution.venue_capabilities import fetch_venue_capabilities

    monkeypatch.setattr(providers, "_provider_factory", None)
    with pytest.raises(RuntimeError, match="market data provider unavailable"):
        await fetch_venue_capabilities("okx_demo")


@pytest.mark.parametrize("result", [
    None, [], {"swap": None}, {"swap": {"BTC/USDT"}},
    {"future": frozenset()}, {"spot": frozenset({""})},
    {"spot": frozenset({1})},
])
async def test_invalid_capability_facts_fail_closed(monkeypatch, result):
    from aletheia.data import providers
    from aletheia.execution.venue_capabilities import fetch_venue_capabilities

    provider = SimpleNamespace(
        get_public_market_capabilities=AsyncMock(return_value=result), close=AsyncMock(),
    )
    monkeypatch.setattr(providers, "_provider_factory", lambda *args, **kwargs: provider)
    with pytest.raises(ValueError, match="market capabilities invalid"):
        await fetch_venue_capabilities("okx_demo")
    provider.close.assert_awaited_once()
