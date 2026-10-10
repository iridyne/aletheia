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


def exchange_upnl(monkeypatch, values):
    """Serve exchange uPnL per (symbol, direction) for bitget_demo usdt_futures.

    Position uPnL is the exchange value or unavailable (PL-PM-18); no local
    (mark - entry) * qty estimate exists to fall back on.
    """
    from aletheia.data.symbols import to_canonical
    from aletheia.portfolio import pnl

    market_type = pnl._normalize_upnl_market_type("usdt_futures")
    index = {
        (to_canonical(symbol), "bitget_demo", market_type, direction): value
        for (symbol, direction), value in values.items()
    }
    monkeypatch.setattr(pnl, "_build_exchange_upnl_index", lambda: index)


def test_long_position_pnl(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTCUSDT", market_type="usdt_futures", direction="long",
        quantity=2.0, entry_price=100.0, execution_mode="bitget_demo",
    ))
    # Simulate a fresh exact instrument mark at 110 and the exchange's uPnL.
    monkeypatch.setattr("aletheia.data.price_feed.get_fresh_price_for_protective_close", lambda s, mt, *, venue: 110.0)
    exchange_upnl(monkeypatch, {("BTCUSDT", "long"): 20.0})
    summary = compute_portfolio()
    assert summary["positions"][0]["unrealized_pnl_source"] == "exchange_exact"
    assert summary["unrealized_pnl"] == 20.0
    assert summary["positions_count"] == 1
    assert summary["exposure_by_symbol"]["BTCUSDT"] == 220.0
    reset_db()


def test_short_position_pnl(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="ETHUSDT", market_type="usdt_futures", direction="short",
        quantity=3.0, entry_price=200.0, execution_mode="bitget_demo",
    ))
    # The exchange reports the short's uPnL; the local Position keys it by direction.
    monkeypatch.setattr("aletheia.data.price_feed.get_fresh_price_for_protective_close", lambda s, mt, *, venue: 180.0)
    exchange_upnl(monkeypatch, {
        ("ETHUSDT", "short"): 60.0,
        ("ETHUSDT", "long"): -999.0,
    })
    summary = compute_portfolio()
    assert summary["unrealized_pnl"] == 60.0
    reset_db()


def test_unrealized_by_symbol_preserves_zero_and_sums_same_symbol_legs(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=90.0,
        execution_mode="bitget_demo",
    ))
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="short",
        quantity=1.0,
        entry_price=90.0,
        execution_mode="bitget_demo",
    ))
    repo.insert_position(Position(
        symbol="ETHUSDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        execution_mode="bitget_demo",
    ))
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda s, mt, *, venue: 100.0,
    )
    exchange_upnl(monkeypatch, {
        ("BTCUSDT", "long"): 10.0,
        ("BTCUSDT", "short"): -10.0,
        ("ETHUSDT", "long"): 0.0,
    })

    summary = compute_portfolio()

    assert summary["positions_count"] == 3
    assert summary["unrealized_pnl"] == 0.0
    assert summary["unrealized_by_symbol"] == {
        "BTCUSDT": 0.0,
        "ETHUSDT": 0.0,
    }
    assert round(sum(summary["unrealized_by_symbol"].values()), 4) == summary["unrealized_pnl"]
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
            market_type="usdt_futures",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            execution_mode="bitget_demo",
        ))
    monkeypatch.setattr("aletheia.data.price_feed.get_fresh_price_for_protective_close", lambda s, mt, *, venue: 110.0)
    monkeypatch.setattr(pnl, "_build_exchange_upnl_index", lambda: {})

    marked = pnl.mark_positions_to_market(persist=True)

    assert len(marked) == 55
    oldest = repo._select_one("positions", "mark-all-000")
    assert oldest is not None
    assert oldest["current_price"] == 110.0
    # No exchange uPnL: the row is honestly unavailable, never a local estimate.
    assert oldest["unrealized_pnl"] is None
    assert oldest["unrealized_pnl_source"] == "unavailable"
    reset_db()


def test_snapshot_persisted(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    repo.insert_position(Position(
        symbol="SOLUSDT", market_type="spot", direction="long",
        quantity=10.0, entry_price=150.0, execution_mode="bitget_demo",
    ))
    repo.insert_market_snapshot(MarketSnapshot(symbol="SOLUSDT", market_type="spot", price=155.0))
    snap = compute_and_store_snapshot(execution_mode="bitget_demo")
    assert snap.positions_count == 1
    assert len(repo.list_portfolio_snapshots()) == 1
    reset_db()


def test_portfolio_snapshots_return_latest_window_in_ascending_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(61):
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=1000 + i, execution_mode="exchange_account",
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


def test_snapshot_day_windows_compare_iso_instants(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    fixed_now = datetime(2026, 8, 22, 15, 0, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(repo, "datetime", FixedDatetime)
    before = fixed_now - timedelta(days=7, seconds=1)
    cutoff = fixed_now - timedelta(days=7)
    after_fractional = fixed_now - timedelta(days=7) + timedelta(microseconds=1)
    for snapshot_at, value in (
        (before.isoformat(), 1.0),
        (cutoff.isoformat().replace("+00:00", "Z"), 2.0),
        (after_fractional.isoformat(), 3.0),
    ):
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(total_value_usdt=value, execution_mode="exchange_account", snapshot_at=snapshot_at)
        )

    rows = repo.list_portfolio_snapshots(days=7)

    assert [row["total_value_usdt"] for row in rows] == [2.0, 3.0]
    reset_db()


def test_account_equity_day_windows_compare_iso_instants(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    fixed_now = datetime(2026, 8, 22, 15, 0, 0, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now if tz is not None else fixed_now.replace(tzinfo=None)

    monkeypatch.setattr(repo, "datetime", FixedDatetime)
    before = fixed_now - timedelta(days=7, seconds=1)
    cutoff = fixed_now - timedelta(days=7)
    after_fractional = fixed_now - timedelta(days=7) + timedelta(microseconds=1)
    for snapshot_at, value in (
        (before.isoformat(), 1.0),
        (cutoff.isoformat().replace("+00:00", "Z"), 2.0),
        (after_fractional.isoformat(), 3.0),
    ):
        repo.insert_portfolio_snapshot(
            PortfolioSnapshot(
                total_value_usdt=value,
                account_equity_usdt=value,
                execution_mode="bitget_demo",
                snapshot_at=snapshot_at,
            )
        )

    rows = repo.list_account_equity_snapshots("bitget_demo", days=7)

    assert [row["account_equity_usdt"] for row in rows] == [2.0, 3.0]
    reset_db()


def test_today_pnl_includes_closed_pnl_only(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=115.0,
        realized_pnl=15.0,
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="broker_ledger",
        status="closed",
        execution_mode="bitget_demo",
        closed_at=datetime.now(timezone.utc).isoformat(),
    ))
    repo.insert_position(Position(
        symbol="ETHUSDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        entry_price=50.0,
        execution_mode="bitget_demo",
        status="open",
    ))
    # open ETH position: the exchange reports +20 uPnL
    monkeypatch.setattr("aletheia.data.price_feed.get_fresh_price_for_protective_close", lambda s, mt, *, venue: 60.0)
    exchange_upnl(monkeypatch, {("ETHUSDT", "long"): 20.0})
    # closed BTC position closed today should contribute to realized today, closed_at is today by default from model
    summary = compute_portfolio()
    assert summary["today_realized_pnl"] == 15.0
    assert summary["unrealized_pnl"] == 20.0
    assert summary["today_pnl"] == 35.0
    reset_db()
