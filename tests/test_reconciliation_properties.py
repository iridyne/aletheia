"""Quantity-conservation properties for normalized reconciliation FillReports.

Actual reconciliation absence/failure behavior is covered by
``test_reconciliation.py`` and the pipeline specs, not an open-list predicate.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from aletheia.execution.reconciliation import (
    _filled_quantity_by_order,
)
from tests.fill_reports import reports


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# Order ids are short strings; empty/None are separately tested below.
_order_ids = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=8,
)

_quantities = st.floats(
    min_value=1e-9, max_value=1e6, allow_nan=False, allow_infinity=False
)




def _fill_row(order_id: str, quantity: float):
    """Build one normalized FillReport through the Bitget extension boundary."""
    return reports([
        {
            "orderId": order_id,
            "tradeId": f"fill-{order_id}-{quantity}",
            "fillPrice": "60000",
            "fillQuantity": str(quantity),
            "fee": "0",
            "feeCoin": "USDT",
            "cTime": "1789171200000",
        }
    ])[0]


_fill_rows = st.builds(_fill_row, _order_ids, _quantities)


# --------------------------------------------------------------------------- #
# Invariant 1: quantity conservation
# --------------------------------------------------------------------------- #


@given(rows=st.lists(_fill_rows, max_size=40))
@settings(max_examples=200, deadline=None)
def test_filled_quantity_is_invariant_under_permutation(rows) -> None:
    """Same normalized fills in a different order produce the same totals."""
    baseline = _filled_quantity_by_order(list(rows))
    reversed_view = _filled_quantity_by_order(list(reversed(rows)))
    assert baseline.keys() == reversed_view.keys()
    for order_id, total in baseline.items():
        assert math.isclose(total, reversed_view[order_id], rel_tol=1e-9, abs_tol=1e-9)


@given(rows=st.lists(_fill_rows, max_size=40))
@settings(max_examples=200, deadline=None)
def test_filled_quantity_equals_sum_of_valid_fills(rows) -> None:
    """The per-order total equals the sum of typed fill quantities."""
    expected: dict[str, float] = {}
    for row in rows:
        order_id = row.venue_order_id
        expected[order_id] = expected.get(order_id, 0.0) + float(row.quantity)
    actual = _filled_quantity_by_order(rows)
    assert actual.keys() == expected.keys()
    for order_id, total in expected.items():
        assert math.isclose(total, actual[order_id], rel_tol=1e-9, abs_tol=1e-9)


@given(rows=st.lists(_fill_rows, min_size=1, max_size=20))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
def test_splitting_a_fill_preserves_the_total(rows) -> None:
    """Splitting one normalized fill into two halves preserves its total."""
    for i, row in enumerate(rows):
        if row.quantity > 1e-6:
            half = float(row.quantity) / 2.0
            split = list(rows)
            split[i : i + 1] = [
                _fill_row(row.venue_order_id, half),
                _fill_row(row.venue_order_id, float(row.quantity) - half),
            ]
            before = _filled_quantity_by_order(rows)
            after = _filled_quantity_by_order(split)
            assert before.keys() == after.keys()
            for order_id, total in before.items():
                assert math.isclose(total, after[order_id], rel_tol=1e-9, abs_tol=1e-9)
            return
def test_filled_quantity_rejects_malformed_inputs() -> None:
    """Non-report inputs contribute zero; a valid report is counted."""
    assert _filled_quantity_by_order({"unrelated": [1, 2, 3]}) == {}
    assert _filled_quantity_by_order([None, {"fillQuantity": 1.0}]) == {}
    valid = _fill_row("A", 3.0)
    assert _filled_quantity_by_order([None, {"fillQuantity": 1.0}, valid]) == {
        "A": pytest.approx(3.0)
    }
