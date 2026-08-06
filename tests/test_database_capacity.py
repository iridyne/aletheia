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


def test_new_sqlite_database_enables_incremental_vacuum(tmp_path):
    from aletheia.storage.sqlite import _open_connection

    path = tmp_path / "new.db"
    db = _open_connection(path)
    try:
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert db.execute("PRAGMA journal_size_limit").fetchone()[0] == 64 * 1024 * 1024
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

    result = reclaim_incremental_freelist(db, max_mb=1)

    assert result["enabled"] is True
    assert result["requested_pages"] <= 256
    assert result["remaining_pages"] >= 0
    assert calls == ["acquire", "release"]
    db.close()


def test_compaction_defaults_to_read_only_plan(tmp_path):
    from aletheia.maintenance.database_capacity import compact_database

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
    from aletheia.maintenance.database_capacity import compact_database

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
    from aletheia.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: True)

    with pytest.raises(RuntimeError, match="Gateway is running"):
        compact_database(path=path, apply=True)


def test_compaction_converts_ohlcv_layout_and_restores_wal(tmp_path, monkeypatch):
    from aletheia.maintenance.database_capacity import compact_database

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
    from aletheia.maintenance.database_capacity import compact_database

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
