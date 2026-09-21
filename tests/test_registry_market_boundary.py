"""Registry owns admission and persistence; Data supplies normalized facts."""

from dataclasses import replace

import pytest

from aletheia.config.schema import AletheiaConfig
from aletheia.data.providers import MarketInstrument
from aletheia.data.symbols import build_instrument_background
from aletheia.universe import registry


def _fact(base="BTC", **kwargs):
    return MarketInstrument(
        "bitget", f"{base}/USDT", base, "USDT", "spot",
        build_instrument_background(symbol=f"{base}/USDT", venue="bitget", market_type="spot", market_info={}),
        **kwargs,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "wrong_type", "wrong_venue", "wrong_symbol", "wrong_market", "read_error", "background_venue", "background_symbol", "background_market_type", "background_physical_market_type", "lowercase_symbol", "padded_symbol"])
async def test_unavailable_market_facts_never_write_registry(monkeypatch, failure):
    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "bitget"
    closed = []

    class Provider:
        async def get_market_instruments(self):
            if failure == "read_error":
                raise RuntimeError("listing unavailable")
            if failure == "wrong_type":
                return [{}]
            if failure in {"lowercase_symbol", "padded_symbol"}:
                fact = _fact("btc" if failure == "lowercase_symbol" else " BTC")
                return [replace(fact, background=fact.background | {"symbol": fact.symbol})]
            if failure.startswith("background_"):
                field = failure.removeprefix("background_")
                fact = _fact()
                return [replace(fact, background=fact.background | {field: "wrong"})]
            changes = {"wrong_venue": {"venue": "okx"}, "wrong_symbol": {"symbol": "ETH/USDT"}, "wrong_market": {"market_type": "option"}}
            return [replace(_fact(), **changes[failure])]

        async def close(self):
            closed.append(True)

    provider = Provider()
    if failure == "missing":
        provider.get_market_instruments = None
    monkeypatch.setattr(registry, "get_market_data_provider", lambda *args, **kwargs: provider)
    monkeypatch.setattr(registry.repo, "sync_universe_assets", lambda *args, **kwargs: pytest.fail("unavailable facts must not write"))
    with pytest.raises((RuntimeError, ValueError)):
        await registry.sync_registry(cfg)
    assert closed == [True]


@pytest.mark.asyncio
async def test_registry_retains_admission_blacklist_and_cleanup(monkeypatch):
    cfg = AletheiaConfig()
    cfg.market_data.ccxt_exchange = "bitget"
    cfg.exchange.enabled_markets = {"usdt_futures": False}
    cfg.universe.blacklist = ["SCAM/USDT"]
    closed, stored = [], []
    perp = _fact("ETH")
    perp = replace(perp, market_type="perp", background=perp.background | {"market_type": "perp", "physical_market_type": "perp"})
    facts = [_fact(), _fact("SCAM"), _fact("BTC3L"), _fact("TEST", is_test_or_dated=True), perp]

    class Provider:
        async def get_market_instruments(self):
            return facts

        async def close(self):
            closed.append(True)

    def create(venue, *, exact_venue):
        assert venue == "bitget" and exact_venue is True
        return Provider()

    def persist(assets, *, active_venue, stale_cutoff_iso):
        assert active_venue == "bitget" and stale_cutoff_iso
        stored.extend(assets)
        return len(assets), 0, 0

    monkeypatch.setattr(registry, "get_market_data_provider", create)
    monkeypatch.setattr(registry.repo, "sync_universe_assets", persist)
    assert await registry.sync_registry(cfg) == 2
    assert [(asset.symbol, asset.blacklist_reason) for asset in stored] == [("BTC/USDT", None), ("SCAM/USDT", "config_blacklist")]
    assert closed == [True]


@pytest.mark.asyncio
async def test_adapter_normalizes_market_facts_without_registry_authority(monkeypatch):
    from aletheia.data.ccxt_provider import CCXTProvider

    def raw(base, **kwargs):
        return {"base": base, "quote": "USDT", "id": base, "symbol": f"{base}/USDT", **kwargs}

    class Exchange:
        markets = {
            "BTC": raw("BTC", precision={"price": 0.01, "amount": 3}, limits={"cost": {"min": 5}}),
            "TEST": raw("TEST", swap=True, contractSize=0.01),
            "EQUITY": raw("SPCX", info={"instCategory": "3"}),
            "inactive": raw("BAD", active=False),
            "option": raw("OPT", option=True),
            "other_quote": raw("USD", quote="USDC"),
        }

        options = {}

        async def load_markets(self):
            return self.markets

    provider = CCXTProvider(exchange="bitget", fallback_exchange="okx")
    monkeypatch.setattr(provider, "_ensure_primary", lambda _market_type=None: Exchange())
    monkeypatch.setattr(provider, "_ensure_fallback", lambda: pytest.fail("cross-venue listing"))
    monkeypatch.setattr(registry.repo, "sync_universe_assets", lambda *args, **kwargs: pytest.fail("Data must not persist registry"))
    facts = await provider.get_market_instruments()
    assert all(isinstance(item, MarketInstrument) and item.venue == "bitget" for item in facts)
    assert [item.symbol for item in facts] == ["BTC/USDT", "TEST/USDT", "SPCX/USDT"]
    assert (facts[0].price_precision, facts[0].amount_precision, facts[0].min_notional) == (2, 3, 5)
    assert facts[1].is_test_or_dated and facts[1].market_type == "perp"
    assert facts[1].contract_size == 0.01
    assert facts[2].is_tokenized_equity and facts[2].background["asset_class"] == "equity"
    perp_facts = await provider.get_market_instruments("perp")
    assert [item.symbol for item in perp_facts] == ["TEST/USDT"]
