"""End-to-end activation of the daily-loss circuit breaker via real closed positions.

This proves Component 1 lights up risk/engine.py §8: once realized losses today
breach the cap, the next proposal is rejected and the kill switch auto-trips.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import TradeProposal
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_realized_losses_trip_breaker_via_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.kill_switch_enabled_demo = True  # default False; paper_internal is demo-mode
    save_config(cfg)

    from aletheia.portfolio.pnl import today_realized_pnl
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk import kill_switch
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    # The default Demo denominator is authoritative paper account equity; this
    # $10 loss is well beyond the default 1% daily-loss limit.
    pos_id = "daily-loss-entry"
    _insert_closed_position_via_producer(
        position_id=pos_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        realized_pnl=-10.0,
        execution_mode="paper_internal",
        closed_at=datetime.now(timezone.utc).isoformat(),
        close_reason="stop_loss",
    )
    expected = -10.0
    assert today_realized_pnl() == pytest.approx(expected)

    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("max_daily_loss" in v for v in result.violations)
    assert kill_switch.is_active("demo") is True
    assert kill_switch.is_active("global") is False


def test_partial_close_ledger_read_failure_marks_snapshot_unavailable(tmp_path, monkeypatch):
    """Monkeypatch list_position_close_events to raise; snapshot must be unavailable."""
    setup_env(tmp_path, monkeypatch)

    import sqlite3 as _sqlite3
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "list_position_close_events",
                        lambda **_kw: (_ for _ in ()).throw(_sqlite3.OperationalError("no such table")))

    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    snapshot = authoritative_account_realized_loss("paper_internal")

    assert snapshot.available is False
    assert snapshot.reason == "partial_close_ledger_read_failed"
    assert snapshot.realized_pnl_usdt is None
    assert snapshot.loss_usdt is None

    # Risk engine must emit account_loss_unavailable and reject any proposal.
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("account_loss_unavailable" in v for v in result.violations)
    assert not any("max_daily_loss" in v for v in result.violations)
