"""Tests for portfolio PnL computation."""

from datetime import datetime, timezone, timedelta
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Position, MarketSnapshot, PortfolioSnapshot
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


def test_mark_to_market_updates_every_open_position_beyond_display_limit(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio import pnl

    for index in range(55):
        repo.insert_position(Position(
            id=f"mark-all-{index:03d}",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="paper_internal",
        ))
    monkeypatch.setattr(pnl, "_latest_price", lambda _symbol: 110.0)
    monkeypatch.setattr(pnl, "_build_exchange_upnl_index", lambda: {})

    marked = pnl.mark_positions_to_market(persist=True)

    assert len(marked) == 55
    oldest = repo._select_one("positions", "mark-all-000")
    assert oldest["current_price"] == 110.0
    assert oldest["unrealized_pnl"] == 10.0
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


def test_portfolio_snapshots_return_latest_window_in_ascending_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(61):
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=1000 + i,
                snapshot_at=(base + timedelta(minutes=i)).isoformat(),
            )
        )

    rows = repo.list_portfolio_snapshots(limit=50)

    assert len(rows) == 50
    assert rows[0]["total_value_usdt"] == 1011
    assert rows[-1]["total_value_usdt"] == 1060
    assert [row["snapshot_at"] for row in rows] == sorted(row["snapshot_at"] for row in rows)
    assert repo.get_latest_portfolio_snapshot()["total_value_usdt"] == 1060
    reset_db()


def test_today_pnl_includes_closed_pnl_only(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=115.0,
        realized_pnl=15.0,
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="not_applicable",
        status="closed",
        execution_mode="paper_internal",
        closed_at=datetime.now(timezone.utc).isoformat(),
    ))
    repo.insert_position(Position(
        symbol="ETHUSDT",
        market_type="spot",
        direction="long",
        quantity=2.0,
        entry_price=50.0,
        execution_mode="paper_internal",
        status="open",
    ))
    # open ETH position at 60 => unrealized +10
    repo.insert_market_snapshot(MarketSnapshot(symbol="ETHUSDT", market_type="spot", price=60.0))
    # closed BTC position closed today should contribute to realized today, closed_at is today by default from model
    summary = compute_portfolio()
    assert summary["today_realized_pnl"] == 15.0
    assert summary["unrealized_pnl"] == 20.0
    assert summary["today_pnl"] == 35.0
    reset_db()
