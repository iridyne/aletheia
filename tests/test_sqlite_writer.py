"""Regression evidence for the process-local SQLite writer lane."""

from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from pathlib import Path
import threading
import time

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage import sqlite as sqlite_store
from aletheia.storage.migrations import run_migrations


def _hold_file_lock(lock_path: str, ready, release) -> None:
    import fcntl
    import os

    file_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(file_fd, fcntl.LOCK_EX)
        ready.set()
        release.wait(timeout=5)
    finally:
        fcntl.flock(file_fd, fcntl.LOCK_UN)
        os.close(file_fd)


def _setup_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite_store.reset_db()
    run_migrations()
    return sqlite_store.get_db()


def _market_row(row_id: str) -> dict:
    return {
        "id": row_id,
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "price": 100.0,
        "snapshot_at": "2026-01-01",
    }


def test_run_write_uses_dedicated_connection_and_returns_exact_result(
    tmp_path,
    monkeypatch,
):
    db = _setup_db(tmp_path, monkeypatch)
    caller_thread = threading.get_ident()
    caller_connection = id(db)
    observed = {}

    def write(conn):
        observed["thread"] = threading.get_ident()
        observed["connection"] = id(conn)
        conn.execute(
            "INSERT INTO market_snapshots(id,symbol,market_type,price,snapshot_at) VALUES(?,?,?,?,?)",
            ("writer-row", "BTC/USDT", "spot", 100.0, "2026-01-01"),
        )
        return "committed"

    assert sqlite_store.run_write(write) == "committed"
    assert observed["thread"] != caller_thread
    assert observed["connection"] != caller_connection
    assert db.execute("SELECT COUNT(*) FROM market_snapshots WHERE id='writer-row'").fetchone()[0] == 1
    metrics = sqlite_store.writer_metrics_snapshot()
    assert metrics["completed"] >= 1
    assert metrics["thread_alive"] is True
    sqlite_store.reset_db()


