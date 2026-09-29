"""Phase 2 tests: CCXT replaces custom HMAC/httpx in BitgetClient."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# BitgetClient._make_exchange — uta flag and sandbox mode
# ---------------------------------------------------------------------------

def test_bitget_client_classic_v2_sets_uta_false():
    """classic_v2 profile sets uta=False in CCXT options."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")
    assert client._uta is False


def test_bitget_client_uta_v3_sets_uta_true():
    """uta_v3 profile sets uta=True in CCXT options."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="uta_v3")
    assert client._uta is True


def test_bitget_client_make_exchange_passes_uta_option():
    """_make_exchange passes uta=True into make_ccxt options for uta_v3."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="uta_v3")
    fake_exchange = MagicMock()
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)) as mock_make,
    ):
        client._make_exchange()

    _args, kwargs = mock_make.call_args
    assert kwargs.get("options", {}).get("uta") is True


def test_bitget_client_make_exchange_enables_sandbox_for_demo():
    """_make_exchange calls set_sandbox_mode(True) when mode is demo."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")
    fake_exchange = MagicMock()
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
    ):
        client._make_exchange()

    fake_exchange.set_sandbox_mode.assert_called_once_with(True)


def test_bitget_client_make_exchange_no_sandbox_for_live():
    """_make_exchange does NOT call set_sandbox_mode when mode is live."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="live", profile="classic_v2")
    fake_exchange = MagicMock()
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
    ):
        client._make_exchange()

    fake_exchange.set_sandbox_mode.assert_not_called()


def test_bitget_client_make_exchange_raises_on_missing_creds():
    """_make_exchange raises RuntimeError when credentials are not configured."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")

    with patch("extensions.exchanges.bitget.client.get_secret", return_value=None):
        with pytest.raises(RuntimeError, match="credentials not configured"):
            client._make_exchange()


# ---------------------------------------------------------------------------
# BitgetClient — CCXT transport: plan history
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_plan_order_history_classic_uses_ccxt():
    """get_plan_order_history for classic_v2 calls the correct CCXT method."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")
    fake_exchange = MagicMock()
    fake_exchange.privateMixGetV2MixOrderOrdersPlanHistory = AsyncMock(
        return_value={"code": "00000", "data": {"entrustedList": [{"planStatus": "executed"}]}}
    )
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
        patch("extensions.exchanges.bitget.client.close_ccxt", AsyncMock(return_value=True)),
    ):
        result = await client.get_plan_order_history(symbol="BTCUSDT", order_id="abc123")

    assert result == [{"planStatus": "executed"}]
    fake_exchange.privateMixGetV2MixOrderOrdersPlanHistory.assert_called_once()
    called_params = fake_exchange.privateMixGetV2MixOrderOrdersPlanHistory.call_args[0][0]
    assert called_params["productType"] == "USDT-FUTURES"
    assert called_params["planType"] == "profit_loss"
    assert called_params["symbol"] == "BTCUSDT"
    assert called_params["orderId"] == "abc123"


@pytest.mark.asyncio
async def test_get_plan_order_history_uta_v3_uses_ccxt():
    """get_plan_order_history for uta_v3 calls privateUtaGetV3TradeStrategySubOrders."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="uta_v3")
    fake_exchange = MagicMock()
    fake_exchange.privateUtaGetV3TradeStrategySubOrders = AsyncMock(
        return_value={
            "code": "00000",
            "data": {
                "list": [{"subOrderId": "child1", "status": "filled"}],
                "cursor": "",
            },
        }
    )
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
        patch("extensions.exchanges.bitget.client.close_ccxt", AsyncMock(return_value=True)),
    ):
        result = await client.get_plan_order_history(order_id="plan123")

    assert len(result) == 1
    assert result[0]["executeOrderId"] == "child1"
    assert result[0]["planStatus"] == "executed"
    assert result[0]["orderId"] == "plan123"


# ---------------------------------------------------------------------------
# BitgetClient — CCXT transport: fills
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_order_fills_classic_uses_ccxt():
    """get_order_fills for classic_v2 calls privateMixGetV2MixOrderFills."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")
    fake_exchange = MagicMock()
    fake_exchange.privateMixGetV2MixOrderFills = AsyncMock(
        return_value={
            "code": "00000",
            "data": {
                "fillList": [{"orderId": "ord1", "tradeId": "fill1", "side": "buy"}],
            },
        }
    )
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
        patch("extensions.exchanges.bitget.client.close_ccxt", AsyncMock(return_value=True)),
    ):
        result = await client.get_order_fills(order_id="ord1", symbol="BTCUSDT")

    assert result == [{"orderId": "ord1", "tradeId": "fill1", "side": "buy"}]
    fake_exchange.privateMixGetV2MixOrderFills.assert_called_once()
    called_params = fake_exchange.privateMixGetV2MixOrderFills.call_args[0][0]
    assert called_params["productType"] == "USDT-FUTURES"
    assert called_params["symbol"] == "BTCUSDT"
    assert called_params["orderId"] == "ord1"


@pytest.mark.asyncio
async def test_get_order_fills_uta_v3_uses_ccxt():
    """get_order_fills for uta_v3 calls privateUtaGetV3TradeFills."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="uta_v3")
    fake_exchange = MagicMock()
    fake_exchange.privateUtaGetV3TradeFills = AsyncMock(
        return_value={
            "code": "00000",
            "data": {"list": [{"orderId": "ord2", "tradeId": "fill2"}]},
        }
    )
    fake_session = MagicMock()

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
        patch("extensions.exchanges.bitget.client.close_ccxt", AsyncMock(return_value=True)),
    ):
        result = await client.get_order_fills(order_id="ord2", symbol="ETHUSDT")

    assert result == [{"orderId": "ord2", "tradeId": "fill2"}]
    fake_exchange.privateUtaGetV3TradeFills.assert_called_once()
    called_params = fake_exchange.privateUtaGetV3TradeFills.call_args[0][0]
    assert called_params["category"] == "USDT-FUTURES"
    assert called_params["symbol"] == "ETHUSDT"
    assert called_params["orderId"] == "ord2"


# ---------------------------------------------------------------------------
# Exchange cleanup: close_ccxt always called even on failure
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exchange_closed_on_plan_history_error():
    """close_ccxt is called even when the CCXT call raises an exception."""
    from extensions.exchanges.bitget.client import BitgetClient

    client = BitgetClient(mode="demo", profile="classic_v2")
    fake_exchange = MagicMock()
    fake_exchange.privateMixGetV2MixOrderOrdersPlanHistory = AsyncMock(side_effect=RuntimeError("boom"))
    fake_session = MagicMock()
    close_mock = AsyncMock(return_value=True)

    with (
        patch("extensions.exchanges.bitget.client.get_secret", return_value="val"),
        patch("extensions.exchanges.bitget.client.make_ccxt", return_value=(fake_exchange, fake_session)),
        patch("extensions.exchanges.bitget.client.close_ccxt", close_mock),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await client.get_plan_order_history()

    close_mock.assert_called_once_with(fake_exchange, fake_session)


# ---------------------------------------------------------------------------
# Probe: auth.py is gone, no custom HMAC signing in codebase
# ---------------------------------------------------------------------------

def test_auth_module_does_not_exist():
    """auth.py has been deleted — no custom HMAC signing remains."""
    import importlib
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("extensions.exchanges.bitget.auth")
