"""Regression tests for fill materialization fail-closed fixes.

Issues: #1722 (non-finite fill economics), #1752 (BGB/BNB fees), #1502 (explicit
venue rejection), #1553 (late broker ack regression).
"""
from __future__ import annotations

import pytest

from aletheia.execution.exchange_materialization import (
    MaterializationIntegrityError,
    _fill_totals,
    _positive_float,
    evidence_for_order,
)
from aletheia.portfolio.pnl import fee_in_quote


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
# #2041 — BGB/BNB quote conversion remains unavailable
# ──────────────────────────────────────────────────────────────────────────────

def test_fee_in_quote_bgb_is_unavailable():
    """BGB fee (not base/quote) cannot become a fabricated quote zero."""
    result = fee_in_quote(
        fee=0.5, fee_currency="BGB", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result is None


def test_fee_in_quote_bnb_is_unavailable():
    """BNB fee on BTC/USDT cannot become a fabricated quote zero."""
    result = fee_in_quote(
        fee=0.001, fee_currency="BNB", fill_price=50000.0, symbol="BTC/USDT"
    )
    assert result is None


def test_third_currency_fee_keeps_aggregate_quote_cost_unavailable():
    """Raw fee evidence may exist while quote-currency economics remain NULL."""
    assert _fill_totals(
        [{
            "fill_price": 2000.0,
            "fill_quantity": 0.5,
            "fee": 0.5,
            "fee_currency": "BGB",
        }],
        symbol="ETH/USDT",
    ) == (0.5, 1000.0, None)


def test_fee_in_quote_usdt_passes_through():
    """USDT fee on ETH/USDT passes through unchanged."""
    result = fee_in_quote(
        fee=1.5, fee_currency="USDT", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result == pytest.approx(1.5)


def test_fee_in_quote_base_asset_converted():
    """Base-asset fee (ETH fee on ETH/USDT) converted via fill price."""
    result = fee_in_quote(
        fee=0.001, fee_currency="ETH", fill_price=2000.0, symbol="ETH/USDT"
    )
    assert result == pytest.approx(2.0)  # 0.001 * 2000


@pytest.mark.parametrize("fee,currency,expected", [(None, "USDT", None), (0.5, "", None), (0.0, "", 0.0)])
def test_unknown_fee_is_not_zero_or_assumed_quote(fee, currency, expected):
    assert fee_in_quote(fee=fee, fee_currency=currency, fill_price=2000.0, symbol="ETH/USDT") == expected
    assert _fill_totals([
        {"fill_price": 2000.0, "fill_quantity": 0.5, "fee": fee, "fee_currency": currency},
    ], symbol="ETH/USDT") == (0.5, 1000.0, expected)


def test_private_fee_parser_preserves_missing_and_rejects_mixed_amounts():
    from aletheia.execution.exchange_materialization import _fee_from_row

    assert _fee_from_row({"feeCoin": "USDT"}) == (None, "USDT")
    assert _fee_from_row({"fee": "0.1"}) == (0.1, "")
    assert _fee_from_row({"feeDetail": [{"feeCoin": "USDT"}]}) == (None, "USDT")
    assert _fee_from_row({"feeDetail": [{"totalFee": "0.0", "feeCoin": "USDT"}]}) == (0.0, "USDT")
    with pytest.raises(MaterializationIntegrityError, match="ambiguous fee total"):
        _fee_from_row({"feeDetail": [
            {"totalFee": "1", "feeCoin": "USDT"}, {"totalFee": "2", "feeCoin": "BGB"},
        ]})


def test_durable_unknown_fee_replay_cannot_be_substituted_with_zero():
    from aletheia.execution.exchange_materialization import PrivateFill, _verify_durable_fills

    evidence = PrivateFill("fill", "order", None, 100.0, 1.0, None, "USDT", "2026-09-12T00:00:00+00:00", 1789171200000)
    local = {"id": "fill", "fill_quantity": 1.0, "fill_price": 100.0, "fee": None,
             "fee_currency": "USDT", "filled_at": evidence.filled_at}
    _verify_durable_fills(None, [local], [evidence], quantity_tolerance=1e-9,
                          lifecycle="close", execution_mode="bitget_demo", upgrade_source=False)
    local["fee"] = 0.0
    with pytest.raises(MaterializationIntegrityError, match="conflicts"):
        _verify_durable_fills(None, [local], [evidence], quantity_tolerance=1e-9,
                              lifecycle="close", execution_mode="bitget_demo", upgrade_source=False)


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
