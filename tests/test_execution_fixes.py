"""Focused regression tests for #1797, #1732, #1792, #1481."""
from __future__ import annotations

import math
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest


# ---------------------------------------------------------------------------
# #1797 — OKX/Binance take_profit_price forwarded in OrderRequest
# ---------------------------------------------------------------------------

def test_okx_order_request_carries_take_profit():
    """OKX OrderRequest must carry take_profit_price (#1797)."""
    from aletheia.exchanges.okx.client import OrderRequest
    req = OrderRequest(
        symbol="BTC/USDT", market_type="usdt_futures",
        side="buy", order_type="market", quantity=0.01,
        take_profit_price=70000.0,
    )
    assert req.take_profit_price == 70000.0


def test_binance_order_request_carries_take_profit():
    """Binance OrderRequest must carry take_profit_price (#1797)."""
    from aletheia.exchanges.binance.client import OrderRequest
    req = OrderRequest(
        symbol="BTC/USDT", market_type="usdt_futures",
        side="buy", order_type="market", quantity=0.01,
        take_profit_price=70000.0, stop_price=60000.0,
    )
    assert req.take_profit_price == 70000.0
    assert req.stop_price == 60000.0


# ---------------------------------------------------------------------------
# #1732 — 1x leverage must call set_leverage
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_okx_client_calls_set_leverage_at_1x():
    """OKX place_order must call set_leverage when leverage=1 (#1732)."""
    from aletheia.exchanges.okx.client import OKXClient, OrderRequest

    with patch.object(OKXClient, "_ensure_exchange") as mock_ensure, \
         patch.object(OKXClient, "set_leverage", new_callable=AsyncMock) as mock_sl, \
         patch.object(OKXClient, "_contract_size", new_callable=AsyncMock, return_value=0.01):
        mock_ex = MagicMock()
        mock_ex.load_markets = AsyncMock()
        mock_ex.markets = {"BTC/USDT:USDT": {"id": "BTC-USDT-SWAP", "active": True}}
        mock_ex.precisionMode = None
        resp = MagicMock()
        resp.get = lambda k, d=None: {"id": "oid1", "status": "closed", "average": "65000", "filled": 0.01}.get(k, d)
        mock_ex.create_order = AsyncMock(return_value=resp)
        mock_ensure.return_value = mock_ex

        req = OrderRequest(
            symbol="BTC/USDT", market_type="usdt_futures",
            side="buy", order_type="market", quantity=0.01,
            leverage=1,
        )
        try:
            await OKXClient.__new__(OKXClient).place_order(req)
        except Exception:
            pass
        mock_sl.assert_called()


@pytest.mark.asyncio
async def test_binance_client_calls_set_leverage_at_1x():
    """Binance place_order must call set_leverage when leverage=1 (#1732)."""
    from aletheia.exchanges.binance.client import BinanceClient, OrderRequest

    with patch.object(BinanceClient, "_ensure_exchange") as mock_ensure, \
         patch.object(BinanceClient, "set_leverage", new_callable=AsyncMock) as mock_sl:
        mock_ex = MagicMock()
        resp = MagicMock()
        resp.get = lambda k, d=None: {"id": "bid1", "status": "closed", "average": "65000", "filled": 0.01}.get(k, d)
        mock_ex.create_order = AsyncMock(return_value=resp)
        mock_ensure.return_value = mock_ex

        req = OrderRequest(
            symbol="BTC/USDT", market_type="usdt_futures",
            side="buy", order_type="market", quantity=0.01,
            leverage=1,
        )
        try:
            await BinanceClient.__new__(BinanceClient).place_order(req)
        except Exception:
            pass
        mock_sl.assert_called()


# ---------------------------------------------------------------------------
# #1792 — Pre-submit leverage timeout must NOT produce submission_unknown
# ---------------------------------------------------------------------------

