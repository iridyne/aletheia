"""Database capacity observation and explicit compaction boundaries."""

import sqlite3

import pytest
from pydantic import ValidationError


def test_capacity_snapshot_is_read_only_and_attributes_objects(tmp_path):
    from aletheia.maintenance.database_capacity import database_capacity_snapshot

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, payload BLOB)")
    db.executemany(
        "INSERT INTO samples(payload) VALUES (?)",
        [(b"x" * 4096,) for _ in range(40)],
    )
    db.commit()
    db.close()

    before_mtime = path.stat().st_mtime_ns
    snapshot = database_capacity_snapshot(
        path=path,
        capacity_mb=0.01,
        deep=True,
        top_n=5,
    )

    assert snapshot["available"] is True
    assert snapshot["database_bytes"] == path.stat().st_size
    assert snapshot["physical_bytes"] >= snapshot["database_bytes"]
    assert snapshot["logical_used_bytes"] > 0
    assert snapshot["capacity_status"] == "over_budget"
    assert any(row["name"] == "samples" for row in snapshot["top_objects"])
    sample_object = next(row for row in snapshot["top_objects"] if row["name"] == "samples")
    assert 0 <= sample_object["packing_ratio"] <= 1
    assert sample_object["unused_bytes"] >= 0
    assert path.stat().st_mtime_ns == before_mtime
    assert snapshot["effective_pragmas"]["query_only"] == 1
    assert "cache_size" in snapshot["effective_pragmas"]


def test_new_sqlite_database_enables_incremental_vacuum(tmp_path):
    from aletheia.storage.sqlite import _open_connection

    path = tmp_path / "new.db"
    db = _open_connection(path)
    try:
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024
    finally:
        db.close()


def test_open_connection_verifies_hardened_pragma_baseline(tmp_path):
    from aletheia.storage.sqlite import _open_connection

    path = tmp_path / "new.db"
    db = _open_connection(path)
    try:
        assert db.execute("PRAGMA cache_size").fetchone()[0] == -65536
        assert db.execute("PRAGMA mmap_size").fetchone()[0] > 0
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        db.close()


def test_pragma_baseline_mismatch_fails_closed(tmp_path):
    from aletheia.storage.sqlite import _verify_pragma_baseline, PragmaBaselineError
    import sqlite3

    path = tmp_path / "x.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
    with pytest.raises(PragmaBaselineError, match="journal_mode"):
        _verify_pragma_baseline(conn, {"journal_mode": "wal"})
    conn.close()


def test_readonly_handle_is_query_only_and_cached(tmp_path):
    from aletheia.storage.sqlite import _open_connection, _open_readonly_connection

    path = tmp_path / "app.db"
    w = _open_connection(path)
    w.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    w.execute("INSERT INTO t(v) VALUES ('x')")
    w.close()

    r = _open_readonly_connection(path, timeout=1.0)
    try:
        assert r.execute("PRAGMA query_only").fetchone()[0] == 1
        assert r.execute("PRAGMA cache_size").fetchone()[0] == -65536
        with pytest.raises(sqlite3.OperationalError, match="attempt to write"):
            r.execute("CREATE TABLE t2(x)")
    finally:
        r.close()


def test_optimize_query_planner_handles_never_analyzed_database(tmp_path):
    from aletheia.storage.sqlite import _open_connection
    from aletheia.maintenance.database_capacity import optimize_query_planner

    path = tmp_path / "new.db"
    db = _open_connection(path)
    try:
        db.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
        db.execute("CREATE INDEX t_v ON t(v)")
        db.executemany("INSERT INTO t(v) VALUES (?)", [(f"row{i}",) for i in range(100)])
        result = optimize_query_planner(db)
        assert "duration_ms" in result
        assert "analyzed" in result
    finally:
        db.close()


def test_passive_checkpoint_reports_exact_backlog(tmp_path):
    from aletheia.maintenance.database_capacity import passive_wal_checkpoint

    path = tmp_path / "app.db"
    path.touch()
    path.with_name("app.db-wal").write_bytes(b"x" * 10)

    class _Cursor:
        def __init__(self, value):
            self._value = value

        def fetchone(self):
            return self._value

    class _Database:
        def execute(self, sql):
            if sql == "PRAGMA page_size":
                return _Cursor((4096,))
            assert sql == "PRAGMA wal_checkpoint(PASSIVE)"
            return _Cursor((0, 1500, 200))

    result = passive_wal_checkpoint(_Database(), path=path)

    assert result["log_frames"] == 1500
    assert result["checkpointed_frames"] == 200
    assert result["remaining_frames"] == 1300
    assert result["remaining_bytes"] == 1300 * 4096
    assert result["status"] == "backlogged"


def test_capacity_budget_has_schema_bounds():
    from aletheia.config.schema import RetentionConfig

    assert RetentionConfig().app_db_capacity_mb == 2048
    with pytest.raises(ValidationError):
        RetentionConfig(app_db_capacity_mb=511)
    with pytest.raises(ValidationError):
        RetentionConfig(app_db_capacity_mb=65537)


