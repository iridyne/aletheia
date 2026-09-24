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
    from extensions.exchanges.ccxt.order_rules import constraints_from_market

    constraints = constraints_from_market(
        venue="okx", symbol="BTC/USDT", market_type="usdt_futures",
        market=_market(), precision_mode="decimal_places",
    )

    assert constraints.min_quantity == Decimal("0.001")
    assert constraints.amount_step == Decimal("0.0001")
    assert constraints.min_notional == Decimal("5")


def test_bitget_spot_zero_min_amount_uses_its_published_minimum_notional():
    from extensions.exchanges.ccxt.order_rules import constraints_from_market

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
    from extensions.exchanges.ccxt.order_rules import constraints_from_market

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

    monkeypatch.setattr(rules, "_constraints_reader", unavailable)
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


def test_core_rules_use_bound_reader_and_absence_clears_cached_authority(monkeypatch):
    from aletheia.execution import order_constraints as rules

    monkeypatch.setattr(rules, "_cache", {})
    monkeypatch.setattr(rules, "_constraints_reader", None, raising=False)
    calls = []

    async def read(execution_mode, venue, symbol, market_type):
        calls.append((execution_mode, venue, symbol, market_type))
        return rules.OrderConstraints(
            venue, symbol, market_type, Decimal("0.001"), None,
            Decimal("0.001"), Decimal("5"),
        )

    rules.configure_order_constraints_reader(read)
    assert rules.prepare_exchange_quantity("bitget_demo", "BTC/USDT", "spot", 0.0011, 2000) == 0.003
    assert rules.get_order_constraints("bitget_demo", "BTC/USDT", "spot").venue == "bitget"
    assert calls == [("bitget_demo", "bitget", "BTC/USDT", "spot")]
    rules.configure_order_constraints_reader(None)
    with pytest.raises(rules.ConstraintUnavailable, match="unavailable"):
        rules.get_order_constraints("bitget_demo", "BTC/USDT", "spot")
    assert len(calls) == 1


def test_core_rules_do_not_construct_or_decode_ccxt():
    import ast
    from pathlib import Path
    from aletheia.execution import order_constraints as rules

    tree = ast.parse(Path(rules.__file__).read_text())
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name and name.startswith(("ccxt", "aletheia.net", "aletheia.exchanges", "extensions")) for name in imports)
    assert not hasattr(rules, "constraints_from_market")


@pytest.mark.anyio
@pytest.mark.parametrize("load_fails", [False, True])
async def test_exchange_reader_owns_client_and_session_cleanup(monkeypatch, load_fails):
    from types import SimpleNamespace
    import ccxt
    from aletheia import net
    from extensions.exchanges.ccxt.order_rules import read_order_constraints
    from aletheia.execution.order_constraints import ConstraintUnavailable

    events = []

    async def load():
        events.append("load")
        if load_fails:
            raise TimeoutError("synthetic transport timeout")

    async def close_exchange():
        events.append("exchange_closed")

    async def close_session():
        events.append("session_closed")

    sandbox_calls = []

    def make(exchange_id, *, options):
        assert exchange_id == "binanceusdm"
        # The reader now applies the mode's listing view, so the fake must model
        # the same surface the real client exposes.
        assert options["defaultType"] == "swap"
        return SimpleNamespace(
            load_markets=load, close=close_exchange, precisionMode=ccxt.DECIMAL_PLACES,
            markets={"BTC/USDT:USDT": _market()},
            set_sandbox_mode=lambda value: sandbox_calls.append(value),
        ), SimpleNamespace(close=close_session)

    monkeypatch.setattr(net, "make_ccxt", make)
    if load_fails:
        with pytest.raises(ConstraintUnavailable) as raised:
            await read_order_constraints("binance_testnet", "binance", "BTC/USDT", "usdt_futures")
        assert isinstance(raised.value.__cause__, TimeoutError)
    else:
        result = await read_order_constraints("binance_testnet", "binance", "BTC/USDT", "usdt_futures")
        assert (result.venue, result.symbol, result.market_type) == ("binance", "BTC/USDT", "usdt_futures")
        assert result.min_quantity == Decimal("0.001")
        assert result.amount_step == Decimal("0.0001")
    assert events == ["load", "exchange_closed", "session_closed"]


