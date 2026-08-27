"""Regression tests for fill materialization fail-closed fixes.

Issues: #1722 (non-finite fill economics), #1752 (BGB/BNB fees), #1502 (explicit
venue rejection), #1553 (late broker ack regression).
"""
from __future__ import annotations

import pytest

from aletheia.execution.exchange_materialization import (
    MaterializationIntegrityError,
    _fee_in_quote,
    _fill_totals,
    _positive_float,
    evidence_for_order,
)


# ──────────────────────────────────────────────────────────────────────────────
# #1722 — non-finite fill economics must be rejected
# ──────────────────────────────────────────────────────────────────────────────

def test_positive_float_nan_returns_none():
    """NaN must not enter fill economics."""
    assert _positive_float(float("nan")) is None


def test_positive_float_inf_returns_none():
    """Infinity must not enter fill economics."""
    assert _positive_float(float("inf")) is None


def test_positive_float_finite_ok():
    """Finite positive values pass through normally."""
    assert _positive_float(100.5) == pytest.approx(100.5)
    assert _positive_float(0) is None  # zero is not positive


@pytest.mark.parametrize("field", ["fillPrice", "fillQuantity", "fee"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_order_matched_nonfinite_private_evidence_is_rejected(field, value):
    """Every non-finite candidate-order economic field invalidates its batch."""
    row = {
        "tradeId": "t_bad",
        "orderId": "o1",
        "fillPrice": "2000.0",
        "fillQuantity": "0.5",
        "fee": "0.1",
        "feeCoin": "USDT",
        "cTime": "1700000000000",
    }
    row[field] = value

    with pytest.raises(MaterializationIntegrityError, match="private fill row"):
        evidence_for_order({"exchange_order_id": "o1"}, [row])


def test_unrelated_nonfinite_private_evidence_does_not_block_exact_order():
    """A malformed row for another order is outside the candidate evidence batch."""
    unrelated = {
        "tradeId": "t_bad",
        "orderId": "o2",
        "fillPrice": "NaN",
        "fillQuantity": "0.5",
        "fee": "0.1",
        "feeCoin": "USDT",
        "cTime": "1700000000000",
    }
    matched = {
        "tradeId": "t_good",
        "orderId": "o1",
        "fillPrice": "2000.0",
        "fillQuantity": "0.5",
        "fee": "0.1",
        "feeCoin": "USDT",
        "cTime": "1700000001000",
    }

    exchange_order_id, evidence = evidence_for_order(
        {"exchange_order_id": "o1"},
        [unrelated, matched],
    )

    assert exchange_order_id == "o1"
    assert [item.fill_id for item in evidence] == ["t_good"]


@pytest.mark.parametrize("field", ["fill_price", "fill_quantity", "fee"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_durable_private_fill_is_rejected_on_replay(field, value):
    """A persisted bad Fill cannot re-enter Position economics during replay."""
    row = {
        "fill_price": "2000.0",
        "fill_quantity": "0.5",
        "fee": "0.1",
        "fee_currency": "USDT",
    }
    row[field] = value

    with pytest.raises(MaterializationIntegrityError, match="durable private Fill"):
        _fill_totals([row], symbol="BTC/USDT")


# ──────────────────────────────────────────────────────────────────────────────
# #1752 — BGB/BNB fee does not block materialization
# ──────────────────────────────────────────────────────────────────────────────

def test_fee_in_quote_bgb_returns_zero():
    """BGB fee (not base/quote) returns 0.0, not exception."""
    result = _fee_in_quote(
        fee=0.5, fee_currency="BGB", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result == 0.0


def test_fee_in_quote_bnb_returns_zero():
    """BNB fee on BTC/USDT returns 0.0."""
    result = _fee_in_quote(
        fee=0.001, fee_currency="BNB", fill_price=50000.0, symbol="BTC/USDT"
    )
    assert result == 0.0


def test_fee_in_quote_usdt_passes_through():
    """USDT fee on ETH/USDT passes through unchanged."""
    result = _fee_in_quote(
        fee=1.5, fee_currency="USDT", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result == pytest.approx(1.5)


def test_fee_in_quote_base_asset_converted():
    """Base-asset fee (ETH fee on ETH/USDT) converted via fill price."""
    result = _fee_in_quote(
        fee=0.001, fee_currency="ETH", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result == pytest.approx(2.0)  # 0.001 * 2000


# ──────────────────────────────────────────────────────────────────────────────
# #1502 — explicit venue cancellation ≠ submission_unknown
# ──────────────────────────────────────────────────────────────────────────────

def test_explicit_cancellation_branch_in_okx_broker():
    """#1502: explicit terminal acknowledgements remain cancelled, not unknown."""
    from aletheia.execution.broker_utils import entry_acknowledgement_state

    assert entry_acknowledgement_state("cancelled") == (
        "cancelled",
        "explicit_venue_rejection",
    )
    assert entry_acknowledgement_state("rejected") == (
        "cancelled",
        "explicit_venue_rejection",
    )
    assert entry_acknowledgement_state("expired") == (
        "cancelled",
        "explicit_venue_rejection",
    )


def test_explicit_cancellation_branch_in_binance_broker():
    """#2043: unknown acknowledgements fail closed instead of submitted."""
    from aletheia.execution.broker_utils import entry_acknowledgement_state

    assert entry_acknowledgement_state("open") == ("submitted", None)
    assert entry_acknowledgement_state("partially_filled") == (
        "materialization_missing",
        "private_fill_evidence_pending",
    )
    assert entry_acknowledgement_state("new-venue-status") == (
        "submission_unknown",
        "unrecognized_venue_order_status",
    )
