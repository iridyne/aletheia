"""Regression test for OKX demo whitelist dict-format bug."""

from unittest.mock import patch, AsyncMock

import pytest

from aletheia.universe.registry import _load_demo_whitelist


@pytest.mark.asyncio
async def test_load_demo_whitelist_returns_merged_frozenset():
    """Whitelist must be a frozenset of symbols, not a dict."""
    okx_demo_markets = {
        "usdt_futures": frozenset(["BTC/USDT", "ETH/USDT", "SOL/USDT"]),
        "spot": frozenset(["BTC/USDT", "ETH/USDT", "SOL/USDT", "WLD/USDT"]),
    }
    with patch(
        "aletheia.execution.venue_capabilities.fetch_venue_capabilities",
        new_callable=AsyncMock,
        return_value=okx_demo_markets,
    ):
        result = await _load_demo_whitelist("okx_demo")
    assert isinstance(result, frozenset), (
        "Expected frozenset, got " + type(result).__name__
    )
    assert "BTC/USDT" in result
    assert "WLD/USDT" in result
    assert len(result) == 4


@pytest.mark.asyncio
async def test_load_demo_whitelist_non_sandbox_returns_none():
    """A mode with no restricted sandbox listing needs no extra filter.

    ``bitget_demo`` and ``binance_testnet`` have sandbox=True profiles and DO
    return a capability snapshot — see
    test_load_demo_whitelist_bitget_demo_uses_sandbox_capabilities.
    ``paper_internal`` is not a registered execution mode at all.
    """
    assert await _load_demo_whitelist("paper_internal") is None


@pytest.mark.asyncio
async def test_load_demo_whitelist_bitget_demo_uses_sandbox_capabilities():
    """bitget_demo has a restricted listing and must be filtered like okx_demo.

    #3465: volume-ranking injected symbols the demo profile does not list.
    """
    fake = {
        "usdt_futures": frozenset(["BTC/USDT", "ETH/USDT"]),
        "spot": frozenset(["BTC/USDT", "ETH/USDT", "ONDO/USDT"]),
    }
    with patch(
        "aletheia.execution.venue_capabilities.fetch_venue_capabilities",
        new_callable=AsyncMock,
        return_value=fake,
    ):
        result = await _load_demo_whitelist("bitget_demo")
    assert result == frozenset(["BTC/USDT", "ETH/USDT", "ONDO/USDT"])


@pytest.mark.asyncio
async def test_load_demo_whitelist_empty_markets():
    with patch(
        "aletheia.execution.venue_capabilities.fetch_venue_capabilities",
        new_callable=AsyncMock,
        return_value={"usdt_futures": frozenset(), "spot": frozenset()},
    ):
        result = await _load_demo_whitelist("okx_demo")
    assert result == frozenset()


@pytest.mark.asyncio
async def test_load_demo_whitelist_unavailable_stays_unknown():
    with patch(
        "aletheia.execution.venue_capabilities.fetch_venue_capabilities",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await _load_demo_whitelist("okx_demo") is None


def test_dict_vs_frozenset_membership():
    old_dict = {"swap": frozenset(["BTC/USDT"]), "spot": frozenset(["BTC/USDT", "WLD/USDT"])}
    assert ("BTC/USDT" not in old_dict) is True
    fixed = frozenset(["BTC/USDT", "WLD/USDT"])
    assert ("BTC/USDT" not in fixed) is False
    assert ("WLD/USDT" in fixed)
    assert ("UNKNOWN/USDT" not in fixed)