def test_capacity_trend_requires_history_then_reports_robust_eta():
    from aletheia.maintenance.database_capacity import database_capacity_trend

    assert database_capacity_trend([])["status"] == "insufficient_history"
    samples = [
        {
            "captured_at": f"2026-08-{day:02d}T00:00:00+00:00",
            "physical_bytes": (100 + (day - 1) * 10) * 1024 * 1024,
        }
        for day in (1, 8, 15)
    ]

    trend = database_capacity_trend(
        samples,
        capacity_bytes=300 * 1024 * 1024,
    )

    assert trend["status"] == "established"
    assert trend["slope_mib_per_day"] == pytest.approx(10.0)
    assert trend["capacity_eta_days"] == pytest.approx(6.0)
    assert trend["capacity_eta_at"].startswith("2026-08-21")


def test_incremental_reclaim_is_bounded_and_writer_serialized(tmp_path, monkeypatch):
    from aletheia.maintenance.database_capacity import reclaim_incremental_freelist
    from aletheia.storage.sqlite import _open_connection

    db = _open_connection(tmp_path / "app.db")
    db.execute("CREATE TABLE churn (id INTEGER PRIMARY KEY, payload BLOB)")
    db.executemany(
        "INSERT INTO churn(payload) VALUES (?)",
        [(b"x" * 8192,) for _ in range(100)],
    )
    db.execute("DELETE FROM churn WHERE id > 1")
    calls: list[str] = []
    monkeypatch.setattr(
        "aletheia.storage.sqlite.acquire_write_lock",
        lambda: calls.append("acquire") or 0.0,
    )
    monkeypatch.setattr(
        "aletheia.storage.sqlite.release_write_lock",
        lambda: calls.append("release"),
    )

    freelist_before = int(db.execute("PRAGMA freelist_count").fetchone()[0])

    result = reclaim_incremental_freelist(db, max_mb=1)

    freelist_after = int(db.execute("PRAGMA freelist_count").fetchone()[0])
    assert result["enabled"] is True
    assert result["remaining_pages"] >= 0
    assert calls == ["acquire", "release"] * (len(calls) // 2)
    assert len(calls) > 2
    # Reported progress must match the freelist the caller can observe.
    assert result["remaining_pages"] == freelist_after
    assert result["reclaimed_pages"] == freelist_before - freelist_after
    db.close()


def test_reclaim_budget_advances_by_reclaimed_not_requested_pages(monkeypatch):
    """A one-page-per-call vacuum must count one page of progress, not 64.

    #3420 advanced the budget by the requested chunk size, so a nominal 16 MiB
    reclaim could stop after 64 calls having freed ~256 KiB (#3433).
    """
    from aletheia.maintenance import database_capacity
    from aletheia.maintenance.database_capacity import reclaim_incremental_freelist

    monkeypatch.setattr(
        "aletheia.storage.sqlite.acquire_write_lock", lambda: 0.0
    )
    monkeypatch.setattr("aletheia.storage.sqlite.release_write_lock", lambda: None)

    page_size = 4096
    start_freelist = 1000
    state = {"freelist": start_freelist}
    vacuum_calls: list[int] = []

    class OnePagePerCall:
        """Reclaims exactly one page however many are requested."""

        def execute(self, sql):
            if "page_size" in sql:
                return _Row(page_size)
            if "freelist_count" in sql:
                return _Row(state["freelist"])
            if "auto_vacuum" in sql:
                return _Row(2)
            if "incremental_vacuum" in sql:
                vacuum_calls.append(int(sql.split("(")[1].rstrip(")")))
                state["freelist"] = max(0, state["freelist"] - 1)
                return _Row(0)
            raise AssertionError(f"unexpected pragma: {sql}")

    result = reclaim_incremental_freelist(OnePagePerCall(), max_mb=16)

    # 16 MiB / 4 KiB = 4096 pages requested, but only 1000 pages exist, so the
    # target is the freelist itself; the attempt cap stops the walk first.
    assert result["attempts"] == database_capacity._RECLAIM_MAX_ATTEMPTS
    # One page per call → progress equals the number of calls, not 64x that.
    assert result["reclaimed_pages"] == database_capacity._RECLAIM_MAX_ATTEMPTS
    assert result["remaining_pages"] == start_freelist - result["reclaimed_pages"]
    # Permit holds stay small: each request is one bounded chunk.
    assert set(vacuum_calls) == {database_capacity._RECLAIM_CHUNK_PAGES}
    # requested_pages still describes requests, and is far above real progress —
    # the two are no longer interchangeable.
    assert result["requested_pages"] > result["reclaimed_pages"]


def test_reclaim_stops_when_a_chunk_makes_no_progress(monkeypatch):
    """A floor SQLite cannot move terminates the loop instead of spinning."""
    from aletheia.maintenance.database_capacity import reclaim_incremental_freelist

    monkeypatch.setattr(
        "aletheia.storage.sqlite.acquire_write_lock", lambda: 0.0
    )
    monkeypatch.setattr("aletheia.storage.sqlite.release_write_lock", lambda: None)

    vacuum_calls: list[int] = []

    class StuckFreelist:
        def execute(self, sql):
            if "page_size" in sql:
                return _Row(4096)
            if "freelist_count" in sql:
                return _Row(500)
            if "auto_vacuum" in sql:
                return _Row(2)
            if "incremental_vacuum" in sql:
                vacuum_calls.append(1)
                return _Row(0)
            raise AssertionError(f"unexpected pragma: {sql}")

    result = reclaim_incremental_freelist(StuckFreelist(), max_mb=16)

    assert len(vacuum_calls) == 1
    assert result["attempts"] == 1
    assert result["reclaimed_pages"] == 0
    assert result["remaining_pages"] == 500


class _Row:
    """Minimal PRAGMA cursor stub returning a single scalar."""

    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


def test_compaction_defaults_to_read_only_plan(tmp_path):
    from ops.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE preserved (value TEXT)")
    db.execute("INSERT INTO preserved VALUES ('evidence')")
    db.commit()
    db.close()

    result = compact_database(path=path)

    assert result["applied"] is False
    assert sqlite3.connect(path).execute("SELECT value FROM preserved").fetchone()[0] == "evidence"


def test_compaction_reclaims_pages_and_preserves_rows(tmp_path, monkeypatch):
    from ops.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, payload BLOB)")
    db.executemany(
        "INSERT INTO preserved(payload) VALUES (?)",
        [(b"x" * 8192,) for _ in range(100)],
    )
    db.execute("DELETE FROM preserved WHERE id > 1")
    db.commit()
    db.close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)

    result = compact_database(path=path, apply=True)

    assert result["applied"] is True
    assert result["backup_path"].endswith(".tar.gz")
    assert result["after"]["auto_vacuum"] == 2
    assert result["after"]["database_bytes"] < result["before"]["database_bytes"]
    verify = sqlite3.connect(path)
    try:
        assert verify.execute("SELECT COUNT(*) FROM preserved").fetchone()[0] == 1
        assert verify.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        verify.close()