def test_repository_helpers_stay_inside_existing_transaction(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    submitted_before = sqlite_store.writer_metrics_snapshot()["submitted"]

    with pytest.raises(RuntimeError, match="rollback sentinel"):
        with repo.transaction():
            repo._insert("market_snapshots", _market_row("rolled-back-row"))
            raise RuntimeError("rollback sentinel")

    assert db.execute("SELECT COUNT(*) FROM market_snapshots WHERE id='rolled-back-row'").fetchone()[0] == 0
    assert sqlite_store.writer_metrics_snapshot()["submitted"] == submitted_before
    sqlite_store.reset_db()


def test_writer_failure_rolls_back_and_propagates(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    failed_before = sqlite_store.writer_metrics_snapshot()["failed"]

    def broken_write(conn):
        conn.execute(
            "INSERT INTO market_snapshots(id,symbol,market_type,price,snapshot_at) VALUES(?,?,?,?,?)",
            ("failed-row", "BTC/USDT", "spot", 100.0, "2026-01-01"),
        )
        raise ValueError("write failed")

    with pytest.raises(ValueError, match="write failed"):
        sqlite_store.run_write(broken_write)

    assert db.execute("SELECT COUNT(*) FROM market_snapshots WHERE id='failed-row'").fetchone()[0] == 0
    assert sqlite_store.writer_metrics_snapshot()["failed"] == failed_before + 1
    sqlite_store.reset_db()


def test_production_connection_guards_legacy_autocommit_writes(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    guarded_before = sqlite_store.writer_metrics_snapshot()["legacy_direct_writes"]

    db.execute(
        "INSERT INTO market_snapshots(id,symbol,market_type,price,snapshot_at) "
        "VALUES(?,?,?,?,?)",
        ("legacy-guard", "BTC/USDT", "spot", 100.0, "2026-01-01"),
    )

    assert isinstance(db, sqlite_store._SerializedConnection)
    assert sqlite_store.writer_metrics_snapshot()["legacy_direct_writes"] == guarded_before + 1
    sqlite_store.reset_db()


def test_write_result_comes_from_writer_connection(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    assert repo.acquire_serenity_lock("writer-result", "owner-a") is True
    assert repo.acquire_serenity_lock("writer-result", "owner-b") is False
    assert repo.release_serenity_lock("writer-result", "owner-a") is True
    sqlite_store.reset_db()


def test_concurrent_repository_writes_share_one_writer_lane(tmp_path, monkeypatch):
    db = _setup_db(tmp_path, monkeypatch)
    row_ids = [f"concurrent-{index}" for index in range(24)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda row_id: repo._insert("market_snapshots", _market_row(row_id)),
                row_ids,
            )
        )

    placeholders = ",".join("?" for _ in row_ids)
    assert db.execute(
        f"SELECT COUNT(*) FROM market_snapshots WHERE id IN ({placeholders})",
        row_ids,
    ).fetchone()[0] == len(row_ids)
    metrics = sqlite_store.writer_metrics_snapshot()
    assert metrics["queue_depth"] == 0
    assert metrics["completed"] >= len(row_ids)
    sqlite_store.reset_db()


def test_cross_process_writer_permit_times_out_before_sqlite_busy(
    tmp_path, monkeypatch,
):
    if sqlite_store.fcntl is None:
        pytest.skip("advisory file locks are unavailable")
    _setup_db(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_file_lock,
        args=(str(tmp_path / "app.write.lock"), ready, release),
    )
    process.start()
    assert ready.wait(timeout=2)

    try:
        with pytest.raises(
            sqlite_store.sqlite3.OperationalError,
            match="another process held the SQLite writer permit",
        ):
            sqlite_store.acquire_write_lock(timeout=0.05)
    finally:
        release.set()
        process.join(timeout=2)

    assert process.exitcode == 0
    sqlite_store.reset_db()


def test_windows_file_lock_backend_serializes_cross_process_writes(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    calls = []

    class FakeMsvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        @staticmethod
        def locking(file_fd, mode, length):
            calls.append((file_fd, mode, length))

    monkeypatch.setattr(sqlite_store, "fcntl", None)
    monkeypatch.setattr(sqlite_store, "msvcrt", FakeMsvcrt)
    try:
        sqlite_store.acquire_write_lock(timeout=0.05)
    finally:
        sqlite_store.release_write_lock()
        sqlite_store.reset_db()

    assert [call[1] for call in calls] == [FakeMsvcrt.LK_NBLCK, FakeMsvcrt.LK_UNLCK]


def test_writer_lane_rejects_before_admission_when_queue_remains_full(
    tmp_path,
    monkeypatch,
):
    _setup_db(tmp_path, monkeypatch)
    monkeypatch.setattr(sqlite_store, "_WRITE_LOCK_TIMEOUT_S", 0.05)
    lane = sqlite_store._SQLiteWriterLane(tmp_path / "app.db", capacity=1)
    first_started = threading.Event()
    release_first = threading.Event()
    errors = []

    def blocking_write(conn):
        first_started.set()
        if not release_first.wait(timeout=2):
            raise RuntimeError("test writer was not released")

    def submit(operation):
        try:
            lane.submit(operation, immediate=False)
        except BaseException as exc:
            errors.append(exc)

    first = threading.Thread(target=submit, args=(blocking_write,))
    second = threading.Thread(target=submit, args=(lambda conn: None,))
    first.start()
    assert first_started.wait(timeout=1)
    second.start()
    deadline = time.monotonic() + 1
    while lane.queue_depth != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    try:
        with pytest.raises(sqlite_store.sqlite3.OperationalError, match="write_queue_timeout"):
            lane.submit(lambda conn: None, immediate=False)
    finally:
        release_first.set()
        first.join(timeout=2)
        second.join(timeout=2)
        lane.close()

    assert not errors
    assert sqlite_store.writer_metrics_snapshot()["admission_timeouts"] >= 1
    sqlite_store.reset_db()


def test_migrated_storage_modules_have_no_direct_literal_dml():
    from aletheia.portfolio import exchange_sync

    mutating = {"INSERT", "UPDATE", "DELETE", "REPLACE"}
    violations = []
    for module_path in (Path(repo.__file__), Path(exchange_sync.__file__)):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"execute", "executemany"} or not node.args:
                continue
            sql_node = node.args[0]
            if isinstance(sql_node, ast.Constant) and isinstance(sql_node.value, str):
                sql = sql_node.value
            elif isinstance(sql_node, ast.JoinedStr) and sql_node.values:
                first = sql_node.values[0]
                sql = first.value if isinstance(first, ast.Constant) else ""
            else:
                continue
            tokens = sql.lstrip().split(None, 1)
            if tokens and tokens[0].upper() in mutating:
                violations.append(f"{module_path.name}:{node.lineno}")

    assert violations == []
