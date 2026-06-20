"""Tests for cross-source price divergence alerting (Phase 1)."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace

from aletheia.data.providers import Ticker
from aletheia.data.sources.chain import (
    FallbackChainProvider,
    _last_fallback_alert,
    _should_alert,
)


@pytest.fixture(autouse=True)
def _clear_dedup():
    """Reset dedup state between tests."""
    _last_fallback_alert.clear()
    yield
    _last_fallback_alert.clear()


def _make_ticker(symbol="BTC/USDT", price=67000.0, provider="binance"):
    return Ticker(
        symbol=symbol,
        last_price=price,
        volume_24h=1e9,
        change_pct_24h=1.0,
        provider=provider,
    )


def _mock_config(alert_enabled=True, cross_check=False, threshold=2.0):
    md = SimpleNamespace(
        price_divergence_alert_enabled=alert_enabled,
        cross_source_check_enabled=cross_check,
        price_divergence_threshold_pct=threshold,
    )
    return SimpleNamespace(market_data=md)


class TestFallbackNotification:
    """When primary fails and fallback succeeds, notify_operator is called."""

    @pytest.mark.anyio
    async def test_fallback_fires_notification(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(side_effect=RuntimeError("rate limited"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, False, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            result = await chain.get_ticker("BTC/USDT")

        assert result.provider == "coingecko"
        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs[1]["level"] == "warning"
        assert "coingecko" in call_kwargs[0][1]

    @pytest.mark.anyio
    async def test_no_notification_when_primary_succeeds(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(return_value=_make_ticker(provider="binance"))
        p2 = AsyncMock()

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, False, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            result = await chain.get_ticker("BTC/USDT")

        assert result.provider == "binance"
        mock_notify.assert_not_called()

    @pytest.mark.anyio
    async def test_no_notification_when_alert_disabled(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(side_effect=RuntimeError("down"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(False, False, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            await chain.get_ticker("BTC/USDT")

        mock_notify.assert_not_called()

    @pytest.mark.anyio
    async def test_dedup_suppresses_second_alert(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(side_effect=RuntimeError("down"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, False, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            await chain.get_ticker("BTC/USDT")
            await chain.get_ticker("BTC/USDT")

        assert mock_notify.call_count == 1


class TestCrossSourceCheck:
    """When cross_source_check_enabled, compare primary vs secondary price."""

    @pytest.mark.anyio
    async def test_divergence_above_threshold_alerts(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(return_value=_make_ticker(price=67000, provider="binance"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(price=68500, provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, True, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            result = await chain.get_ticker("BTC/USDT")

        assert result.last_price == 67000
        mock_notify.assert_called_once()
        assert "divergence" in mock_notify.call_args[0][0].lower()

    @pytest.mark.anyio
    async def test_divergence_below_threshold_silent(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(return_value=_make_ticker(price=67000, provider="binance"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(price=67100, provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, True, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            await chain.get_ticker("BTC/USDT")

        mock_notify.assert_not_called()

    @pytest.mark.anyio
    async def test_cross_check_disabled_no_secondary_call(self):
        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(return_value=_make_ticker(price=67000, provider="binance"))
        p2 = AsyncMock()
        p2.get_ticker = AsyncMock(return_value=_make_ticker(price=99999, provider="coingecko"))

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, False, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            await chain.get_ticker("BTC/USDT")

        p2.get_ticker.assert_not_called()
        mock_notify.assert_not_called()

    @pytest.mark.anyio
    async def test_cross_check_timeout_silent(self):
        """If secondary provider times out, no alert and no crash."""
        import asyncio

        p1 = AsyncMock()
        p1.get_ticker = AsyncMock(return_value=_make_ticker(price=67000, provider="binance"))
        p2 = AsyncMock()

        async def slow_ticker(*a, **kw):
            await asyncio.sleep(10)

        p2.get_ticker = slow_ticker

        chain = FallbackChainProvider([p1, p2])

        with patch(
            "aletheia.data.sources.chain._load_divergence_config",
            return_value=(True, True, 2.0),
        ), patch("aletheia.notifications.notify_operator") as mock_notify:
            result = await chain.get_ticker("BTC/USDT")

        assert result.last_price == 67000
        mock_notify.assert_not_called()


class TestShouldAlert:
    """Unit tests for the dedup helper."""

    def test_first_call_returns_true(self):
        assert _should_alert("TEST/USDT") is True

    def test_immediate_second_call_returns_false(self):
        _should_alert("TEST/USDT")
        assert _should_alert("TEST/USDT") is False

    def test_different_symbols_independent(self):
        _should_alert("BTC/USDT")
        assert _should_alert("ETH/USDT") is True
