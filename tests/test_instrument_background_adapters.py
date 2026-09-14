"""Public venue decoding retains Core applicability without private clients."""

import importlib

import pytest

from aletheia.data.symbols import instrument_analysis_context
from extensions.market_data import decode_market_background, fetch_registry_catalog


@pytest.mark.parametrize(
    "venue,market_type,raw,asset_class,source,contract_type",
    [
        ("bitget", "spot", {}, "crypto", "supported_market_default", None),
        ("bitget", "perp", {"symbolType": "perpetual"}, "crypto", "supported_market_default", "PERPETUAL"),
        ("binance", "spot", {}, "crypto", "supported_market_default", None),
        ("binance", "perp", {}, "unknown", "unresolved", None),
        ("binance", "perp", {"underlyingType": "COIN", "contractType": "PERPETUAL"}, "crypto", "exchange_metadata", "PERPETUAL"),
        ("binance", "perp", {"underlyingType": "MYSTERY"}, "unknown", "exchange_metadata", None),
        ("okx", "perp", {"instCategory": "1", "instType": "SWAP"}, "crypto", "exchange_metadata", "SWAP"),
        ("okx", "perp", {"instCategory": "3", "instType": "SWAP"}, "equity", "exchange_metadata", "SWAP"),
        ("okx", "perp", {}, "unknown", "unresolved", None),
        ("unknown", "spot", {}, "unknown", "unresolved", None),
    ],
)
def test_venue_classification_preserves_defaults_and_missing(
    venue, market_type, raw, asset_class, source, contract_type,
):
    background = decode_market_background(
        symbol="BTC/USDT", venue=venue, market_type=market_type,
        market_info={"base": "BTC", "quote": "USDT", "info": raw},
    )
    assert background["asset_class"] == asset_class
    assert background["classification"]["source"] == source
    assert background["classification"]["contract_type"] == contract_type
    assert (instrument_analysis_context(background) is not None) == (asset_class == "crypto")


def test_binance_subtypes_and_core_stock_linked_scope():
    background = decode_market_background(
        symbol="SOXL/USDT", venue="binance", market_type="perp",
        market_info={"info": {"underlyingType": "EQUITY", "underlyingSubType": "ETF"}},
    )
    assert background["classification"]["underlying_subtypes"] == ["ETF"]
    assert instrument_analysis_context(background)["kind"] == "stock_linked_perp"
    assert instrument_analysis_context({**background, "venue": "bitget"}) is None
    assert instrument_analysis_context({**background, "venue": "okx"}) is None


@pytest.mark.parametrize("venue", ["bitget", "binance", "okx"])
def test_missing_venue_decoder_does_not_fall_back(monkeypatch, venue):
    import builtins

    original = builtins.__import__
    missing = f"extensions.exchanges.{venue}.instrument_background"

    def guarded(name, *args, **kwargs):
        if name == missing:
            raise ModuleNotFoundError(name, name=name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    with pytest.raises(ModuleNotFoundError) as exc:
        decode_market_background(
            symbol="BTC/USDT", venue=venue, market_type="spot", market_info={},
        )
    assert exc.value.name == missing


async def test_catalog_uses_venue_exclusion_and_closes_provider(monkeypatch):
    from types import SimpleNamespace
    from aletheia.data import ccxt_provider

    closed = []
    markets = {
        "BTC/USDT": {"base": "BTC", "quote": "USDT", "swap": True, "info": {"instCategory": "1"}},
        "SPCX/USDT": {"base": "SPCX", "quote": "USDT", "swap": True, "info": {"instCategory": "3"}},
    }

    async def load_markets():
        return markets

    async def close():
        closed.append(True)

    exchange = SimpleNamespace(markets=markets, load_markets=load_markets)
    monkeypatch.setattr(
        ccxt_provider, "get_market_data_provider",
        lambda **kwargs: SimpleNamespace(_ensure_primary=lambda: exchange, close=close),
    )
    entries = await fetch_registry_catalog("okx", "")
    assert [(row.symbol, row.excluded_crypto) for row in entries] == [
        ("BTC/USDT", False), ("SPCX/USDT", True),
    ]
    assert closed == [True]


def test_core_and_generic_catalog_do_not_decode_venue_wire_fields():
    import inspect
    from aletheia.data.symbols import build_instrument_background

    catalog = importlib.import_module("extensions.exchanges.ccxt.registry_catalog")
    for source in (inspect.getsource(build_instrument_background), inspect.getsource(catalog)):
        for field in ("underlyingType", "underlyingSubType", "contractType", "symbolType", "baseCoin", "instCategory", "instType"):
            assert field not in source
    assert "market_info" not in inspect.signature(build_instrument_background).parameters
