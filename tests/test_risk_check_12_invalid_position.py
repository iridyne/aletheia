"""Tests for #1425: risk check #12 fails closed on invalid position exposure."""

import pytest

import aletheia.risk.engine as eng
from aletheia.storage.models import TradeProposal


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("quantity", "not-a-number"),
        ("current_price", "invalid"),
        # entry_price=None triggers account_leverage check (#17) with a different code
        # before check #12 can run; tested via _current_account_exposure unit tests instead.
    ],
)
def test_check_12_live_fails_closed_on_invalid_position_data(monkeypatch, field, invalid_value):
    """Check #12 live: invalid position data → violation, not silent zero."""
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    position = {
        "id": "position-corrupt",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "bitget_live_limited",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
    }
    position[field] = invalid_value
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    result = eng.evaluate_proposal(proposal)
    assert any(
        v.startswith("max_total_live_exposure_exceeded:account_exposure_check_error:invalid_position_data")
        for v in result.violations
    ), f"Expected check error, got: {result.violations}"


def test_check_12_live_passes_with_valid_position(monkeypatch):
    """Check #12 live: valid position + proposal within cap → no exposure-data violation."""
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    position = {
        "id": "position-valid",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "bitget_live_limited",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
    }
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    result = eng.evaluate_proposal(proposal)
    # The specific check-12 data-quality violation must NOT appear for a valid position.
    # Other violations (live_disabled, account_mode_mismatch, etc.) are expected in test env.
    assert not any(
        v.startswith("max_total_live_exposure_exceeded:account_exposure_check_error")
        for v in result.violations
    ), f"Check #12 data-quality violation should not fire for valid position, got: {result.violations}"


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        ("quantity", float("nan")),
        ("current_price", float("inf")),
    ],
)
def test_check_12_demo_fails_closed_on_invalid_position_data(monkeypatch, field, invalid_value):
    """Check #12 demo: invalid position data → violation, not silent zero."""
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    position = {
        "id": "position-corrupt",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "okx_demo",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
    }
    position[field] = invalid_value
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    result = eng.evaluate_proposal(proposal)
    assert any(
        v.startswith("max_total_live_exposure_exceeded:account_exposure_check_error:invalid_position_data")
        for v in result.violations
    ), f"Expected check error, got: {result.violations}"


def test_check_12_demo_passes_with_valid_position(monkeypatch):
    """Check #12 demo: valid position + proposal within cap → passes."""
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    position = {
        "id": "position-valid",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "okx_demo",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0,
    }
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    result = eng.evaluate_proposal(proposal)
    # Position notional: 1.0 * 100.0 = 100
    # Proposal notional: 0.1 * 100.0 = 10
    # Total: 110, default cap → passes
    assert not any(
        v.startswith("max_total_live_exposure_exceeded")
        for v in result.violations
    ), f"Should pass, got: {result.violations}"
