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
    from extensions.exchanges.ccxt.order_constraints import constraints_from_market

    constraints = constraints_from_market(
        venue="okx", symbol="BTC/USDT", market_type="usdt_futures",
        market=_market(), precision_mode="decimal_places",
    )

    assert constraints.min_quantity == Decimal("0.001")
    assert constraints.amount_step == Decimal("0.0001")
    assert constraints.min_notional == Decimal("5")


def test_bitget_spot_zero_min_amount_uses_its_published_minimum_notional():
    from extensions.exchanges.ccxt.order_constraints import constraints_from_market

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
        ConstraintUnavailable, normalize_quantity,
        OrderConstraints,
    )
    from extensions.exchanges.ccxt.order_constraints import constraints_from_market

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


def test_no_extension_cannot_refresh_or_reuse_cached_constraints(monkeypatch):
    import aletheia.execution.order_constraints as rules

    monkeypatch.setattr(rules, "_constraint_reader", None)
    monkeypatch.setattr(rules, "_cache", {
        ("bitget", "BTC/USDT", "spot"): (object(), time.monotonic()),
    })

    def forbidden(*args, **kwargs):
        pytest.fail("Core must not construct a transport without an Extension")

    monkeypatch.setattr("aletheia.net.make_ccxt", forbidden)
    with pytest.raises(rules.ConstraintUnavailable, match="exchange_extension_unavailable"):
        rules.get_order_constraints("bitget_demo", "BTC/USDT", "spot")


def test_gateway_preparation_uses_core_error_facts_not_transport_names():
    from aletheia.execution.gateway import _preparation_dependency_unavailable
    from aletheia.execution.order_constraints import ConstraintUnavailable
    from aletheia.uta.errors import UTAError, UTAErrorCode

    class NetworkNamedBusinessError(ValueError):
        pass

    assert _preparation_dependency_unavailable(TimeoutError())
    assert _preparation_dependency_unavailable(ConnectionError())
    assert _preparation_dependency_unavailable(ConstraintUnavailable("read_failed", retryable=True))
    assert _preparation_dependency_unavailable(UTAError(UTAErrorCode.NETWORK, "read_failed", retryable=True))
    assert not _preparation_dependency_unavailable(UTAError(UTAErrorCode.AUTH, "auth_failed"))
    assert not _preparation_dependency_unavailable(NetworkNamedBusinessError())
    error = ConstraintUnavailable("invalid_rules")
    error.__cause__ = TimeoutError()
    assert not _preparation_dependency_unavailable(error)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, TimeoutError, ValueError])
@pytest.mark.parametrize("stage", ["factory", "load", "client_close", "session_close"])
async def test_ccxt_constraint_reader_normalizes_and_closes_loop_local_client(monkeypatch, failure, stage):
    from extensions.exchanges.ccxt.order_constraints import read_order_constraints
    import ccxt

    closed = []

    class Client:
        precisionMode = ccxt.DECIMAL_PLACES
        markets = {"BTC": _market()}

        async def load_markets(self):
            if failure and stage == "load":
                raise failure("synthetic transport details")

        async def close(self):
            closed.append("client")
            if failure and stage == "client_close":
                raise failure("synthetic transport details")

    class Session:
        async def close(self):
            closed.append("session")
            if failure and stage == "session_close":
                raise failure("synthetic transport details")

    calls = []

    def factory(venue, **kwargs):
        calls.append((venue, kwargs))
        if failure and stage == "factory":
            raise failure("synthetic transport details")
        return Client(), Session()

    monkeypatch.setattr("aletheia.net.make_ccxt", factory)
    if failure:
        from aletheia.execution.order_constraints import ConstraintUnavailable
        with pytest.raises(ConstraintUnavailable) as raised:
            await read_order_constraints("okx", "BTC/USDT", "usdt_futures")
        assert raised.value.retryable is (failure is TimeoutError)
        assert str(raised.value) == "executable_venue_rules_unavailable"
    else:
        result = await read_order_constraints("okx", "BTC/USDT", "usdt_futures")
        assert result.amount_step == Decimal("0.0001")
        assert result.venue == "okx"
    assert calls == [("okx", {"options": {"defaultType": "swap"}})]
    assert closed == ([] if failure and stage == "factory" else ["client", "session"])


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

    monkeypatch.setattr(rules, "_constraint_reader", unavailable)
    with pytest.raises(rules.ConstraintUnavailable, match="refresh failed"):
        rules.get_order_constraints("bitget_demo", "BTC/USDT", "spot")


def test_bounded_entry_quantity_rounds_down_without_crossing_venue_minima():
    from aletheia.execution.order_constraints import ConstraintUnavailable, OrderConstraints, normalize_quantity
    constraints = OrderConstraints(
        venue="bitget", symbol="BTC/USDT", market_type="usdt_futures",
        min_quantity=Decimal("0.001"), max_quantity=None,
        amount_step=Decimal("0.001"), min_notional=Decimal("5"),
    )
    assert normalize_quantity(0.0039, 2000, constraints, allow_increase=False) == 0.003
    with pytest.raises(ConstraintUnavailable, match="below exchange minimum"):
        normalize_quantity(0.0029, 2000, constraints, allow_increase=False)


def test_bounded_step_restores_only_one_ulp_of_division_noise():
    from aletheia.execution.order_constraints import ConstraintUnavailable, OrderConstraints, normalize_quantity
    constraints = OrderConstraints(
        venue="bitget", symbol="TEST/USDT", market_type="usdt_futures",
        min_quantity=Decimal("0.996"), max_quantity=None,
        amount_step=Decimal("0.001"), min_notional=Decimal("5"),
    )
    quantity = normalize_quantity(5.00988 / 5.03, 5.03, constraints, allow_increase=False)
    assert quantity == 0.996 and quantity * 5.03 <= 5.00988 + 1e-12
    with pytest.raises(ConstraintUnavailable, match="below exchange minimum"):
        normalize_quantity(0.995999999, 5.03, constraints, allow_increase=False)