def test_okx_leverage_timeout_not_submission_unknown(tmp_path):
    """OKX: leverage timeout before place_order → error, NOT submission_unknown (#1792)."""
    import aletheia.execution.okx_broker as broker
    from aletheia.storage.models import Order

    fake_order = MagicMock(spec=Order)
    fake_order.id = "order-123"
    fake_order.execution_mode = "okx_demo"

    inserted = {}
    updated = {}

    def fake_insert(o):
        inserted["order"] = o

    def fake_update(order_id, **kwargs):
        updated.update(kwargs)
        updated["order_id"] = order_id

    with patch.object(broker, "_guard_unresolved_submission"), \
         patch.object(broker, "_get_demo_symbols", return_value=None), \
         patch("aletheia.execution.okx_broker.OKXClient") as MockClient, \
         patch("aletheia.execution.okx_broker.insert_order", side_effect=lambda o: setattr(o, "id", "order-123")), \
         patch("aletheia.execution.okx_broker.update_order", side_effect=fake_update), \
         patch("aletheia.execution.okx_broker.write_post_commit_audit"), \
         patch("aletheia.execution.okx_broker.okx_ccxt_symbol", return_value="BTC-USDT-SWAP"):

        mock_client = MagicMock()
        # set_leverage times out
        mock_client.set_leverage = AsyncMock(side_effect=TimeoutError("leverage timeout"))
        mock_client.place_order = AsyncMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        with pytest.raises(TimeoutError):
            broker.execute_live_order(
                proposal_id="prop-1",
                risk_decision_id="risk-1",
                symbol="BTC/USDT",
                market_type="usdt_futures",
                direction="long",
                order_type="market",
                quantity=0.01,
                price=65000.0,
                leverage=5,
            )

        # Must NOT have called place_order
        mock_client.place_order.assert_not_called()
        # Status must be "error", not "submission_unknown"
        assert updated.get("status") == "error"
        assert updated.get("error_reason") == "leverage_setup_failed_before_submission"


def test_binance_leverage_timeout_not_submission_unknown(tmp_path):
    """Binance: leverage timeout before place_order → error, NOT submission_unknown (#1792)."""
    import aletheia.execution.binance_broker as broker

    updated = {}

    def fake_update(order_id, **kwargs):
        updated.update(kwargs)

    with patch.object(broker, "_guard_unresolved_submission"), \
         patch("aletheia.execution.binance_broker.BinanceClient") as MockClient, \
         patch("aletheia.execution.binance_broker.insert_order", side_effect=lambda o: setattr(o, "id", "order-456")), \
         patch("aletheia.execution.binance_broker.update_order", side_effect=fake_update), \
         patch("aletheia.execution.binance_broker.write_post_commit_audit"), \
         patch("aletheia.execution.binance_broker.to_ccxt_perp", return_value="BTC/USDT:USDT"):

        mock_client = MagicMock()
        mock_client.set_leverage = AsyncMock(side_effect=TimeoutError("leverage timeout"))
        mock_client.place_order = AsyncMock()
        mock_client.close = AsyncMock()
        MockClient.return_value = mock_client

        with pytest.raises(TimeoutError):
            broker.execute_live_order(
                proposal_id="prop-2",
                risk_decision_id="risk-2",
                symbol="BTC/USDT",
                market_type="usdt_futures",
                direction="long",
                order_type="market",
                quantity=0.01,
                price=65000.0,
                leverage=5,
            )

        mock_client.place_order.assert_not_called()
        assert updated.get("status") == "error"
        assert updated.get("error_reason") == "leverage_setup_failed_before_submission"


# ---------------------------------------------------------------------------
# #1481 — Non-finite venue metadata → ConstraintUnavailable
# ---------------------------------------------------------------------------

def test_nan_tick_size_raises_constraint_unavailable():
    """NaN metadata value must raise ConstraintUnavailable before order placement (#1481)."""
    from aletheia.execution.order_constraints import constraints_from_market, ConstraintUnavailable

    market = {
        "contractSize": 1,
        "limits": {
            "amount": {"min": float("nan"), "max": 10000},
            "cost": {"min": None},
        },
        "precision": {"amount": float("nan")},
    }

    with pytest.raises(ConstraintUnavailable):
        constraints_from_market(
            venue="okx", symbol="BTC/USDT", market_type="usdt_futures",
            market=market, precision_mode="tick_size",
        )


def test_inf_contract_size_raises_constraint_unavailable():
    """Infinite contractSize must raise ConstraintUnavailable (#1481)."""
    from aletheia.execution.order_constraints import constraints_from_market, ConstraintUnavailable

    market = {
        "contractSize": float("inf"),
        "limits": {"amount": {"min": 1, "max": None}, "cost": {}},
        "precision": {"amount": 0.01},
    }

    with pytest.raises(ConstraintUnavailable):
        constraints_from_market(
            venue="binance", symbol="BTC/USDT", market_type="usdt_futures",
            market=market, precision_mode="tick_size",
        )


def test_finite_metadata_does_not_raise():
    """Normal finite metadata must not raise (#1481 non-regression)."""
    from aletheia.execution.order_contracts import constraints_from_market  # noqa
    from aletheia.execution.order_constraints import constraints_from_market

    market = {
        "contractSize": 0.01,
        "limits": {"amount": {"min": 1, "max": 1000}, "cost": {"min": 5}},
        "precision": {"amount": 0.01},
    }
    c = constraints_from_market(
        venue="okx", symbol="BTC/USDT", market_type="usdt_futures",
        market=market, precision_mode="tick_size",
    )
    assert c.amount_step == Decimal("0.01") * Decimal("0.01")
