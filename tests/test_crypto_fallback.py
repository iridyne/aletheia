"""Tests for crypto fallback data sources (CoinGecko + Binance public + chain)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace

from aletheia.data.providers import Candle, Ticker
from aletheia.data.sources.symbol_map import to_coingecko_id, to_binance_symbol
from aletheia.data.sources.coingecko import CoinGeckoProvider
from aletheia.data.sources.binance_public import BinancePublicProvider
from aletheia.data.sources.chain import FallbackChainProvider


# ---------------------------------------------------------------------------
# symbol_map
# ---------------------------------------------------------------------------


class TestSymbolMap:
    def test_coingecko_known(self):
        assert to_coingecko_id("BTC/USDT") == "bitcoin"
        assert to_coingecko_id("ETH/USDT") == "ethereum"
        assert to_coingecko_id("SOL/USDT") == "solana"

    def test_coingecko_unknown_returns_none(self):
        # F6: unmapped symbols return None (graceful) instead of raising KeyError,
        # so callers can fall back to another data source.
        assert to_coingecko_id("UNKNOWN/USDT") is None

    def test_binance_symbol(self):
        assert to_binance_symbol("BTC/USDT") == "BTCUSDT"
        assert to_binance_symbol("ETH/USDT") == "ETHUSDT"


# ---------------------------------------------------------------------------
# CoinGeckoProvider
# ---------------------------------------------------------------------------


class TestCoinGeckoProvider:
    @pytest.mark.anyio
    async def test_get_ticker(self, monkeypatch):
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._last_request_time", 0.0
        )
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._MIN_INTERVAL_SEC", 0.0
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "bitcoin": {
                "usd": 67500.0,
                "usd_24h_vol": 25000000000.0,
                "usd_24h_change": 2.5,
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aletheia.net.httpx_client", return_value=mock_client):
            provider = CoinGeckoProvider()
            ticker = await provider.get_ticker("BTC/USDT")

        assert ticker.symbol == "BTC/USDT"
        assert ticker.last_price == 67500.0
        assert ticker.volume_24h == 25000000000.0
        assert ticker.change_pct_24h == 2.5
        assert ticker.provider == "coingecko"

    @pytest.mark.anyio
    async def test_get_ohlcv(self, monkeypatch):
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._last_request_time", 0.0
        )
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._MIN_INTERVAL_SEC", 0.0
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            [1700000000000, 67000.0, 67500.0, 66800.0, 67200.0],
            [1700014400000, 67200.0, 67800.0, 67100.0, 67600.0],
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aletheia.net.httpx_client", return_value=mock_client):
            provider = CoinGeckoProvider()
            candles = await provider.get_ohlcv("BTC/USDT", timeframe="4h", limit=10)

        assert len(candles) == 2
        assert candles[0].open == 67000.0
        assert candles[0].volume == 0.0
        assert candles[1].close == 67600.0

    @pytest.mark.anyio
    async def test_unsupported_timeframe(self):
        provider = CoinGeckoProvider()
        with pytest.raises(NotImplementedError):
            await provider.get_ohlcv("BTC/USDT", timeframe="1m")

    @pytest.mark.anyio
    async def test_unknown_symbol_raises(self, monkeypatch):
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._last_request_time", 0.0
        )
        monkeypatch.setattr(
            "aletheia.data.sources.coingecko._MIN_INTERVAL_SEC", 0.0
        )
        provider = CoinGeckoProvider()
        # F6: unmapped symbol raises a clear ValueError (not KeyError) so the provider
        # fallback chain degrades to another source.
        with pytest.raises(ValueError):
            await provider.get_ticker("FAKECOIN/USDT")


# ---------------------------------------------------------------------------
# BinancePublicProvider
# ---------------------------------------------------------------------------


class TestBinancePublicProvider:
    @pytest.mark.anyio
    async def test_get_ticker(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "lastPrice": "67500.00",
            "quoteVolume": "25000000000.00",
            "priceChangePercent": "2.50",
            "highPrice": "68000.00",
            "lowPrice": "66500.00",
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aletheia.net.httpx_client", return_value=mock_client):
            provider = BinancePublicProvider()
            ticker = await provider.get_ticker("BTC/USDT")

        assert ticker.symbol == "BTC/USDT"
        assert ticker.last_price == 67500.0
        assert ticker.volume_24h == 25000000000.0
        assert ticker.change_pct_24h == 2.5
        assert ticker.high_24h == 68000.0
        assert ticker.low_24h == 66500.0
        assert ticker.provider == "binance_public"

    @pytest.mark.anyio
    async def test_get_ohlcv(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            [1700000000000, "67000.0", "67500.0", "66800.0", "67200.0", "150.5",
             1700003599999, "10101500.0", 5000, "80.0", "5400000.0", "0"],
            [1700003600000, "67200.0", "67800.0", "67100.0", "67600.0", "200.3",
             1700007199999, "13520000.0", 6000, "110.0", "7400000.0", "0"],
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aletheia.net.httpx_client", return_value=mock_client):
            provider = BinancePublicProvider()
            candles = await provider.get_ohlcv("BTC/USDT", timeframe="1h", limit=10)

        assert len(candles) == 2
        assert candles[0].timestamp == 1700000000000
        assert candles[0].open == 67000.0
        assert candles[0].volume == 150.5
        assert candles[1].close == 67600.0

    @pytest.mark.anyio
    async def test_since_param_forwarded(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("aletheia.net.httpx_client", return_value=mock_client):
            provider = BinancePublicProvider()
            await provider.get_ohlcv("ETH/USDT", timeframe="1h", since=1700000000000)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["startTime"] == 1700000000000


# ---------------------------------------------------------------------------
# FallbackChainProvider
# ---------------------------------------------------------------------------


class TestFallbackChainProvider:
    @pytest.mark.anyio
    async def test_first_provider_succeeds(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(
            return_value=Ticker(symbol="BTC/USDT", last_price=67000, volume_24h=1e9, change_pct_24h=1.0)
        )
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock()

        chain = FallbackChainProvider([p1, p2])
        result = await chain.get_ticker("BTC/USDT")

        assert result.last_price == 67000
        p2.get_ticker.assert_not_called()

    @pytest.mark.anyio
    async def test_first_fails_second_succeeds(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(side_effect=RuntimeError("exchange down"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(
            return_value=Ticker(symbol="BTC/USDT", last_price=67500, volume_24h=1e9, change_pct_24h=2.0)
        )

        chain = FallbackChainProvider([p1, p2])
        result = await chain.get_ticker("BTC/USDT")

        assert result.last_price == 67500

    @pytest.mark.anyio
    async def test_all_fail_raises_last(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(side_effect=RuntimeError("err1"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(side_effect=ValueError("err2"))

        chain = FallbackChainProvider([p1, p2])
        with pytest.raises(ValueError, match="err2"):
            await chain.get_ticker("BTC/USDT")

    @pytest.mark.anyio
    async def test_not_implemented_skips_silently(self):
        p1 = AsyncMock()
        p1.get_ohlcv = AsyncMock(side_effect=NotImplementedError("no 1m"))
        p2 = AsyncMock()
        p2.get_ohlcv = AsyncMock(
            return_value=[Candle(timestamp=1, open=1, high=2, low=0.5, close=1.5, volume=100)]
        )

        chain = FallbackChainProvider([p1, p2])
        result = await chain.get_ohlcv("BTC/USDT", timeframe="1m")

        assert len(result) == 1
        assert result[0].close == 1.5

    @pytest.mark.anyio
    async def test_close_calls_all(self):
        p1 = AsyncMock()
        p1.close = AsyncMock()
        p2 = AsyncMock()
        p2.close = AsyncMock()

        chain = FallbackChainProvider([p1, p2])
        await chain.close()

        p1.close.assert_called_once()
        p2.close.assert_called_once()

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError):
            FallbackChainProvider([])


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


class TestFactory:
    def test_fallback_enabled_returns_chain(self, monkeypatch):
        from aletheia.config import loader
        from aletheia.data.ccxt_provider import get_market_data_provider

        md = SimpleNamespace(
            ccxt_exchange="binance",
            ccxt_fallback="bybit",
            enable_crypto_fallback=True,
        )
        monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(market_data=md))

        p = get_market_data_provider()
        assert isinstance(p, FallbackChainProvider)
        assert len(p._providers) == 3

    def test_fallback_disabled_returns_ccxt(self, monkeypatch):
        from aletheia.config import loader
        from aletheia.data.ccxt_provider import get_market_data_provider, CCXTProvider

        md = SimpleNamespace(
            ccxt_exchange="binance",
            ccxt_fallback="bybit",
            enable_crypto_fallback=False,
        )
        monkeypatch.setattr(loader, "load_config", lambda: SimpleNamespace(market_data=md))

        p = get_market_data_provider()
        assert isinstance(p, CCXTProvider)
