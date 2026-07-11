"""Regression coverage for exchange-side order size rules."""

from decimal import Decimal
import time

import pytest


def _market(**overrides):
    market = {
        "active": True,
        "base": "BTC",
        "quote": "USDT",
        "spot": False,
        "swap": True,
        "contractSize": 0.001,
        "limits": {"amount": {"min": 1, "max": 1000}, "cost": {"min": 5}},
        "precision": {"amount": 1},
    }
    market.update(overrides)
    return market


def test_constraints_are_converted_from_contracts_to_internal_coin_units():
    from aletheia.execution.order_constraints import constraints_from_market

    constraints = constraints_from_market(
        venue="okx", symbol="BTC/USDT", market_type="usdt_futures",
        market=_market(), precision_mode="decimal_places",
    )

    assert constraints.min_quantity == Decimal("0.001")
    assert constraints.amount_step == Decimal("0.0001")
    assert constraints.min_notional == Decimal("5")


def test_bitget_spot_zero_min_amount_uses_its_published_minimum_notional():
    from aletheia.execution.order_constraints import constraints_from_market

    market = _market(
        spot=True, swap=False, contractSize=None,
        limits={"amount": {"min": 0, "max": None}, "cost": {"min": 5}},
        precision={"amount": 0.0001},
        info={"minTradeAmount": "0", "minTradeUSDT": "5", "quantityPrecision": "4"},
    )
    constraints = constraints_from_market(
        venue="bitget", symbol="BTC/USDT", market_type="spot", market=market,
        precision_mode="tick_size",
    )

    assert constraints.min_quantity == Decimal("0")
    assert constraints.amount_step == Decimal("0.0001")
    assert constraints.min_notional == Decimal("5")


def test_quantity_is_rounded_up_to_step_and_minimum_notional():
    from aletheia.execution.order_constraints import OrderConstraints, normalize_quantity

    constraints = OrderConstraints(
        venue="bitget", symbol="BTC/USDT", market_type="usdt_futures",
        min_quantity=Decimal("0.001"), max_quantity=None,
        amount_step=Decimal("0.001"), min_notional=Decimal("5"),
    )

    # 0.0011 must round up to 0.002, and $5 at $2,000 requires 0.003 BTC.
    assert normalize_quantity(0.0011, 2000, constraints) == 0.003


def test_missing_price_or_unsupported_precision_rejects_fail_closed():
    from aletheia.execution.order_constraints import (
        ConstraintUnavailable, constraints_from_market, normalize_quantity,
        OrderConstraints,
    )

    constraints = OrderConstraints(
        venue="bitget", symbol="BTC/USDT", market_type="spot",
        min_quantity=Decimal("0.001"), max_quantity=None,
        amount_step=Decimal("0.001"), min_notional=Decimal("5"),
    )
    with pytest.raises(ConstraintUnavailable, match="price"):
        normalize_quantity(0.001, 0, constraints)
    with pytest.raises(ConstraintUnavailable, match="precision"):
        constraints_from_market(
            venue="bitget", symbol="BTC/USDT", market_type="usdt_futures",
            market=_market(), precision_mode="significant_digits",
        )


def test_execution_mode_uses_its_own_venue_not_market_data_venue():
    from aletheia.execution.order_constraints import venue_for_execution_mode

    assert venue_for_execution_mode("bitget_demo") == "bitget"
    assert venue_for_execution_mode("okx_demo") == "okx"
    assert venue_for_execution_mode("binance_testnet") == "binance"
    assert venue_for_execution_mode("paper_internal") is None


def test_stale_rules_are_not_used_when_their_refresh_fails(monkeypatch):
    import aletheia.execution.order_constraints as rules

    constraints = rules.OrderConstraints(
        venue="bitget", symbol="BTC/USDT", market_type="spot",
        min_quantity=Decimal("0"), max_quantity=None,
        amount_step=Decimal("0.0001"), min_notional=Decimal("5"),
    )
    key = ("bitget", "BTC/USDT", "spot")
    monkeypatch.setattr(rules, "_cache", {key: (constraints, time.monotonic() - 301)})

    async def unavailable(*args):
        raise rules.ConstraintUnavailable("refresh failed")

    monkeypatch.setattr(rules, "_fetch_constraints", unavailable)
    with pytest.raises(rules.ConstraintUnavailable, match="refresh failed"):
        rules.get_order_constraints("bitget_demo", "BTC/USDT", "spot")
