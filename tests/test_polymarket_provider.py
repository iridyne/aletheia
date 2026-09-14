"""Trust-boundary checks for normalized prediction probabilities."""

import pytest

from extensions.prediction.polymarket import _parse_market


@pytest.mark.parametrize("price", [None, -0.1, 1.1, float("nan"), float("inf"), True])
def test_market_without_a_valid_probability_is_not_a_zero_probability_fact(price):
    raw = {"question": "Fixture market"}
    if price is not None:
        raw["outcomePrices"] = [price]
    assert _parse_market(raw) is None


@pytest.mark.parametrize("price", [0.0, 0.5, 1.0])
@pytest.mark.parametrize("field", ["outcomePrices", "lastTradePrice"])
def test_valid_probability_endpoints_remain_observable(price, field):
    raw = {"question": "Fixture market", field: [price] if field == "outcomePrices" else price}
    market = _parse_market(raw)
    assert market is not None
    assert market.yes_probability == price