def test_existing_exchange_composition_binds_rules_without_io(monkeypatch):
    from aletheia.execution import order_constraints as rules
    from extensions.exchanges import composition
    from extensions.exchanges.ccxt.order_rules import read_order_constraints

    monkeypatch.setattr(rules, "_constraints_reader", None)
    monkeypatch.setattr(rules, "_cache", {})
    monkeypatch.setattr(composition, "configure_uta_provider", lambda **kwargs: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("binding must not resolve credentials")

    monkeypatch.setattr(composition, "get_secret", forbidden)
    composition.bind_uta_provider()
    assert rules._constraints_reader is read_order_constraints


@pytest.mark.parametrize("case", ["dated_first", "dated_only", "perpetual_inactive"])
@pytest.mark.parametrize("allow_increase", [False, True])
async def test_gateway_quantity_rules_use_exact_perpetual_identity(monkeypatch, case, allow_increase):
    """Actual Bitget market parsing cannot substitute delivery minima for a swap."""
    from types import SimpleNamespace
    import ccxt.async_support as ccxt
    from aletheia import net
    from aletheia.execution import gateway, order_constraints as rules
    from extensions.exchanges.ccxt.order_rules import read_order_constraints

    exchange = ccxt.bitget({"options": {"fetchMarkets": {"types": ["swap"]}}})
    common = {
        "baseCoin": "BTC", "quoteCoin": "USDT", "supportMarginCoins": ["USDT"],
        "symbolStatus": "normal", "pricePlace": "1", "priceEndStep": "1",
        "volumePlace": "3", "sizeMultiplier": "0.001", "minTradeUSDT": "5",
    }
    raw = [{
        **common, "symbol": "BTCUSDT_261225", "symbolType": "delivery",
        "deliveryTime": "1798156800000",
        "minTradeNum": "1" if case == "dated_first" else "0.001",
    }]
    if case != "dated_only":
        raw.append({
            **common, "symbol": "BTCUSDT", "symbolType": "perpetual", "minTradeNum": "0.001",
            "symbolStatus": "offline" if case == "perpetual_inactive" else "normal",
        })

    async def contracts(params):
        return {"code": "00000", "data": raw if params["productType"] == "USDT-FUTURES" else []}

    exchange.publicMixGetV2MixMarketContracts = contracts
    try:
        exchange.set_markets(await exchange.fetch_default_markets({}))
        markets = exchange.markets
    finally:
        await exchange.close()
    assert next(iter(markets)) == "BTC/USDT:USDT-261225"
    assert markets["BTC/USDT:USDT-261225"]["future"] is True
    events = []

    async def load():
        events.append("load")

    async def close():
        events.append("close")

    monkeypatch.setattr(net, "make_ccxt", lambda *_a, **_kw: (SimpleNamespace(
        markets=markets, load_markets=load, close=close, precisionMode=ccxt.TICK_SIZE,
        set_sandbox_mode=lambda _value: None,
    ), None))
    monkeypatch.setattr(rules, "_constraints_reader", read_order_constraints)
    monkeypatch.setattr(rules, "_cache", {})
    quantity, error = gateway._prepare_exchange_quantity(
        "bitget_demo", "BTC/USDT", "usdt_futures", 0.002, 60000,
        allow_increase=allow_increase,
    )
    assert events == ["load", "close"]
    assert quantity == 0.002
    if case == "dated_first":
        assert error is None
    else:
        assert error is not None
        assert "No order was sent" in error


def test_rules_lookup_keeps_spot_distinct_from_same_pair_perpetual():
    from types import SimpleNamespace
    from extensions.exchanges.ccxt.order_rules import _find_market

    spot = _market(spot=True, swap=False, contractSize=None)
    exchange = SimpleNamespace(markets={"BTC/USDT:USDT": _market(), "BTC/USDT": spot})
    assert _find_market(exchange, "BTC/USDT", "spot") is spot


def test_demo_and_live_rules_are_not_shared_across_one_venue(monkeypatch):
    """A demo order must not be validated against the live listing set.

    The cache key was the venue, so bitget_demo and live bitget shared one entry,
    and the reader built a plain production client. RENDER is listed live but
    absent from the Bitget demo market map, so it passed pre-trade validation and
    then died at submission with `contract_size_unavailable` — a fail-open gap the
    order could only discover at the exchange.
    """
    from aletheia.execution import order_constraints as rules

    monkeypatch.setattr(rules, "_cache", {})
    monkeypatch.setattr(rules, "_constraints_reader", None, raising=False)
    seen = []

    async def read(execution_mode, venue, symbol, market_type):
        seen.append(execution_mode)
        return rules.OrderConstraints(
            venue, symbol, market_type, Decimal("0.001"), None,
            Decimal("0.001"), Decimal("5"),
        )

    rules.configure_order_constraints_reader(read)
    rules.get_order_constraints("bitget_demo", "RENDER/USDT", "usdt_futures")
    rules.get_order_constraints("bitget_live_limited", "RENDER/USDT", "usdt_futures")

    # Two modes on one venue must each consult the reader for their own listing.
    assert seen == ["bitget_demo", "bitget_live_limited"]


async def test_reader_applies_the_execution_mode_listing_view(monkeypatch):
    """The pre-trade client must match submission's sandbox and options."""
    import ccxt
    from types import SimpleNamespace

    from aletheia import net
    from extensions.exchanges.ccxt.order_rules import read_order_constraints

    seen: dict[str, object] = {}

    async def load():
        return {}

    async def close():
        return None

    def make(exchange_id, *, options):
        seen["options"] = dict(options)
        return SimpleNamespace(
            load_markets=load,
            close=close,
            precisionMode=ccxt.TICK_SIZE,
            markets={"RENDER/USDT:USDT": _market()},
            set_sandbox_mode=lambda value: seen.__setitem__("sandbox", value),
        ), None

    monkeypatch.setattr(net, "make_ccxt", make)
    await read_order_constraints("bitget_demo", "bitget", "RENDER/USDT", "usdt_futures")

    # bitget_demo profiles declare sandbox=True and carry a uta listing option;
    # both must reach the client that answers the pre-trade question.
    assert seen.get("sandbox") is True
    assert "uta" in seen["options"]
