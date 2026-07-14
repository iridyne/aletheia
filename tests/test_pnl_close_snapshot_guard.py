"""Regression coverage for close-vs-averaging snapshot races."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.files.jsonl as jsonl
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq.reset_db()
    connection = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = connection
    jsonl._last_audit_hash = None
    run_migrations()


def test_close_rejects_averaging_change_then_succeeds_with_fresh_snapshot(
    tmp_path, monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position

    position = Position(
        id="position-close-averaging-race",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="bitget_demo",
        entry_fee=0.06,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
    )
    repo.insert_position(position)

    original_select_one = repo._select_one
    stale_snapshot_read = threading.Event()
    averaging_committed = threading.Event()
    block_initial_read = True

    def pause_after_initial_read(table, row_id):
        nonlocal block_initial_read
        row = original_select_one(table, row_id)
        if (
            block_initial_read
            and table == "positions"
            and row_id == position.id
            and threading.current_thread() is not threading.main_thread()
        ):
            block_initial_read = False
            stale_snapshot_read.set()
            assert averaging_committed.wait(timeout=10)
        return row

    monkeypatch.setattr(repo, "_select_one", pause_after_initial_read)

    with ThreadPoolExecutor(max_workers=1) as executor:
        close_attempt = executor.submit(
            close_position,
            position.id,
            exit_price=110.0,
            reason="take_profit",
        )
        assert stale_snapshot_read.wait(timeout=10)
        repo.update_position(
            position.id,
            quantity=2.0,
            entry_price=105.0,
            current_price=110.0,
            entry_fee=0.2,
            slippage_cost=0.3,
        )
        averaging_committed.set()
        with pytest.raises(
            RuntimeError,
            match="position economics changed before close",
        ):
            close_attempt.result(timeout=10)

    still_open = repo._select_one("positions", position.id)
    assert still_open["status"] == "open"
    assert still_open["quantity"] == pytest.approx(2.0)
    assert still_open["entry_price"] == pytest.approx(105.0)
    assert still_open["entry_fee"] == pytest.approx(0.2)
    assert not any(
        event["event_type"] == "position_closed"
        for event in repo.list_audit_events(limit=20)
    )

    retried = close_position(position.id, exit_price=110.0, reason="take_profit")

    # gross=(110-105)*2=10; stored entry fee=0.2; demo exit fee=110*2*0.0006.
    assert retried["realized_pnl"] == pytest.approx(9.668)
    closed = repo._select_one("positions", position.id)
    assert closed["status"] == "closed"
    assert closed["realized_pnl"] == pytest.approx(9.668)
