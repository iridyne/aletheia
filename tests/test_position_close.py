"""Tests for closing positions, realized PnL, and today's realized-PnL aggregate.

Supports the §21.3 `max daily loss` risk check, which needs a realized-PnL source.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position
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


def test_close_long_position_realizes_profit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                 quantity=2.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(p)

    result = close_position(p.id, exit_price=110.0)
    assert result["realized_pnl"] == 20.0  # (110-100)*2

    closed = repo._select_one("positions", p.id)
    assert closed["status"] == "closed"
    assert closed["realized_pnl"] == 20.0
    assert closed["closed_at"] is not None
    # no longer counted as open
    assert len(repo.list_positions(status="open")) == 0


def test_close_short_position_realizes_loss(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    p = Position(symbol="ETHUSDT", market_type="usdt_futures", direction="short",
                 quantity=1.0, entry_price=200.0, execution_mode="paper_internal")
    repo.insert_position(p)
    # price rose against the short → loss = (200-220)*1 = -20
    result = close_position(p.id, exit_price=220.0)
    assert result["realized_pnl"] == -20.0


def test_today_realized_pnl_sums_closed_positions(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position, today_realized_pnl

    p1 = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=100.0, execution_mode="paper_internal")
    p2 = Position(symbol="SOLUSDT", market_type="spot", direction="long",
                  quantity=1.0, entry_price=50.0, execution_mode="paper_internal")
    repo.insert_position(p1)
    repo.insert_position(p2)
    close_position(p1.id, exit_price=90.0)   # -10
    close_position(p2.id, exit_price=60.0)   # +10
    assert today_realized_pnl() == 0.0

    p3 = Position(symbol="ETHUSDT", market_type="spot", direction="long",
                  quantity=2.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(p3)
    close_position(p3.id, exit_price=80.0)   # -40
    assert today_realized_pnl() == -40.0
