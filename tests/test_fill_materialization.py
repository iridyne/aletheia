"""Regression tests for fill materialization fail-closed fixes.

Issues: #1722 (non-finite fill economics), #1752 (BGB/BNB fees), #1502 (explicit
venue rejection), #1553 (late broker ack regression).
"""
from __future__ import annotations
import math
import pytest
from unittest.mock import MagicMock, patch

from aletheia.execution.exchange_materialization import (
    MaterializationIntegrityError,
    _fee_in_quote,
    _positive_float,
    parse_bitget_private_fills,
)


# ──────────────────────────────────────────────────────────────────────────────
# #1722 — non-finite fill economics must be rejected
# ──────────────────────────────────────────────────────────────────────────────

def test_positive_float_nan_returns_none():
    """NaN must not enter fill economics — returns None so the row is skipped."""
    assert _positive_float(float("nan")) is None


def test_positive_float_inf_returns_none():
    """Infinity must not enter fill economics — returns None so the row is skipped."""
    assert _positive_float(float("inf")) is None


def test_positive_float_finite_ok():
    """Finite positive values pass through normally."""
    assert _positive_float(100.5) == pytest.approx(100.5)
    assert _positive_float(0) is None  # zero is not positive


def test_parse_bitget_nan_price_row_is_skipped():
    """A fill row with NaN price is silently skipped (not exception), valid rows still parsed."""
    nan_row = {
        "tradeId": "t_bad",
        "orderId": "o1",
        "fillPrice": float("nan"),
        "fillQuantity": 1.0,
        "fee": "0.1",
        "feeCoin": "USDT",
        "cTime": 1700000000000,
    }
    good_row = {
        "tradeId": "t_good",
        "orderId": "o2",
        "fillPrice": 2000.0,
        "fillQuantity": 0.5,
        "fee": "0.1",
        "feeCoin": "USDT",
        "cTime": 1700000001000,
    }
    result = parse_bitget_private_fills([nan_row, good_row])
    assert len(result) == 1
    assert result[0].fill_id == "t_good"


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
    """#1502: okx_broker.execute_live_order contains explicit-rejection branch."""
    import inspect
    import aletheia.execution.okx_broker as mod
    src = inspect.getsource(mod.execute_live_order)
    assert "explicit_venue_rejection" in src, (
        "okx_broker.execute_live_order must handle explicit venue cancellation (#1502)"
    )
    assert "'cancelled', 'rejected', 'expired'" in src or \
           '"cancelled", "rejected", "expired"' in src, (
        "okx_broker must check for explicit terminal statuses from venue"
    )


def test_explicit_cancellation_branch_in_binance_broker():
    """#1502: binance_broker.execute_live_order contains explicit-rejection branch."""
    import inspect
    import aletheia.execution.binance_broker as mod
    src = inspect.getsource(mod.execute_live_order)
    assert "explicit_venue_rejection" in src, (
        "binance_broker.execute_live_order must handle explicit venue cancellation (#1502)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# #1553 — source-level contract: late ack guard present
# ──────────────────────────────────────────────────────────────────────────────

def test_late_ack_guard_in_okx_broker():
    """#1553: okx_broker checks current Order state before applying ack."""
    import inspect
    import aletheia.execution.okx_broker as mod
    src = inspect.getsource(mod.execute_live_order)
    assert "discarding late ack" in src, (
        "okx_broker must discard late ack for already-reconciled Orders (#1553)"
    )
    assert "submission_unknown" in src and "_UNRESOLVED" in src, (
        "okx_broker must define _UNRESOLVED guard before updating Order state"
    )


def test_late_ack_guard_in_binance_broker():
    """#1553: binance_broker checks current Order state before applying ack."""
    import inspect
    import aletheia.execution.binance_broker as mod
    src = inspect.getsource(mod.execute_live_order)
    assert "discarding late ack" in src, (
        "binance_broker must discard late ack for already-reconciled Orders (#1553)"
    )
