"""Tests for InstrumentId and symbol conversion helpers."""

import pytest
from aletheia.data.symbols import (
    InstrumentId,
    to_canonical, to_bitget, to_ccxt, to_ccxt_perp, to_yfinance,
    canonical_of,
    MARKET_SPOT, MARKET_PERP, MARKET_FUTURES,
)


# ---------------------------------------------------------------------------
# InstrumentId construction and string round-trip
# ---------------------------------------------------------------------------

class TestInstrumentIdConstruction:
    def test_valid_spot(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        assert iid.exchange == "binance"
        assert iid.symbol == "BTC/USDT"
        assert iid.market_type == "spot"

    def test_str_format(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        assert str(iid) == "binance:BTC/USDT:spot"

    def test_str_format_perp(self):
        iid = InstrumentId(exchange="bybit", symbol="ETH/USDT", market_type="perp")
        assert str(iid) == "bybit:ETH/USDT:perp"

    def test_frozen_immutable(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        with pytest.raises((AttributeError, TypeError)):
            iid.exchange = "bybit"  # type: ignore

    def test_hashable(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        s = {iid, iid}
        assert len(s) == 1

    def test_equality(self):
        a = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        b = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        assert a == b

    def test_inequality_exchange(self):
        a = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        b = InstrumentId(exchange="bybit",   symbol="BTC/USDT", market_type="spot")
        assert a != b

    def test_inequality_market_type(self):
        a = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        b = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="perp")
        assert a != b


class TestInstrumentIdValidation:
    def test_empty_exchange_raises(self):
        with pytest.raises(ValueError, match="exchange"):
            InstrumentId(exchange="", symbol="BTC/USDT", market_type="spot")

    def test_non_canonical_symbol_raises(self):
        with pytest.raises(ValueError, match="canonical"):
            InstrumentId(exchange="binance", symbol="BTCUSDT", market_type="spot")

    def test_invalid_market_type_raises(self):
        with pytest.raises(ValueError, match="market_type"):
            InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="unknown")


# ---------------------------------------------------------------------------
# parse() — string round-trip
# ---------------------------------------------------------------------------

class TestParse:
    def test_spot_round_trip(self):
        s = "binance:BTC/USDT:spot"
        iid = InstrumentId.parse(s)
        assert str(iid) == s

    def test_perp_round_trip(self):
        s = "bybit:ETH/USDT:perp"
        iid = InstrumentId.parse(s)
        assert str(iid) == s

    def test_futures_round_trip(self):
        s = "bitget:SOL/USDT:futures"
        iid = InstrumentId.parse(s)
        assert str(iid) == s

    def test_exchange_normalised_lowercase(self):
        iid = InstrumentId.parse("Binance:BTC/USDT:spot")
        assert iid.exchange == "binance"

    def test_malformed_missing_parts_raises(self):
        with pytest.raises(ValueError):
            InstrumentId.parse("binance:BTC/USDT")  # missing market_type

    def test_malformed_empty_raises(self):
        with pytest.raises(ValueError):
            InstrumentId.parse("")


# ---------------------------------------------------------------------------
# from_parts() — normalisation factory
# ---------------------------------------------------------------------------

class TestFromParts:
    def test_normalises_exchange_case(self):
        iid = InstrumentId.from_parts("Binance", "BTC/USDT", "spot")
        assert iid.exchange == "binance"

    def test_normalises_bitget_symbol(self):
        iid = InstrumentId.from_parts("binance", "BTCUSDT", "spot")
        assert iid.symbol == "BTC/USDT"

    def test_normalises_dash_symbol(self):
        iid = InstrumentId.from_parts("binance", "BTC-USDT", "spot")
        assert iid.symbol == "BTC/USDT"

    def test_default_market_type_is_spot(self):
        iid = InstrumentId.from_parts("binance", "BTC/USDT")
        assert iid.market_type == MARKET_SPOT

    def test_invalid_market_type_still_raises(self):
        with pytest.raises(ValueError):
            InstrumentId.from_parts("binance", "BTC/USDT", "leveraged")


# ---------------------------------------------------------------------------
# to_ccxt_symbol()
# ---------------------------------------------------------------------------

class TestToCcxtSymbol:
    def test_spot_returns_canonical(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="spot")
        assert iid.to_ccxt_symbol() == "BTC/USDT"

    def test_perp_usdt_returns_colon_settle(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type="perp")
        assert iid.to_ccxt_symbol() == "BTC/USDT:USDT"

    def test_perp_usdc_settle(self):
        iid = InstrumentId(exchange="binance", symbol="BTC/USDC", market_type="perp")
        assert iid.to_ccxt_symbol() == "BTC/USDC:USDC"  # quote currency is the settle

    def test_futures_same_as_perp(self):
        iid = InstrumentId(exchange="bybit", symbol="ETH/USDT", market_type="futures")
        assert iid.to_ccxt_symbol() == "ETH/USDT:USDT"

    def test_eth_spot(self):
        iid = InstrumentId(exchange="bybit", symbol="ETH/USDT", market_type="spot")
        assert iid.to_ccxt_symbol() == "ETH/USDT"


# ---------------------------------------------------------------------------
# to_bitget_symbol()
# ---------------------------------------------------------------------------

class TestToBitgetSymbol:
    def test_spot_btc(self):
        iid = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type="spot")
        assert iid.to_bitget_symbol() == "BTCUSDT"

    def test_perp_same_as_spot(self):
        iid = InstrumentId(exchange="bitget", symbol="ETH/USDT", market_type="perp")
        assert iid.to_bitget_symbol() == "ETHUSDT"


# ---------------------------------------------------------------------------
# Legacy string helper functions
# ---------------------------------------------------------------------------

class TestToCanonical:
    def test_already_canonical(self):
        assert to_canonical("BTC/USDT") == "BTC/USDT"

    def test_bitget_format(self):
        assert to_canonical("BTCUSDT") == "BTC/USDT"

    def test_dash_format(self):
        assert to_canonical("BTC-USDT") == "BTC/USDT"

    def test_ccxt_perp_suffix_stripped(self):
        assert to_canonical("BTC/USDT:USDT") == "BTC/USDT"

    def test_uppercase_normalised(self):
        assert to_canonical("btcusdt") == "BTC/USDT"

    def test_eth(self):
        assert to_canonical("ETHUSDT") == "ETH/USDT"

    def test_sol(self):
        assert to_canonical("SOLUSDT") == "SOL/USDT"


class TestToBitget:
    def test_canonical_to_bitget(self):
        assert to_bitget("BTC/USDT") == "BTCUSDT"

    def test_already_bitget(self):
        assert to_bitget("BTCUSDT") == "BTCUSDT"


class TestToCcxtPerp:
    def test_usdt_quote(self):
        assert to_ccxt_perp("BTC/USDT") == "BTC/USDT:USDT"

    def test_usdc_quote(self):
        assert to_ccxt_perp("BTC/USDC") == "BTC/USDC:USDC"  # settle = quote

    def test_eth(self):
        assert to_ccxt_perp("ETH/USDT") == "ETH/USDT:USDT"

    def test_bitget_format_input(self):
        assert to_ccxt_perp("BTCUSDT") == "BTC/USDT:USDT"


class TestToYfinance:
    def test_usdt_to_usd(self):
        assert to_yfinance("BTC/USDT") == "BTC-USD"

    def test_usdc_to_usd(self):
        assert to_yfinance("BTC/USDC") == "BTC-USD"

    def test_btc_quote_preserved(self):
        assert to_yfinance("ETH/BTC") == "ETH-BTC"


# ---------------------------------------------------------------------------
# canonical_of() — InstrumentId-aware canonicaliser (D1 Bug 2 regression)
# ---------------------------------------------------------------------------

class TestCanonicalOf:
    def test_instrument_id_returns_its_canonical_symbol(self):
        # The exact D1 Bug 2 case: a fallback provider received an InstrumentId
        # and used to call to_canonical(str(iid)). str(iid) is
        # "okx:AAVE/USDT:spot"; to_canonical reads the leading "okx:" as a CCXT
        # perp settle suffix and collapses the whole thing to "OKX".
        iid = InstrumentId(exchange="okx", symbol="AAVE/USDT", market_type="spot")
        assert canonical_of(iid) == "AAVE/USDT"

    def test_documents_the_trap_it_avoids(self):
        # Pin the buggy behaviour so the regression can never silently return:
        # the old code path really does produce "OKX", canonical_of must not.
        iid = InstrumentId(exchange="okx", symbol="AAVE/USDT", market_type="spot")
        assert to_canonical(str(iid)) == "OKX"          # the trap
        assert canonical_of(iid) != "OKX"               # the fix

    def test_perp_instrument_id_keeps_canonical(self):
        iid = InstrumentId(exchange="bybit", symbol="BTC/USDT", market_type="perp")
        assert canonical_of(iid) == "BTC/USDT"

    def test_plain_string_passthrough(self):
        # Strings still flow through to_canonical unchanged (all venue forms).
        assert canonical_of("BTCUSDT") == "BTC/USDT"
        assert canonical_of("BTC-USDT") == "BTC/USDT"
        assert canonical_of("BTC/USDT:USDT") == "BTC/USDT"  # real perp suffix
        assert canonical_of("BTC/USDT") == "BTC/USDT"