def test_compaction_refuses_running_gateway(tmp_path, monkeypatch):
    from ops.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: True)

    with pytest.raises(RuntimeError, match="Gateway is running"):
        compact_database(path=path, apply=True)


def test_compaction_converts_ohlcv_layout_and_restores_wal(tmp_path, monkeypatch):
    from ops.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE market_ohlcv (
            exchange TEXT NOT NULL DEFAULT 'legacy_unknown', symbol TEXT NOT NULL,
            market_type TEXT NOT NULL DEFAULT 'spot', timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
            low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
            provider TEXT NOT NULL DEFAULT 'ccxt',
            PRIMARY KEY(exchange,symbol,market_type,timeframe,ts)
        );
        CREATE INDEX idx_market_ohlcv_timeframe_ts ON market_ohlcv(timeframe,ts);
        INSERT INTO market_ohlcv VALUES
            ('bitget','BTC/USDT','swap','1m',1,100,101,99,100.5,12,'ccxt');
        """
    )
    db.close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)
    progress: list[tuple[str, str]] = []

    result = compact_database(
        path=path,
        apply=True,
        progress=lambda stage, message: progress.append((stage, message)),
    )

    assert result["market_ohlcv_optimization"]["applied"] is True
    assert [stage for stage, _message in progress] == [
        "backup",
        "preflight",
        "ohlcv_layout",
        "vacuum",
        "verify",
        "complete",
    ]
    verify = sqlite3.connect(path)
    try:
        schema = verify.execute("SELECT sql FROM sqlite_schema WHERE name='market_ohlcv'").fetchone()[0]
        assert "WITHOUT ROWID" in schema.upper()
        assert verify.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0] == 1
        assert verify.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert verify.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        verify.close()


def test_compaction_refusal_restores_original_wal_mode(tmp_path, monkeypatch):
    from ops.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path, isolation_level=None)
    db.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE market_ohlcv (
            exchange TEXT NOT NULL DEFAULT 'legacy_unknown', symbol TEXT NOT NULL,
            market_type TEXT NOT NULL DEFAULT 'spot', timeframe TEXT NOT NULL,
            ts INTEGER NOT NULL, open REAL NOT NULL, high REAL NOT NULL,
            low REAL NOT NULL, close REAL NOT NULL, volume REAL NOT NULL,
            provider TEXT NOT NULL DEFAULT 'ccxt',
            PRIMARY KEY(exchange,symbol,market_type,timeframe,ts)
        );
        CREATE INDEX owner_custom_index ON market_ohlcv(close);
        INSERT INTO market_ohlcv VALUES
            ('bitget','BTC/USDT','swap','1m',1,100,101,99,100.5,12,'ccxt');
        """
    )
    db.close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)

    with pytest.raises(RuntimeError, match="unexpected indexes"):
        compact_database(path=path, apply=True)

    verify = sqlite3.connect(path)
    try:
        assert verify.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert verify.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0] == 1
        assert verify.execute("SELECT rowid FROM market_ohlcv").fetchone() is not None
    finally:
        verify.close()
