"""Regression test for OKX demo whitelist dict-format bug."""

from unittest.mock import patch, AsyncMock

import pytest

from aletheia.universe.registry import _load_demo_whitelist


@pytest.mark.asyncio
async def test_load_demo_whitelist_returns_partitioned_frozenset():
    """Whitelist is a frozenset of ``symbol:market_type``, not a dict or union."""
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
    assert result == frozenset({
        "BTC/USDT:usdt_futures", "ETH/USDT:usdt_futures", "SOL/USDT:usdt_futures",
        "BTC/USDT:spot", "ETH/USDT:spot", "SOL/USDT:spot", "WLD/USDT:spot",
    })
    assert len(result) == 7
    # A flat union would let the spot-only WLD/USDT admit a perp instrument.
    assert "WLD/USDT:usdt_futures" not in result


@pytest.mark.asyncio
async def test_load_demo_whitelist_non_sandbox_returns_none():
    """A mode with no external venue needs no extra filter.

    ``bitget_demo`` and ``binance_testnet`` have sandbox=True profiles and DO
    return a capability snapshot — see
    test_load_demo_whitelist_bitget_demo_uses_sandbox_capabilities.
    ``paper_internal`` is a registered local identity with no venue listing.
    """
    assert await _load_demo_whitelist("paper_internal") is None


@pytest.mark.asyncio
async def test_load_demo_whitelist_unresolvable_external_mode_raises(monkeypatch):
    """An external mode whose profile cannot be resolved must not skip the filter.

    Codex review P2 on #3465: returning "no filter" for an unprovable listing
    source repopulates the demo universe with unexecutable symbols.
    """
    from aletheia.execution.order_constraints import ConstraintUnavailable

    def unresolvable(_execution_mode):
        raise ConstraintUnavailable("profile resolution failed", temporary=True)

    monkeypatch.setattr(
        "extensions.exchanges.ccxt.order_rules._listing_view", unresolvable
    )
    with pytest.raises(ConstraintUnavailable):
        await _load_demo_whitelist("okx_demo")


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
    assert result == frozenset([
        "BTC/USDT:usdt_futures", "ETH/USDT:usdt_futures",
        "BTC/USDT:spot", "ETH/USDT:spot", "ONDO/USDT:spot",
    ])


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


@pytest.mark.asyncio
async def test_registry_demo_whitelist_does_not_admit_perp_from_spot_listing(monkeypatch):
    """A spot-only demo listing must not admit a perp sharing the symbol.

    Codex review P1 on #3465: the whitelist was a flat symbol union, so a
    symbol the demo venue lists only as spot also passed the perp instrument
    and reached the same late Gateway rejection.
    """
    from unittest.mock import MagicMock
    from aletheia.universe.registry import sync_registry

    def market(base, *, swap):
        return {
            "id": f"{base}USDT",
            "symbol": f"{base}/USDT" + (":USDT" if swap else ""),
            "base": base,
            "quote": "USDT",
            "active": True,
            "spot": not swap,
            "swap": swap,
            "contract": swap,
            "precision": {"price": 4, "amount": 6},
            "limits": {"cost": {"min": 1}},
        }

    markets = {
        "SPOTONLY/USDT": market("SPOTONLY", swap=False),
        "SPOTONLY/USDT:USDT": market("SPOTONLY", swap=True),
    }

    class FakeExchange:
        async def load_markets(self):
            self.markets = markets

    captured = {}

    def fake_sync(assets, **_kwargs):
        captured["assets"] = assets
        return len(assets), 0, 0

    cfg = MagicMock()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.exchange.enabled_markets = {"usdt_futures": True}
    cfg.universe.blacklist = []

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.CCXTProvider._ensure_primary",
        lambda self: FakeExchange(),
    )
    # The demo listing has SPOTONLY/USDT as spot only.
    monkeypatch.setattr(
        "aletheia.universe.registry._load_demo_whitelist",
        AsyncMock(return_value=frozenset({"SPOTONLY/USDT:spot"})),
    )
    monkeypatch.setattr(
        "aletheia.universe.registry.repo.sync_universe_assets", fake_sync
    )

    assert await sync_registry(cfg) == 1
    assert [(a.symbol, a.market_type) for a in captured["assets"]] == [
        ("SPOTONLY/USDT", "spot")
    ]
