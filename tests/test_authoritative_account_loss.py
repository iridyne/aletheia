"""Risk daily loss must use provable account economics, never performance fallback."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


def _closed_position(
    position_id: str,
    pnl: float | None,
    *,
    mode: str = "paper_internal",
    eligible: bool = True,
) -> None:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    closed_at = datetime.now(timezone.utc).isoformat()
    if eligible:
        _insert_closed_position_via_producer(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            realized_pnl=float(pnl or 0.0),
            execution_mode=mode,
            closed_at=closed_at,
        )
        return

    kwargs = {
        "id": position_id,
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 100.0 + float(pnl or 0.0),
        "execution_mode": mode,
        "status": "closed",
        "realized_pnl": pnl,
        "closed_at": closed_at,
    }
    kwargs.update({
        "close_reason": "reconciled_gone",
        "close_category": "administrative",
        "exit_price_source": "unavailable",
        "exit_fill_verified": False,
        "outcome_eligible": False,
        "outcome_exclusion_reason": "administrative_close",
        "outcome_semantics_version": "oi-v1",
    })
    repo.insert_position(Position(**kwargs))


def _corrupt_paper_close_event(position_id: str, amount: float) -> None:
    """Create an internally continuous ledger mismatch from a real close event."""
    from aletheia.storage import repositories as repo

    db = repo.get_db()
    event = db.execute(
        "SELECT * FROM paper_account_events "
        "WHERE action = 'position_closed' AND position_id = ?",
        (position_id,),
    ).fetchone()
    assert event is not None
    after = float(event["cash_balance_before_usdt"]) + amount
    db.execute(
        "UPDATE paper_account_events SET amount_usdt = ?, "
        "cash_balance_after_usdt = ? WHERE id = ?",
        (amount, after, event["id"]),
    )


def _proposal():
    from aletheia.storage.models import TradeProposal

    return TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.01, price=100.0,
    )


def test_authoritative_loss_reconciles_verified_paper_close_to_cash_ledger():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss

    _closed_position("loss", -8.0)
    _closed_position("gain", 3.0)

    snapshot = authoritative_account_realized_loss("paper_internal")
    assert snapshot.available is True
    assert snapshot.realized_pnl_usdt == -5.0
    assert snapshot.loss_usdt == 5.0
    assert snapshot.source == "paper_account_ledger"


def test_mismatched_or_administrative_paper_close_makes_loss_unavailable():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss

    _closed_position("verified", -8.0)
    _corrupt_paper_close_event("verified", -7.0)
    mismatch = authoritative_account_realized_loss("paper_internal")
    assert mismatch.available is False
    assert mismatch.reason == "paper_ledger_mismatch"
    assert mismatch.realized_pnl_usdt is None


def test_verified_external_economics_are_separate_from_strategy_metric_api():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss

    _closed_position("broker-loss", -4.0, mode="okx_demo")
    snapshot = authoritative_account_realized_loss("okx_demo")
    assert snapshot.available is True
    assert snapshot.realized_pnl_usdt == -4.0
    assert snapshot.source == "verified_position_economics"


def test_unverified_close_blocks_risk_without_fabricating_breach_or_tripping(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.kill_switch import is_active
    from aletheia.storage import repositories as repo

    cfg = load_config()
    cfg.risk.kill_switch_enabled_demo = True
    save_config(cfg)
    _closed_position("administrative", -10_000.0, eligible=False)

    proposal = _proposal()
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert "account_loss_unavailable:administrative_close" in result.violations
    assert not any(value.startswith("max_daily_loss_exceeded") for value in result.violations)
    assert is_active() is False


def test_genuine_authoritative_loss_keeps_existing_threshold_and_trip(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.kill_switch import is_active
    from aletheia.storage import repositories as repo

    cfg = load_config()
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.kill_switch_enabled_demo = True
    save_config(cfg)
    _closed_position("verified-loss", -10.0)

    proposal = _proposal()
    repo.insert_trade_proposal(proposal)
    result = evaluate_proposal(proposal)
    assert any(value.startswith("max_daily_loss_exceeded:-10.0") for value in result.violations)
    assert is_active() is True


def _pending_paper_close(
    position_id: str,
    *,
    entry_price: float = 100.0,
    exit_price: float = 102.0,
    quantity: float = 0.1,
    direction: str = "short",
    entry_fee: float = 0.001,
    exit_fee: float = 0.001,
) -> None:
    """Insert a closed paper_internal usdt_futures position with realized_pnl=NULL.

    Simulates a position where the funding-computation step has not yet run
    (e.g. a collection gap caused settle_unsettled_paper_closes to skip it).
    No paper_account_events entry is written — that is the invariant being tested.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    closed_at = datetime.now(timezone.utc).isoformat()
    repo.insert_position(Position(
        id=position_id,
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=exit_price,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=None,     # funding not computed yet
        funding_cost=None,
        funding_source="unavailable",
        close_reason="max_hold",
        close_category="neutral",
        entry_fee=entry_fee,
        exit_fee=exit_fee,
        exit_price_source="exchange",
        exit_fill_verified=True,
        outcome_eligible=False,
        outcome_exclusion_reason="missing_event_observation",
        outcome_semantics_version="oi-v1",
        closed_at=closed_at,
    ))


