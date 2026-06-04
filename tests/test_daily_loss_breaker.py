"""End-to-end activation of the daily-loss circuit breaker via real closed positions.

This proves Component 1 lights up risk/engine.py §8: once realized losses today
breach the cap, the next proposal is rejected and the kill switch auto-trips.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, TradeProposal
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
    from aletheia.portfolio.pnl import close_position, today_realized_pnl
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk import kill_switch

    # cap = max_live_limited_cap_usdt (100) * max_daily_loss_pct (1.0%) = $1 limit.
    pos = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                   quantity=1.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(pos)
    close_position(pos.id, exit_price=90.0, reason="stop_loss")  # realized -10
    assert today_realized_pnl() == -10.0

    p = TradeProposal(symbol="ETHUSDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("max_daily_loss" in v for v in result.violations)
    assert kill_switch.is_active() is True
