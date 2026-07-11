from __future__ import annotations

import asyncio


def test_volume_scanner_fetches_exact_perp_and_normalizes_contract_volume(monkeypatch):
    from aletheia.data.collectors.volume_scanner import VolumeScannerCollector
    from aletheia.data.symbols import InstrumentId

    class FakeExchange:
        markets = {
            "SPCX/USDT:USDT": {"contract": True, "contractSize": 0.01},
        }

        async def load_markets(self):
            return self.markets

        async def fetch_tickers(self, symbols):
            assert symbols == ["SPCX/USDT:USDT"]
            return {
                "SPCX/USDT:USDT": {
                    "quoteVolume": None,
                    "baseVolume": 1000,
                    "last": 150,
                    "percentage": 2.5,
                }
            }

    class FakeProvider:
        _primary_name = "okx"

        def __init__(self):
            self.exchange = FakeExchange()

        def _ensure_primary(self):
            return self.exchange

        async def close(self):
            return None

    provider = FakeProvider()

    def fake_factory(*, exchange=None, native_ccxt=False):
        assert exchange == "okx"
        assert native_ccxt is True
        return provider

    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider",
        fake_factory,
    )
    instrument = InstrumentId.from_parts("okx", "SPCX/USDT", "perp")

    result = asyncio.run(VolumeScannerCollector(top_n=30).collect([instrument]))

    assert result["errors"] == []
    assert result["top_symbols"] == [
        {
            "symbol": "SPCX/USDT",
            "market_type": "perp",
            "exchange": "okx",
            "volume_24h_usd": 1500.0,
            "last_price": 150.0,
            "change_pct_24h": 2.5,
        }
    ]


def test_volume_scanner_skips_missing_market_without_losing_valid_group(monkeypatch):
    from aletheia.data.collectors.volume_scanner import VolumeScannerCollector
    from aletheia.data.symbols import InstrumentId

    class FakeExchange:
        markets = {"BTC/USDT:USDT": {"contract": True, "contractSize": 1.0}}

        async def load_markets(self):
            return self.markets

        async def fetch_tickers(self, symbols):
            assert symbols == ["BTC/USDT:USDT"]
            return {
                "BTC/USDT:USDT": {
                    "quoteVolume": 5_000_000,
                    "last": 50_000,
                    "percentage": 1.0,
                }
            }

    class FakeProvider:
        _primary_name = "okx"

        def _ensure_primary(self):
            return FakeExchange()

        async def close(self):
            return None

    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider",
        lambda **kwargs: FakeProvider(),
    )
    instruments = [
        InstrumentId.from_parts("okx", "BTC/USDT", "perp"),
        InstrumentId.from_parts("okx", "MISSING/USDT", "perp"),
    ]

    result = asyncio.run(VolumeScannerCollector(top_n=30).collect(instruments))

    assert [row["symbol"] for row in result["top_symbols"]] == ["BTC/USDT"]
    assert any("MISSING/USDT" in error for error in result["errors"])


def test_volume_scanner_keeps_observation_only_equity_out_of_crypto_ranking(monkeypatch):
    from aletheia.data.collectors.volume_scanner import VolumeScannerCollector
    from aletheia.data.symbols import InstrumentId

    class FakeExchange:
        markets = {
            "KORU/USDT:USDT": {
                "contract": True,
                "contractSize": 1.0,
                "info": {"instCategory": "3"},
            }
        }

        async def load_markets(self):
            return self.markets

        async def fetch_tickers(self, symbols):
            raise AssertionError("observation-only equity must not be fetched by crypto ranking")

    class FakeProvider:
        _primary_name = "okx"

        def _ensure_primary(self):
            return FakeExchange()

        async def close(self):
            return None

    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider",
        lambda **kwargs: FakeProvider(),
    )

    result = asyncio.run(VolumeScannerCollector().collect([
        InstrumentId.from_parts("okx", "KORU/USDT", "perp")
    ]))

    assert result["top_symbols"] == []
    assert result["errors"] == []