def test_null_realized_pnl_uses_conservative_estimate_not_blocking():
    """paper_internal closes with realized_pnl=NULL use a conservative estimate.

    The risk engine must not be blocked by unresolved funding computation; instead
    the daily-loss check proceeds with gross−fees (funding=0, gains capped at 0).
    """
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss

    # Short that lost money (price went up) — gross loss estimate is negative.
    # exit=102, entry=100, qty=0.1 → gross = (100-102)*0.1 = -0.2
    # fees = 0.001+0.001 = 0.002 → estimate = min(0, -0.2-0.002) = -0.202
    _pending_paper_close("pending-1", entry_price=100.0, exit_price=102.0,
                         quantity=0.1, direction="short")

    snapshot = authoritative_account_realized_loss("paper_internal")
    assert snapshot.available is True, f"Expected available, got: {snapshot.reason}"
    assert snapshot.source == "paper_account_ledger_partial_estimate"
    assert snapshot.reason == "available_with_conservative_estimates"
    assert snapshot.realized_pnl_usdt is not None
    assert snapshot.realized_pnl_usdt < 0.0  # conservative estimate is a loss
    assert abs(snapshot.realized_pnl_usdt - (-0.202)) < 1e-6


def test_null_realized_pnl_combined_with_settled_returns_sum():
    """A mix of settled and pending closes produces a combined conservative total."""
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import ensure_paper_account

    ensure_paper_account()
    # Settled close: -3.0 verified via the normal close path + ledger entry
    _closed_position("settled-mix", -3.0)
    # Pending close: short that gained (price dropped) → gross gain capped at 0
    # exit=95, entry=100, qty=0.1 → gross = (100-95)*0.1 = +0.5 → capped to 0
    _pending_paper_close("pending-mix", entry_price=100.0, exit_price=95.0,
                         quantity=0.1, direction="short")

    snapshot = authoritative_account_realized_loss("paper_internal")
    assert snapshot.available is True
    assert snapshot.source == "paper_account_ledger_partial_estimate"
    # settled contribution = -3.0; conservative contribution = 0.0 (gain capped)
    assert abs(snapshot.realized_pnl_usdt - (-3.0)) < 1e-6


