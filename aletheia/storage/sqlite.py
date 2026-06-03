"""SQLite database initialization and connection management."""

import sqlite3
from pathlib import Path

from aletheia.config.loader import get_data_dir

_db_conn: sqlite3.Connection | None = None


def db_path() -> Path:
    return get_data_dir() / "app.db"


def get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(str(db_path()), check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _db_conn.execute("PRAGMA journal_mode=WAL")
        _db_conn.execute("PRAGMA foreign_keys=ON")
    return _db_conn


def close_db() -> None:
    global _db_conn
    if _db_conn:
        _db_conn.close()
        _db_conn = None


def reset_db() -> None:
    """Reset connection (for testing)."""
    global _db_conn
    _db_conn = None
