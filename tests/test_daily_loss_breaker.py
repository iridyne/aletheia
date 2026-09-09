"""An unreadable external close ledger must not produce a false daily-loss total."""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


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


def test_partial_close_ledger_read_failure_marks_snapshot_unavailable(tmp_path, monkeypatch):
    """Monkeypatch list_position_close_events to raise; snapshot must be unavailable."""
    setup_env(tmp_path, monkeypatch)

    import sqlite3 as _sqlite3
    import aletheia.storage.repositories as _repo
    monkeypatch.setattr(_repo, "list_position_close_events",
                        lambda **_kw: (_ for _ in ()).throw(_sqlite3.OperationalError("no such table")))

    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    snapshot = authoritative_account_realized_loss("bitget_demo")

    assert snapshot.available is False
    assert snapshot.reason == "partial_close_ledger_read_failed"
    assert snapshot.realized_pnl_usdt is None
    assert snapshot.loss_usdt is None

    # Risk engine must emit account_loss_unavailable and reject any proposal.
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("account_loss_unavailable" in v for v in result.violations)
    assert not any("max_daily_loss" in v for v in result.violations)
