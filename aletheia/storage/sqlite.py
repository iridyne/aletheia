"""SQLite database initialization and connection management."""

import sqlite3
import threading
from pathlib import Path

from aletheia.config.loader import get_data_dir

# One connection per OS thread — eliminates "database is locked" / "no transaction is
# active" races between APScheduler background threads and uvicorn async handlers.
_local = threading.local()
_connection_generation = 0
_injected_db_path: Path | None = None

# Process-level write serialization lock.  Every write transaction acquires this
# before issuing BEGIN so that only one connection can hold the SQLite writer slot
# at a time.  Contention is resolved in Python-land instead of inside SQLite's
# busy_timeout loop, which means "database is locked" can never be raised by normal
# application code.  Timeout is set slightly above busy_timeout (30 s) as a last
# resort; if it fires it means a write transaction was held open across I/O — a bug
# in the caller, not a concurrency problem.
_WRITE_LOCK = threading.Lock()
_WRITE_LOCK_TIMEOUT_S = 32.0


def db_path() -> Path:
    return get_data_dir() / "app.db"


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "generation", -1) != _connection_generation:
        conn.close()
        conn = None
        _local.conn = None
    if conn is None:
        # isolation_level=None → true autocommit. Each DML statement commits
        # immediately unless wrapped in an explicit BEGIN/COMMIT block (via the
        # transaction() context manager in repositories.py). This eliminates the
        # class of bugs where an abandoned daemon thread holds an implicitly-opened
        # transaction forever, permanently locking the WAL writer slot.
        #
        # Previously (default isolation_level=""), Python auto-issued BEGIN before
        # every INSERT/UPDATE/DELETE. If commit() never ran (thread abandoned by
        # _run_sync_with_timeout, or exception path missed), the connection held
        # in_transaction=True until GC — which never happens for thread-local conns
        # on daemon threads. Under uvicorn this caused permanent "database is locked".
        conn = sqlite3.connect(
            str(_injected_db_path or db_path()), check_same_thread=True, timeout=30,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # synchronous=NORMAL: WAL-safe write throughput tier (FULL is overkill in WAL).
        # busy_timeout: SQLite-level retry on SQLITE_BUSY (matches connection timeout).
        # wal_autocheckpoint: bound the WAL file before checkpoint flushes it.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        _local.conn = conn
        _local.generation = _connection_generation
    return conn


def close_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn:
        conn.close()
        _local.conn = None


def rollback_if_in_transaction() -> bool:
    """Rollback this thread's open SQLite transaction, if any.

    Scheduler jobs run on reusable worker threads. If a job path leaks an open
    transaction after an exception, that worker thread can hold SQLite's writer
    lock across future jobs and make otherwise healthy routes fail with
    ``database is locked``. This helper is intentionally local to the current
    thread/connection; it never touches other processes or trading state.
    """
    conn = getattr(_local, "conn", None)
    if conn is None or not conn.in_transaction:
        return False
    conn.rollback()
    return True


def reset_db() -> None:
    """Reset connection for this thread (for testing)."""
    global _connection_generation, _injected_db_path

    close_db()
    _connection_generation += 1
    _injected_db_path = None


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
        global _connection_generation, _injected_db_path

        _local.conn = value
        _connection_generation += 1
        _local.generation = _connection_generation
        if value is None:
            return
        # Legacy tests inject a file-backed connection on the ASGI loop thread.
        # Sync FastAPI endpoints correctly run in worker threads, so retain only
        # the file path and let every worker open its own production-like
        # thread-local connection. Autocommit mirrors get_db().
        value.isolation_level = None
        try:
            row = value.execute("PRAGMA database_list").fetchone()
            raw_path = row[2] if row else ""
            _injected_db_path = Path(raw_path) if raw_path else None
        except sqlite3.Error:
            _injected_db_path = None

    def __delete__(self, obj):
        _local.conn = None


import sys as _sys
_mod = _sys.modules[__name__]

class _Module(type(_mod)):
    _db_conn = _DbConnDescriptor()

_mod.__class__ = _Module
