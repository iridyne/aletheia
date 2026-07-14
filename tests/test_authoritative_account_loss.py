"""Risk daily loss must use provable account economics, never performance fallback."""

from __future__ import annotations

from datetime import datetime, timezone


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
