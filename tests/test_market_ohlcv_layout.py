"""OHLCV physical-layout and rowid-independent retention contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest


_ROWID_DDL = """
CREATE TABLE market_ohlcv (
    exchange TEXT NOT NULL DEFAULT 'legacy_unknown',
    symbol TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'spot',
    timeframe TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    provider TEXT NOT NULL DEFAULT 'ccxt',
    PRIMARY KEY (exchange, symbol, market_type, timeframe, ts)
)
"""


def _row(ts: int, *, exchange: str = "bitget") -> tuple:
    return (
        exchange,
        "BTC/USDT",
        "swap",
        "1m",
        ts,
        100.0,
        101.0,
        99.0,
        100.5,
        12.0,
        "ccxt",
    )


def _insert(db: sqlite3.Connection, rows: list[tuple]) -> None:
    db.executemany(
        "INSERT INTO market_ohlcv "
        "(exchange,symbol,market_type,timeframe,ts,open,high,low,close,volume,provider) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )


def test_rebuild_market_ohlcv_without_rowid_preserves_values_and_indexes():
    from aletheia.storage.market_ohlcv_layout import (
        market_ohlcv_is_without_rowid,
        rebuild_market_ohlcv_without_rowid,
    )

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute(_ROWID_DDL)
    db.execute(
        "CREATE INDEX idx_market_ohlcv_instrument_tf_ts ON market_ohlcv(exchange,symbol,market_type,timeframe,ts DESC)"
    )
    db.execute("CREATE INDEX idx_market_ohlcv_timeframe_ts ON market_ohlcv(timeframe,ts)")
    rows = [_row(1), _row(2), _row(3, exchange="okx")]
    _insert(db, rows)

    result = rebuild_market_ohlcv_without_rowid(db)

    assert result["applied"] is True
    assert result["before"]["row_count"] == result["after"]["row_count"] == 3
    assert market_ohlcv_is_without_rowid(db) is True
    assert db.execute(
        "SELECT exchange,symbol,market_type,timeframe,ts,open,high,low,close,volume,provider "
        "FROM market_ohlcv ORDER BY exchange,ts"
    ).fetchall() == sorted(rows)
    with pytest.raises(sqlite3.OperationalError, match="rowid"):
        db.execute("SELECT rowid FROM market_ohlcv").fetchall()
    indexes = {
        row[0] for row in db.execute("SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name='market_ohlcv'")
    }
    assert indexes == {"idx_market_ohlcv_timeframe_ts"}
    assert rebuild_market_ohlcv_without_rowid(db)["reason"] == "already_without_rowid"
    db.close()


def test_rebuild_refuses_unexpected_index_without_mutation():
    from aletheia.storage.market_ohlcv_layout import rebuild_market_ohlcv_without_rowid

    db = sqlite3.connect(":memory:", isolation_level=None)
    db.execute(_ROWID_DDL)
    _insert(db, [_row(1)])
    db.execute("CREATE INDEX owner_custom_index ON market_ohlcv(close)")

    with pytest.raises(RuntimeError, match="unexpected indexes"):
        rebuild_market_ohlcv_without_rowid(db)

    assert db.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0] == 1
    assert db.execute("SELECT rowid FROM market_ohlcv").fetchone() is not None
    db.close()


def test_market_ohlcv_retention_works_without_rowid(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.repositories import prune_market_ohlcv
    from aletheia.storage.sqlite import get_db, reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    run_migrations()
    db = get_db()
    from aletheia.storage.market_ohlcv_layout import rebuild_market_ohlcv_without_rowid

    assert rebuild_market_ohlcv_without_rowid(db)["applied"] is True
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    old_ms = int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp() * 1000)
    _insert(db, [_row(old_ms), _row(now_ms), _row(now_ms - 1, exchange="legacy_unknown")])

    assert prune_market_ohlcv({"1m": 2}) == 2
    assert [tuple(row) for row in db.execute("SELECT exchange,ts FROM market_ohlcv")] == [("bitget", now_ms)]
    reset_db()
