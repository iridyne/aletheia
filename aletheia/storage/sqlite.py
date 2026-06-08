"""SQLite database initialization and connection management."""

import sqlite3
import threading
from pathlib import Path

from aletheia.config.loader import get_data_dir

# One connection per OS thread — eliminates "database is locked" / "no transaction is
# active" races between APScheduler background threads and uvicorn async handlers.
_local = threading.local()


def db_path() -> Path:
    return get_data_dir() / "app.db"


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        # timeout=30: retry up to 30 s on SQLITE_BUSY before raising, avoids transient
        # lock errors during concurrent writes from scheduler + API handlers.
        conn = sqlite3.connect(str(db_path()), check_same_thread=True, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def close_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn:
        conn.close()
        _local.conn = None


def reset_db() -> None:
    """Reset connection for this thread (for testing)."""
    close_db()


# ---------------------------------------------------------------------------
# Test-compatibility shim: tests set sq._db_conn = conn to inject a test DB.
# The production code uses threading.local, so we bridge the two via a module-
# level descriptor that forwards reads/writes to _local.conn.
# ---------------------------------------------------------------------------

class _DbConnDescriptor:
    """Module-level attribute that proxies to threading.local storage."""
    def __get__(self, obj, objtype=None):
        return getattr(_local, "conn", None)

    def __set__(self, obj, value):
        _local.conn = value

    def __delete__(self, obj):
        _local.conn = None


import sys as _sys
_mod = _sys.modules[__name__]

class _Module(type(_mod)):
    _db_conn = _DbConnDescriptor()

_mod.__class__ = _Module
