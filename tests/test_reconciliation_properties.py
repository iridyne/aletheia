"""Property-based invariants for the pure core of ``execution.reconciliation``.

Line/branch coverage tells us a branch was taken, not that the answer is
correct. For the reconciler two invariants must hold or state silently
diverges from the exchange:

1. **Quantity conservation.** For any bag of fill rows,
   ``_filled_quantity_by_order`` must produce a per-order total that is
   (a) invariant under permutation of the input rows and
   (b) equal to the sum of the per-fill quantities we recognise as valid.
   A split, reordered, or duplicated fill event must not change the total.

2. **CR-EX-01 monotonicity.** ``resolve_orders_missing_from_exchange`` is
   a pure set predicate: an order is resolved iff its exchange id is
   *not* in the "still open" set. Adding an id to that set can only
   remove orders from the resolved list, never add one. Removing an id
   can only add orders, never remove one. This is what "no look-ahead"
   means at the reconciler boundary.

Both properties are stated with Hypothesis so shuffled / duplicated /
partial-fill sequences are explored automatically. The suite is scoped
to the two pure functions on purpose: reconciler I/O (DB, ccxt) is
already exercised by ``test_reconciliation.py`` and the pipeline specs.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from aletheia.execution.reconciliation import (
    _OPEN_LOCAL_STATES,
    _filled_quantity_by_order,
    resolve_orders_missing_from_exchange,
)

# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #

# Order ids are short strings; empty/None are separately tested below.
_order_ids = st.text(
    alphabet=st.characters(min_codepoint=48, max_codepoint=122),
    min_size=1,
    max_size=8,
)

# Non-negative finite quantities. We deliberately allow 0 and very small
# values to exercise the ``max(quantity, 0.0)`` clamp and float sums.
_quantities = st.floats(
    min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False
)


def _fill_row(order_id: str, quantity: float) -> dict:
    """Build a fill row using one of the accepted quantity aliases.

    Kept minimal: the function only reads a fixed set of keys, so a
    representative pick per row is enough to explore branches.
    """
    return {"orderId": order_id, "fillQuantity": quantity}


_fill_rows = st.builds(_fill_row, _order_ids, _quantities)


# --------------------------------------------------------------------------- #
# Invariant 1: quantity conservation
# --------------------------------------------------------------------------- #


@given(rows=st.lists(_fill_rows, max_size=40))
@settings(max_examples=200, deadline=None)
def test_filled_quantity_is_invariant_under_permutation(rows: list[dict]) -> None:
    """Same fills in a different order must produce the same totals."""
    baseline = _filled_quantity_by_order(list(rows))
    reversed_view = _filled_quantity_by_order(list(reversed(rows)))
    assert baseline.keys() == reversed_view.keys()
    for order_id, total in baseline.items():
        assert math.isclose(total, reversed_view[order_id], rel_tol=1e-9, abs_tol=1e-9)


@given(rows=st.lists(_fill_rows, max_size=40))
@settings(max_examples=200, deadline=None)
def test_filled_quantity_equals_sum_of_valid_fills(rows: list[dict]) -> None:
    """The per-order total must equal the naive sum of clamped quantities."""
    expected: dict[str, float] = {}
    for row in rows:
        expected[row["orderId"]] = expected.get(row["orderId"], 0.0) + max(
            row["fillQuantity"], 0.0
        )
    actual = _filled_quantity_by_order(rows)
    assert actual.keys() == expected.keys()
    for order_id, total in expected.items():
        assert math.isclose(total, actual[order_id], rel_tol=1e-9, abs_tol=1e-9)


@given(rows=st.lists(_fill_rows, min_size=1, max_size=20))
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
def test_splitting_a_fill_preserves_the_total(rows: list[dict]) -> None:
    """Splitting one fill into two halves must not change the total.

    This is the property a partial-fill re-run would break: if the
    reconciler double-counts on split, this test surfaces it.
    """
    # Pick the first row with a non-trivial quantity to split.
    for i, row in enumerate(rows):
        if row["fillQuantity"] > 1e-6:
            half = row["fillQuantity"] / 2.0
            split = list(rows)
            split[i : i + 1] = [
                _fill_row(row["orderId"], half),
                _fill_row(row["orderId"], row["fillQuantity"] - half),
            ]
            before = _filled_quantity_by_order(rows)
            after = _filled_quantity_by_order(split)
            assert before.keys() == after.keys()
            for order_id, total in before.items():
                assert math.isclose(
                    total, after[order_id], rel_tol=1e-9, abs_tol=1e-9
                )
            return
    # No non-trivial row to split; nothing to assert (Hypothesis will find
    # another example next iteration).


def test_filled_quantity_rejects_malformed_inputs() -> None:
    """Non-list / non-dict / missing-id / negative rows must contribute zero."""
    # A bare dict without the fillList/list envelope: empty.
    assert _filled_quantity_by_order({"unrelated": [1, 2, 3]}) == {}
    # None row and a row with no id are both silently ignored.
    assert _filled_quantity_by_order([None, {"fillQuantity": 1.0}]) == {}
    # Negative and non-numeric quantities collapse to zero contribution.
    result = _filled_quantity_by_order(
        [
            {"orderId": "A", "fillQuantity": -5.0},
            {"orderId": "A", "fillQuantity": "not-a-number"},
            {"orderId": "A", "fillQuantity": 3.0},
        ]
    )
    assert result == {"A": pytest.approx(3.0)}


# --------------------------------------------------------------------------- #
# Invariant 2: CR-EX-01 monotonicity (no look-ahead)
# --------------------------------------------------------------------------- #

_open_state = st.sampled_from(_OPEN_LOCAL_STATES)
_closed_state = st.sampled_from(
    ("filled", "cancelled", "rejected", "closed_verified")
)


@st.composite
def _local_order(draw) -> dict:
    return {
        "execution_mode": draw(
            st.sampled_from(("okx_live_limited", "binance_live_limited", "paper"))
        ),
        "status": draw(st.one_of(_open_state, _closed_state)),
        "exchange_order_id": draw(
            st.one_of(st.none(), st.just(""), _order_ids)
        ),
    }


_local_orders_list = st.lists(_local_order(), max_size=15)
_open_id_set = st.sets(_order_ids, max_size=15)


@given(orders=_local_orders_list, open_ids=_open_id_set, extra=_order_ids)
@settings(max_examples=200, deadline=None)
def test_adding_an_open_id_only_shrinks_resolved_set(
    orders: list[dict], open_ids: set[str], extra: str
) -> None:
    """Marking one more order as still-open at the exchange must not
    cause a previously-unresolved order to become resolved."""
    before = set(resolve_orders_missing_from_exchange(orders, open_ids))
    after = set(resolve_orders_missing_from_exchange(orders, open_ids | {extra}))
    assert after.issubset(before)


@given(orders=_local_orders_list, open_ids=_open_id_set)
@settings(max_examples=200, deadline=None)
def test_resolved_orders_never_appear_in_exchange_open_set(
    orders: list[dict], open_ids: set[str]
) -> None:
    """An order the reconciler resolves must not simultaneously be open
    on the exchange. This is the core CR-EX-01 safety invariant."""
    resolved = resolve_orders_missing_from_exchange(orders, open_ids)
    for exchange_id in resolved:
        assert exchange_id not in open_ids


@given(orders=_local_orders_list, open_ids=_open_id_set)
@settings(max_examples=200, deadline=None)
def test_every_resolved_id_is_backed_by_a_qualifying_order(
    orders: list[dict], open_ids: set[str]
) -> None:
    """Every id in the resolved list must be produced by *some* order
    that matches all four criteria: configured execution mode, locally
    open status, non-empty exchange_order_id, and not present in the
    exchange's open set. This guards against a config typo that would
    accidentally converge paper orders and against silent drift in the
    resolver predicate."""
    modes = ("okx_live_limited",)
    resolved = resolve_orders_missing_from_exchange(orders, open_ids, modes=modes)
    for eid in resolved:
        assert any(
            order["execution_mode"] in modes
            and order["status"] in _OPEN_LOCAL_STATES
            and order.get("exchange_order_id") == eid
            and eid not in open_ids
            for order in orders
        ), f"resolved id {eid!r} has no qualifying order"


def test_resolve_is_idempotent_on_a_frozen_snapshot() -> None:
    """Running the resolver twice on the same inputs is a no-op — the
    resolved list is a pure function of ``(orders, open_ids)``."""
    orders = [
        {
            "execution_mode": "okx_live_limited",
            "status": "submitted",
            "exchange_order_id": "X1",
        },
        {
            "execution_mode": "okx_live_limited",
            "status": "submitted",
            "exchange_order_id": "X2",
        },
    ]
    first = resolve_orders_missing_from_exchange(orders, {"X2"})
    second = resolve_orders_missing_from_exchange(orders, {"X2"})
    assert first == second == ["X1"]
