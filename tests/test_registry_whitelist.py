"""Regression test for OKX demo whitelist dict-format bug."""

from unittest.mock import patch, AsyncMock

import pytest

from aletheia.universe.registry import _load_demo_whitelist


@pytest.mark.asyncio
async def test_load_demo_whitelist_returns_merged_frozenset():
    """Whitelist must be a frozenset of symbols, not a dict."""
    okx_demo_markets = {
        "swap": frozenset(["BTC/USDT", "ETH/USDT", "SOL/USDT"]),
        "spot": frozenset(["BTC/USDT", "ETH/USDT", "SOL/USDT", "WLD/USDT"]),
    }
    with patch(
        "aletheia.exchanges.okx.client.OKXClient.load_demo_markets",
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
async def test_load_demo_whitelist_non_okx_returns_none():
    assert await _load_demo_whitelist("bitget_demo") is None
    assert await _load_demo_whitelist("binance_testnet") is None
    assert await _load_demo_whitelist("paper_internal") is None


@pytest.mark.asyncio
async def test_load_demo_whitelist_empty_markets():
    with patch(
        "aletheia.exchanges.okx.client.OKXClient.load_demo_markets",
        new_callable=AsyncMock,
        return_value={"swap": frozenset(), "spot": frozenset()},
    ):
        result = await _load_demo_whitelist("okx_demo")
    assert result == frozenset()


def test_dict_vs_frozenset_membership():
    old_dict = {"swap": frozenset(["BTC/USDT"]), "spot": frozenset(["BTC/USDT", "WLD/USDT"])}
    assert ("BTC/USDT" not in old_dict) is True
    fixed = frozenset(["BTC/USDT", "WLD/USDT"])
    assert ("BTC/USDT" not in fixed) is False
    assert ("WLD/USDT" in fixed)
    assert ("UNKNOWN/USDT" not in fixed)
