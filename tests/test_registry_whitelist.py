"""Regression test for OKX demo whitelist dict-format bug."""

from unittest.mock import patch, AsyncMock

import pytest

from aletheia.universe.registry import _load_demo_whitelist


@pytest.mark.asyncio
async def test_missing_catalog_adapter_cannot_write_registry(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data import market_catalog
    from aletheia.universe import registry

    monkeypatch.setattr(market_catalog, "_catalog_fetch", None)
    monkeypatch.setattr(registry, "_load_demo_whitelist", AsyncMock(return_value=None))

    def forbidden(*args, **kwargs):
        raise AssertionError("unavailable catalog cannot create clients or write")

    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", forbidden)
    monkeypatch.setattr(registry.repo, "sync_universe_assets", forbidden)
    assert not hasattr(registry, "_precision_to_decimals")
    assert not hasattr(registry, "_is_excluded")
    with pytest.raises(RuntimeError, match="universe_market_catalog_extension_unavailable"):
        await registry.sync_registry(AletheiaConfig())


@pytest.mark.asyncio
async def test_registry_applies_policy_to_normalized_catalog_without_raw_client(monkeypatch):
    from dataclasses import replace
    from aletheia.config.schema import AletheiaConfig
    from aletheia.data import market_catalog
    from aletheia.universe import registry

    entry = market_catalog.MarketCatalogEntry(
        symbol="BTC/USDT", base="BTC", quote="USDT", market_type="spot",
        background={"asset_class": "crypto"}, excluded_crypto=False,
        min_notional=5, price_precision=2, amount_precision=4,
    )
    fetch = AsyncMock(return_value=[entry, replace(entry, market_type="perp"),
                                   replace(entry, symbol="ETH/USDT", base="ETH")])
    monkeypatch.setattr(market_catalog, "_catalog_fetch", fetch)
    monkeypatch.setattr(registry, "_load_demo_whitelist", AsyncMock(return_value={"BTC/USDT"}))
    rows = []
    monkeypatch.setattr(registry.repo, "sync_universe_assets", lambda assets, **kwargs: (rows.extend(assets) or len(assets), 0, 0))

    def forbidden(*args, **kwargs):
        raise AssertionError("Core catalog policy cannot acquire a raw client")

    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", forbidden)
    cfg = AletheiaConfig()
    cfg.exchange.enabled_markets["usdt_futures"] = False
    cfg.universe.blacklist = ["BTC/USDT"]
    assert await registry.sync_registry(cfg) == 1
    fetch.assert_awaited_once_with(cfg.market_data.ccxt_exchange, cfg.market_data.ccxt_fallback or cfg.market_data.ccxt_exchange)
    assert (rows[0].symbol, rows[0].market_type, rows[0].blacklist_reason) == ("BTC/USDT", "spot", "config_blacklist")
    assert (rows[0].min_notional, rows[0].price_precision, rows[0].amount_precision) == (5, 2, 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog, unavailable", [
    ({}, True), (None, True), ([], True),
    ({"BTC/USD": {"active": True, "base": "BTC", "quote": "USD"}}, False),
])
async def test_catalog_availability_precedes_registry_mutation(monkeypatch, catalog, unavailable):
    from types import SimpleNamespace
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe import registry

    exchange = SimpleNamespace(markets=catalog, load_markets=AsyncMock())
    provider = SimpleNamespace(_ensure_primary=lambda: exchange, close=AsyncMock())
    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", lambda **kwargs: provider)
    monkeypatch.setattr(registry, "_load_demo_whitelist", AsyncMock(return_value=None))
    writes = []

    def sync(*args, **kwargs):
        writes.append("registry")
        return (0, 0, 1)

    monkeypatch.setattr(registry.repo, "sync_universe_assets", sync)
    monkeypatch.setattr(registry, "_invalidate_stale_pipeline_data", lambda: writes.append("pipeline"))
    if unavailable:
        with pytest.raises(RuntimeError, match="universe_market_catalog_unavailable"):
            await registry.sync_registry(AletheiaConfig())
        assert writes == []
    else:
        # A complete catalog filtered to zero supported assets is distinct.
        assert await registry.sync_registry(AletheiaConfig()) == 0
        assert writes == ["registry", "pipeline"]
    provider.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_capability_refresh_requires_explicit_extension(monkeypatch):
    from aletheia.execution import venue_capabilities

    monkeypatch.setattr(venue_capabilities, "_market_capability_reader", None)
    with pytest.raises(RuntimeError, match="exchange_extension_unavailable"):
        await venue_capabilities.fetch_venue_capabilities("okx_demo")

    reader = AsyncMock(return_value={"spot": frozenset({"BTC/USDT"})})
    monkeypatch.setattr(venue_capabilities, "_market_capability_reader", reader)
    assert await venue_capabilities.fetch_venue_capabilities("okx_demo") == {
        "spot": frozenset({"BTC/USDT"}),
    }
    reader.assert_awaited_once_with("okx_demo")


@pytest.mark.asyncio
async def test_extension_normalizes_restricted_market_vocabulary(monkeypatch):
    from extensions.exchanges.composition import fetch_market_restrictions

    reader = AsyncMock(return_value={"swap": frozenset({"BTC/USDT"})})
    monkeypatch.setattr(
        "extensions.exchanges.ccxt.market_capabilities.load_public_market_capabilities",
        reader,
    )
    assert await fetch_market_restrictions("okx_demo") == {
        "usdt_futures": frozenset({"BTC/USDT"}), "spot": frozenset(),
    }
    reader.assert_awaited_once_with("okx", sandbox=True)
    assert await fetch_market_restrictions("bitget_demo") is None
    assert reader.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["okx_demo", "contract_adapter"])
async def test_load_demo_whitelist_returns_merged_frozenset(mode):
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
        result = await _load_demo_whitelist(mode)
    assert isinstance(result, frozenset), (
        "Expected frozenset, got " + type(result).__name__
    )
    assert "BTC/USDT" in result
    assert "WLD/USDT" in result
    assert len(result) == 4


@pytest.mark.asyncio
async def test_load_demo_whitelist_non_okx_returns_none(monkeypatch):
    from aletheia.execution import venue_capabilities
    from extensions.exchanges.composition import fetch_market_restrictions

    monkeypatch.setattr(venue_capabilities, "_market_capability_reader", fetch_market_restrictions)
    assert await _load_demo_whitelist("bitget_demo") is None
    assert await _load_demo_whitelist("binance_testnet") is None
    assert await _load_demo_whitelist("paper_internal") is None


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
