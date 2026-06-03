"""Tests for portfolio PnL computation."""

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.portfolio.pnl import compute_portfolio, compute_and_store_snapshot


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_long_position_pnl(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTCUSDT", market_type="spot", direction="long",
        quantity=2.0, entry_price=100.0, execution_mode="paper_internal",
    ))
    # latest market price 110 → unrealized = (110-100)*2 = 20
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=110.0))
    summary = compute_portfolio()
    assert summary["unrealized_pnl"] == 20.0
    assert summary["positions_count"] == 1
    assert summary["exposure_by_symbol"]["BTCUSDT"] == 220.0
    reset_db()


def test_short_position_pnl(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="ETHUSDT", market_type="usdt_futures", direction="short",
        quantity=3.0, entry_price=200.0, execution_mode="paper_internal",
    ))
    # price drops to 180 → short profit = (200-180)*3 = 60
    repo.insert_market_snapshot(MarketSnapshot(symbol="ETHUSDT", market_type="spot", price=180.0))
    summary = compute_portfolio()
    assert summary["unrealized_pnl"] == 60.0
    reset_db()


def test_snapshot_persisted(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="SOLUSDT", market_type="spot", direction="long",
        quantity=10.0, entry_price=150.0, execution_mode="paper_internal",
    ))
    repo.insert_market_snapshot(MarketSnapshot(symbol="SOLUSDT", market_type="spot", price=155.0))
    snap = compute_and_store_snapshot()
    assert snap.positions_count == 1
    assert len(repo.list_portfolio_snapshots()) == 1
    reset_db()