def _orphaned_paper_close(position_id: str, realized_pnl: float) -> None:
    """Insert a closed paper_internal position with realized_pnl set but NO ledger entry.

    Simulates the condition produced by a direct DB repair (writing realized_pnl to
    positions without going through settle_position_closed).
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    closed_at = datetime.now(timezone.utc).isoformat()
    repo.insert_position(Position(
        id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="short",
        quantity=0.1,
        entry_price=100.0,
        current_price=100.0 - realized_pnl / 0.1,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=realized_pnl,
        funding_cost=0.0,
        funding_source="local_simulation",
        close_reason="max_hold",
        close_category="neutral",
        entry_fee=0.001,
        exit_fee=0.001,
        exit_price_source="exchange",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="",
        outcome_semantics_version="oi-v1",
        closed_at=closed_at,
    ))
    # Deliberately omit settle_position_closed() to simulate the orphan condition.


def test_orphaned_ledger_entry_is_inline_reconciled_and_does_not_block():
    """An orphaned position (realized_pnl set, no ledger entry) is auto-fixed inline.

    The daily-loss check must not block paper trades when the only mismatch is a
    missing position_closed event that can be deterministically reconstructed from
    positions.realized_pnl.
    """
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import ensure_paper_account
    from aletheia.storage import repositories as repo

    ensure_paper_account()
    _orphaned_paper_close("orphan-1", -5.0)

    # Verify the orphan state: position exists with realized_pnl, no ledger entry.
    db = repo.get_db()
    ledger_before = db.execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE action='position_closed' AND position_id='orphan-1'"
    ).fetchone()[0]
    assert ledger_before == 0, "Test setup error: ledger entry should be absent"

    snapshot = authoritative_account_realized_loss("paper_internal")

    # After the call, the ledger entry must have been written inline.
    ledger_after = db.execute(
        "SELECT COUNT(*) FROM paper_account_events "
        "WHERE action='position_closed' AND position_id='orphan-1'"
    ).fetchone()[0]
    assert ledger_after == 1, "Inline reconcile should have written the ledger entry"

    assert snapshot.available is True, f"Expected available, got: {snapshot.reason}"
    assert snapshot.source == "paper_account_ledger"
    assert abs(snapshot.realized_pnl_usdt - (-5.0)) < 1e-6


def test_funding_settler_repairs_orphan_when_no_unsettled_rows_exist():
    from aletheia.portfolio.paper_account import (
        ensure_paper_account,
        settle_unsettled_paper_closes,
    )
    from aletheia.storage import repositories as repo

    ensure_paper_account()
    _orphaned_paper_close("orphan-only", -4.0)

    result = asyncio.run(settle_unsettled_paper_closes())

    event = repo.get_db().execute(
        "SELECT amount_usdt FROM paper_account_events "
        "WHERE action='position_closed' AND position_id=?",
        ("orphan-only",),
    ).fetchone()
    assert event is not None
    assert float(event["amount_usdt"]) == -4.0
    assert result == {
        "settled": 1,
        "still_pending": 0,
        "gave_up": 0,
        "errors": [],
    }


def test_delayed_previous_day_settlement_is_not_today_phantom():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import (
        ensure_paper_account,
        settle_position_closed,
    )
    from aletheia.storage import repositories as repo

    ensure_paper_account()
    _orphaned_paper_close("delayed-settlement", -6.0)
    repo.update_position(
        "delayed-settlement",
        closed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    settle_position_closed("delayed-settlement", -6.0)

    snapshot = authoritative_account_realized_loss("paper_internal")

    assert snapshot.available is True
    assert snapshot.reason == "available"
    assert snapshot.realized_pnl_usdt == 0.0
    assert snapshot.loss_usdt == 0.0
    assert snapshot.event_count == 0


def test_delayed_previous_day_settlement_with_wrong_amount_remains_unavailable():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import (
        ensure_paper_account,
        settle_position_closed,
    )
    from aletheia.storage import repositories as repo

    ensure_paper_account()
    _orphaned_paper_close("delayed-corrupt-settlement", -6.0)
    repo.update_position(
        "delayed-corrupt-settlement",
        closed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )
    settle_position_closed("delayed-corrupt-settlement", -6.0)
    _corrupt_paper_close_event("delayed-corrupt-settlement", -5.0)

    snapshot = authoritative_account_realized_loss("paper_internal")

    assert snapshot.available is False
    assert snapshot.reason == "paper_ledger_mismatch"
    assert snapshot.realized_pnl_usdt is None


def test_new_close_event_without_valid_paper_position_remains_unavailable():
    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.portfolio.paper_account import (
        ensure_paper_account,
        settle_position_closed,
    )

    ensure_paper_account()
    settle_position_closed("missing-position", -1.0)

    snapshot = authoritative_account_realized_loss("paper_internal")

    assert snapshot.available is False
    assert snapshot.reason == "paper_ledger_mismatch"
    assert snapshot.realized_pnl_usdt is None
