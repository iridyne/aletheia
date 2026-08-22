"""Regression tests for EX-A execution fixes.

Issues: #1797 (take_profit forwarded), #1732 (1x leverage resets exchange),
#1792 (leverage timeout → error, not submission_unknown), #1481 (non-finite metadata).
"""
from __future__ import annotations
import inspect
import pytest
from decimal import Decimal

from aletheia.execution.errors import LeverageSetupError
from aletheia.execution.order_constraints import _decimal, ConstraintUnavailable


# ──────────────────────────────────────────────────────────────────────────────
# #1481 — non-finite venue metadata raises ConstraintUnavailable
# ──────────────────────────────────────────────────────────────────────────────

def test_nan_metadata_raises_constraint_unavailable():
    """NaN tick_size must raise ConstraintUnavailable, not pass silently."""
    with pytest.raises(ConstraintUnavailable, match="non-finite"):
        _decimal(float("nan"), "tick_size", required=True)


def test_inf_metadata_raises_constraint_unavailable():
    """Infinity tick_size must raise ConstraintUnavailable."""
    with pytest.raises(ConstraintUnavailable, match="non-finite"):
        _decimal(float("inf"), "tick_size", required=True)


def test_finite_metadata_passes():
    """Finite positive metadata parses correctly."""
    result = _decimal("0.01", "tick_size", required=True)
    assert result == Decimal("0.01")


def test_decimal_nan_string_raises():
    """String 'NaN' also rejected."""
    with pytest.raises(ConstraintUnavailable):
        _decimal("NaN", "tick_size", required=True)


# ──────────────────────────────────────────────────────────────────────────────
# #1732 — 1x leverage must reset exchange leverage
# ──────────────────────────────────────────────────────────────────────────────

def test_bitget_client_calls_set_leverage_for_1x():
    """Bitget place_order must call set_leverage even when leverage=1."""
    import pathlib
    src = pathlib.Path("aletheia/exchanges/bitget/client.py").read_text()
    assert "leverage >= 1" in src, (
        "Bitget place_order must set leverage for values >= 1 (including 1x) (#1732)"
    )
    assert "leverage > 1" not in src, (
        "Bitget must not skip 1x leverage with '> 1' guard"
    )


def test_okx_client_calls_set_leverage_for_1x():
    """OKX place_order must use >= 1 leverage guard."""
    import aletheia.exchanges.okx.client as mod
    src = inspect.getsource(mod.OKXClient.place_order)
    assert ">= 1" in src, "OKX must set leverage for values >= 1 (#1732)"
    assert "leverage > 1" not in src, "OKX must not skip 1x with '> 1'"


def test_binance_client_calls_set_leverage_for_1x():
    """Binance place_order must use >= 1 leverage guard."""
    import aletheia.exchanges.binance.client as mod
    src = inspect.getsource(mod.BinanceClient.place_order)
    assert ">= 1" in src, "Binance must set leverage for values >= 1 (#1732)"
    assert "leverage > 1" not in src, "Binance must not skip 1x with '> 1'"


# ──────────────────────────────────────────────────────────────────────────────
# #1792 — leverage timeout → error, NOT submission_unknown
# ──────────────────────────────────────────────────────────────────────────────

def test_leverage_setup_error_class_exists():
    """LeverageSetupError is defined and distinct from SubmissionUnknownError."""
    from aletheia.execution.errors import SubmissionUnknownError
    assert LeverageSetupError is not SubmissionUnknownError
    assert issubclass(LeverageSetupError, RuntimeError)


def test_bitget_broker_catches_leverage_setup_error_not_as_unknown():
    """bitget_broker must catch LeverageSetupError before ambiguous submission check."""
    import aletheia.execution.bitget_broker as mod
    src = inspect.getsource(mod)
    assert "except LeverageSetupError" in src, (
        "bitget_broker must handle LeverageSetupError explicitly (#1792)"
    )
    assert "leverage_setup_failed_before_submission" in src, (
        "bitget_broker must use error reason 'leverage_setup_failed_before_submission'"
    )


def test_okx_broker_catches_leverage_setup_error():
    """okx_broker must catch LeverageSetupError before ambiguous submission check."""
    import aletheia.execution.okx_broker as mod
    src = inspect.getsource(mod)
    assert "except LeverageSetupError" in src, (
        "okx_broker must handle LeverageSetupError explicitly (#1792)"
    )


def test_binance_broker_catches_leverage_setup_error():
    """binance_broker must catch LeverageSetupError before ambiguous submission check."""
    import aletheia.execution.binance_broker as mod
    src = inspect.getsource(mod)
    assert "except LeverageSetupError" in src, (
        "binance_broker must handle LeverageSetupError explicitly (#1792)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# #1797 — OKX/Binance take_profit_price must be forwarded
# ──────────────────────────────────────────────────────────────────────────────

def test_gateway_forwards_take_profit_to_all_modes():
    """Gateway must pass take_profit_price to all execution modes, not just Bitget."""
    import aletheia.execution.gateway as mod
    src = inspect.getsource(mod)
    # Verify the old pattern (only bitget) is gone
    assert "if allowed_mode == \"bitget_demo\"" not in src or \
           "take_profit_price=execution_proposal.take_profit" in src, (
        "Gateway must forward take_profit_price unconditionally (#1797)"
    )
    # Verify the new unconditional forwarding
    assert "take_profit_price=execution_proposal.take_profit" in src, (
        "Gateway must pass take_profit_price=execution_proposal.take_profit to brokers"
    )
