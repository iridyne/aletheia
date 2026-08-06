"""Physical layout helpers for the bounded OHLCV telemetry table.

The logical schema is stable. Existing databases are converted only inside the
explicit offline compaction boundary. Automatic migrations never rebuild this
table, including on fresh installs, so physical optimization cannot lengthen
Gateway startup or violate the additive-migration contract.
"""

from __future__ import annotations

import sqlite3
import time


MARKET_OHLCV_COLUMNS = "exchange,symbol,market_type,timeframe,ts,open,high,low,close,volume,provider"
MARKET_OHLCV_PRIMARY_KEY = (
    "exchange",
    "symbol",
    "market_type",
    "timeframe",
    "ts",
)
MARKET_OHLCV_WITHOUT_ROWID_DDL = """
CREATE TABLE {table_name} (
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
) WITHOUT ROWID
"""

_EXPECTED_EXPLICIT_INDEXES = {
    "idx_market_ohlcv_instrument_tf_ts",
    "idx_market_ohlcv_timeframe_ts",
}
_REBUILD_TABLE = "_aletheia_market_ohlcv_wor_new"


def market_ohlcv_exists(db: sqlite3.Connection) -> bool:
    return db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='market_ohlcv'").fetchone() is not None


def market_ohlcv_is_without_rowid(db: sqlite3.Connection) -> bool:
    """Return the physical table variant without probing the hidden rowid."""

    row = db.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='market_ohlcv'").fetchone()
    return bool(row and "WITHOUT ROWID" in str(row[0]).upper())


def market_ohlcv_storage_profile(db: sqlite3.Connection) -> dict:
    """Return bounded layout facts used by compact planning and verification."""

    if not market_ohlcv_exists(db):
        return {"available": False}
    row_count = int(db.execute("SELECT COUNT(*) FROM market_ohlcv").fetchone()[0])
    objects: list[dict] = []
    try:
        objects = [
            {
                "name": str(row[0]),
                "bytes": int(row[1]),
                "cells": int(row[2]),
                "unused_bytes": int(row[3]),
            }
            for row in db.execute(
                "SELECT name,pgsize,ncell,unused FROM dbstat "
                "WHERE aggregate=TRUE AND name IN ("
                "'market_ohlcv','sqlite_autoindex_market_ohlcv_1',"
                "'idx_market_ohlcv_instrument_tf_ts','idx_market_ohlcv_timeframe_ts') "
                "ORDER BY pgsize DESC"
            )
        ]
    except sqlite3.Error:
        # dbstat is diagnostic-only and may be omitted by a custom SQLite build.
        pass
    return {
        "available": True,
        "without_rowid": market_ohlcv_is_without_rowid(db),
        "row_count": row_count,
        "objects": objects,
        "object_bytes": sum(row["bytes"] for row in objects),
    }


def _assert_expected_market_ohlcv_shape(db: sqlite3.Connection) -> None:
    info = list(db.execute("PRAGMA table_info(market_ohlcv)"))
    columns = [str(row[1]) for row in info]
    primary_key = [str(row[1]) for row in sorted(info, key=lambda row: int(row[5])) if int(row[5])]
    expected_columns = MARKET_OHLCV_COLUMNS.split(",")
    if columns != expected_columns or primary_key != list(MARKET_OHLCV_PRIMARY_KEY):
        raise RuntimeError("market_ohlcv has an unexpected logical schema; refusing physical rebuild")
    explicit_indexes = {
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name='market_ohlcv' AND sql IS NOT NULL"
        )
    }
    unexpected = explicit_indexes - _EXPECTED_EXPLICIT_INDEXES
    if unexpected:
        raise RuntimeError(
            "market_ohlcv has unexpected indexes; refusing physical rebuild: " + ", ".join(sorted(unexpected))
        )
    if db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",
        (_REBUILD_TABLE,),
    ).fetchone():
        raise RuntimeError(f"temporary rebuild table {_REBUILD_TABLE!r} already exists; manual inspection required")


def rebuild_market_ohlcv_without_rowid(db: sqlite3.Connection) -> dict:
    """Atomically rebuild ``market_ohlcv`` as a clustered composite-PK table.

    The caller owns the offline process/cross-process writer permit.  Logical
    equality is checked before the old table is dropped, and every DDL/DML step
    is covered by one rollback-capable SQLite transaction.
    """

    before = market_ohlcv_storage_profile(db)
    if not before.get("available"):
        return {"applied": False, "reason": "table_unavailable", "before": before}
    if before["without_rowid"]:
        return {"applied": False, "reason": "already_without_rowid", "before": before}
    _assert_expected_market_ohlcv_shape(db)

    started = time.perf_counter()
    db.execute("BEGIN IMMEDIATE")
    try:
        db.execute(MARKET_OHLCV_WITHOUT_ROWID_DDL.format(table_name=_REBUILD_TABLE))
        db.execute(
            f"INSERT INTO {_REBUILD_TABLE} ({MARKET_OHLCV_COLUMNS}) "
            f"SELECT {MARKET_OHLCV_COLUMNS} FROM market_ohlcv "
            "ORDER BY exchange,symbol,market_type,timeframe,ts"
        )
        copied = int(db.execute(f"SELECT COUNT(*) FROM {_REBUILD_TABLE}").fetchone()[0])
        if copied != int(before["row_count"]):
            raise RuntimeError(f"market_ohlcv rebuild count mismatch: before={before['row_count']} copied={copied}")
        mismatch = db.execute(
            f"SELECT 1 FROM market_ohlcv AS old LEFT JOIN {_REBUILD_TABLE} AS new ON "
            "new.exchange=old.exchange AND new.symbol=old.symbol "
            "AND new.market_type=old.market_type AND new.timeframe=old.timeframe "
            "AND new.ts=old.ts WHERE "
            "new.exchange IS NULL OR new.open IS NOT old.open OR new.high IS NOT old.high "
            "OR new.low IS NOT old.low OR new.close IS NOT old.close "
            "OR new.volume IS NOT old.volume OR new.provider IS NOT old.provider LIMIT 1"
        ).fetchone()
        if mismatch is not None:
            raise RuntimeError("market_ohlcv rebuild value verification failed")
        db.execute("DROP TABLE market_ohlcv")
        db.execute(f"ALTER TABLE {_REBUILD_TABLE} RENAME TO market_ohlcv")
        db.execute("CREATE INDEX idx_market_ohlcv_timeframe_ts ON market_ohlcv(timeframe,ts)")
        db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise

    after = market_ohlcv_storage_profile(db)
    if int(after.get("row_count", -1)) != int(before["row_count"]):
        raise RuntimeError("market_ohlcv post-commit row-count verification failed")
    return {
        "applied": True,
        "duration_sec": round(time.perf_counter() - started, 3),
        "before": before,
        "after": after,
    }
