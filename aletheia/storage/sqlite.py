"""SQLite connection management and process-local write serialization."""

from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import os
import queue
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, TypeVar

from aletheia.config.loader import get_data_dir

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt byte-range locks.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - POSIX uses flock.
    msvcrt = None

# One connection per OS thread — eliminates "database is locked" / "no transaction is
# active" races between APScheduler background threads and uvicorn async handlers.
_local = threading.local()
_connection_generation = 0
_injected_db_path: Path | None = None
_injected_db_active = False

_logger = logging.getLogger("aletheia.storage.sqlite")
_T = TypeVar("_T")

# Process-level write serialization lock.  Every write transaction acquires this
# before issuing BEGIN so that only one connection can hold the SQLite writer slot
# at a time.  Contention is resolved in Python-land instead of inside SQLite's
# busy_timeout loop, which means "database is locked" can never be raised by normal
# application code.
#
# Timeout philosophy: normal write transactions complete in single-digit milliseconds.
# A timeout in the seconds range means a write transaction is being held open across
# I/O (an await, a network call, or a long computation) — that is a bug in the caller,
# not a capacity problem.  The previous 32 s value allowed broken callers to block
# all other writers for half a minute before failing, compounding the problem.
#
# Current values:
#   _WRITE_LOCK_TIMEOUT_S  = 6 s  (application writer permit; fail fast)
#   PRAGMA busy_timeout    = 30 s (SQLite's last-resort fallback)
#   _acquire_file_write_lock timeout is derived from the same _WRITE_LOCK_TIMEOUT_S
#
# The application permit is the fast-fail signal for a write transaction held across
# I/O. SQLite's longer timeout remains a last-resort guard for contention outside that
# permit. Raise an issue when either fires; do not tune around the underlying holder.
_WRITE_LOCK = threading.Lock()
_WRITE_LOCK_TIMEOUT_S = 6.0
_write_lock_state = threading.local()

_WRITE_QUEUE_CAPACITY = 256
_WRITE_QUEUE_STOP = object()
_MUTATING_SQL = {
    "ALTER",
    "ANALYZE",
    "CREATE",
    "DELETE",
    "DROP",
    "INSERT",
    "REINDEX",
    "REPLACE",
    "UPDATE",
    "VACUUM",
}
_TRANSACTION_START_SQL = {"BEGIN", "SAVEPOINT"}
_TRANSACTION_END_SQL = {"COMMIT", "END", "RELEASE", "ROLLBACK"}
_WITH_WRITE_RE = re.compile(r"\b(?:INSERT|UPDATE|DELETE|REPLACE)\b", re.IGNORECASE)


def acquire_write_lock(*, timeout: float = _WRITE_LOCK_TIMEOUT_S) -> float:
    """Acquire process and cross-process writer permits; return wait time in ms."""

    depth = getattr(_write_lock_state, "depth", 0)
    if depth:
        _write_lock_state.depth = depth + 1
        return 0.0
    started = time.perf_counter()
    if not _WRITE_LOCK.acquire(timeout=timeout):
        waited_ms = (time.perf_counter() - started) * 1000.0
        _logger.warning("sqlite_write_lock_timeout wait_ms=%.1f", waited_ms)
        raise sqlite3.OperationalError("write_lock_timeout: a write transaction was held open too long")
    try:
        _acquire_file_write_lock(started=started, timeout=timeout)
    except BaseException:
        _WRITE_LOCK.release()
        raise
    _write_lock_state.depth = 1
    return (time.perf_counter() - started) * 1000.0


def release_write_lock() -> None:
    """Release one nesting level of the process writer permit."""

    depth = getattr(_write_lock_state, "depth", 0)
    if depth <= 0:
        raise RuntimeError("SQLite write lock released by a non-owner thread")
    if depth > 1:
        _write_lock_state.depth = depth - 1
        return
    _write_lock_state.depth = 0
    file_fd = getattr(_write_lock_state, "file_fd", None)
    if file_fd is not None:
        try:
            if fcntl is not None:
                fcntl.flock(file_fd, fcntl.LOCK_UN)
            elif msvcrt is not None:
                os.lseek(file_fd, 0, os.SEEK_SET)
                msvcrt.locking(file_fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(file_fd)
            _write_lock_state.file_fd = None
    _WRITE_LOCK.release()


def write_lock_held_by_current_thread() -> bool:
    return bool(getattr(_write_lock_state, "depth", 0))


def _acquire_file_write_lock(*, started: float, timeout: float) -> None:
    if (fcntl is None and msvcrt is None) or (_injected_db_active and _injected_db_path is None):
        return
    lock_path = Path(_injected_db_path or db_path()).with_suffix(".write.lock")
    try:
        file_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise sqlite3.OperationalError(f"cannot open SQLite writer lock: {exc}") from exc
    try:
        if msvcrt is not None and os.fstat(file_fd).st_size == 0:
            os.write(file_fd, b"\0")
        while True:
            try:
                os.lseek(file_fd, 0, os.SEEK_SET)
                if fcntl is not None:
                    fcntl.flock(file_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    msvcrt.locking(file_fd, msvcrt.LK_NBLCK, 1)
                _write_lock_state.file_fd = file_fd
                return
            except (BlockingIOError, OSError):
                if time.perf_counter() - started >= timeout:
                    waited_ms = (time.perf_counter() - started) * 1000.0
                    _logger.warning("sqlite_cross_process_write_lock_timeout wait_ms=%.1f", waited_ms)
                    raise sqlite3.OperationalError(
                        "write_lock_timeout: another process held the SQLite writer permit"
                    )
                time.sleep(0.01)
    except BaseException:
        os.close(file_fd)
        raise


def _sql_token(sql: str) -> str:
    stripped = sql.lstrip()
    while stripped.startswith("--"):
        _, separator, stripped = stripped.partition("\n")
        if not separator:
            return ""
        stripped = stripped.lstrip()
    return stripped.partition(" ")[0].partition("\n")[0].upper()


def _is_mutating_sql(sql: str) -> bool:
    token = _sql_token(sql)
    if token in _MUTATING_SQL:
        return True
    if token == "WITH":
        return bool(_WITH_WRITE_RE.search(sql))
    if token == "PRAGMA":
        normalized = sql.upper()
        return "=" in normalized or any(name in normalized for name in ("JOURNAL_MODE", "WAL_CHECKPOINT", "OPTIMIZE"))
    return False


class _SerializedConnection(sqlite3.Connection):
    """Guard legacy direct writes that have not yet moved to the writer lane."""

    _owns_transaction_lock = False

    def execute(self, sql, parameters=(), /):
        token = _sql_token(sql)
        if token in _TRANSACTION_START_SQL and not self.in_transaction:
            if write_lock_held_by_current_thread():
                return super().execute(sql, parameters)
            acquire_write_lock()
            self._owns_transaction_lock = True
            try:
                return super().execute(sql, parameters)
            except BaseException:
                self._release_owned_lock()
                raise
        if token in _TRANSACTION_END_SQL:
            try:
                return super().execute(sql, parameters)
            finally:
                if not self.in_transaction:
                    self._release_owned_lock()
        if self.in_transaction or not _is_mutating_sql(sql):
            return super().execute(sql, parameters)
        wait_ms = acquire_write_lock()
        try:
            _record_writer_metric("legacy_direct_writes", 1)
            _record_writer_max("max_lock_wait_ms", wait_ms)
            return super().execute(sql, parameters)
        finally:
            release_write_lock()

    def executemany(self, sql, seq_of_parameters, /):
        if self.in_transaction or not _is_mutating_sql(sql):
            return super().executemany(sql, seq_of_parameters)
        wait_ms = acquire_write_lock()
        try:
            _record_writer_metric("legacy_direct_writes", 1)
            _record_writer_max("max_lock_wait_ms", wait_ms)
            return super().executemany(sql, seq_of_parameters)
        finally:
            release_write_lock()

    def executescript(self, sql_script, /):
        if self.in_transaction:
            return super().executescript(sql_script)
        wait_ms = acquire_write_lock()
        try:
            _record_writer_metric("legacy_direct_writes", 1)
            _record_writer_max("max_lock_wait_ms", wait_ms)
            return super().executescript(sql_script)
        finally:
            release_write_lock()

    def commit(self):
        try:
            return super().commit()
        finally:
            self._release_owned_lock()

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_owned_lock()

    def close(self):
        try:
            if self.in_transaction:
                super().rollback()
        finally:
            self._release_owned_lock()
        return super().close()

    def _release_owned_lock(self) -> None:
        if self._owns_transaction_lock:
            self._owns_transaction_lock = False
            release_write_lock()


def _open_connection(path: Path) -> sqlite3.Connection:
    initialize_incremental_vacuum = not path.exists() or path.stat().st_size == 0
    conn = sqlite3.connect(
        str(path),
        check_same_thread=True,
        timeout=30,
        isolation_level=None,
        factory=_SerializedConnection,
    )
    conn.row_factory = sqlite3.Row
    # SQLite can enable auto_vacuum without rewriting pages only before the
    # first schema object exists. Existing databases require the explicit,
    # offline `aletheia data compact --apply` maintenance boundary.
    if initialize_incremental_vacuum:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")  # 30 s — last-resort SQLite fallback
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


@dataclass
class _WriteJob:
    operation: Callable[[sqlite3.Connection], object]
    future: Future
    enqueued_at: float
    immediate: bool


_writer_metrics_lock = threading.Lock()
_writer_metrics: dict[str, float | int] = {
    "submitted": 0,
    "completed": 0,
    "failed": 0,
    "admission_timeouts": 0,
    "legacy_direct_writes": 0,
    "max_queue_depth": 0,
    "max_queue_wait_ms": 0.0,
    "max_lock_wait_ms": 0.0,
    "max_transaction_ms": 0.0,
}


def _record_writer_metric(name: str, amount: int) -> None:
    with _writer_metrics_lock:
        _writer_metrics[name] = int(_writer_metrics.get(name, 0)) + amount


def _record_writer_max(name: str, value: float | int) -> None:
    with _writer_metrics_lock:
        _writer_metrics[name] = max(float(_writer_metrics.get(name, 0.0)), float(value))


class _SQLiteWriterLane:
    """Bounded synchronous lane backed by one dedicated SQLite connection."""

    def __init__(self, path: Path, *, capacity: int = _WRITE_QUEUE_CAPACITY) -> None:
        self._path = path
        self._capacity = capacity
        self._queue: queue.Queue[_WriteJob | object] = queue.Queue(maxsize=capacity)
        self._thread = threading.Thread(
            target=self._run,
            name="aletheia-sqlite-writer",
            daemon=True,
        )
        self._started = threading.Event()
        self._startup_error: BaseException | None = None
        self._closed = False
        self._state_lock = threading.Lock()
        self._thread.start()
        if not self._started.wait(timeout=_WRITE_LOCK_TIMEOUT_S + 5):
            raise RuntimeError("SQLite writer lane did not start")
        if self._startup_error is not None:
            raise RuntimeError("SQLite writer lane failed to open its connection") from self._startup_error

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def thread_alive(self) -> bool:
        return self._thread.is_alive()

    def submit(
        self,
        operation: Callable[[sqlite3.Connection], _T],
        *,
        immediate: bool,
    ) -> _T:
        future: Future[_T] = Future()
        job = _WriteJob(
            operation=operation,
            future=future,
            enqueued_at=time.perf_counter(),
            immediate=immediate,
        )
        with self._state_lock:
            if self._closed:
                raise RuntimeError("SQLite writer lane is closed")
            try:
                self._queue.put(job, timeout=_WRITE_LOCK_TIMEOUT_S)
            except queue.Full as exc:
                _record_writer_metric("admission_timeouts", 1)
                _logger.warning(
                    "sqlite_write_queue_timeout capacity=%d depth=%d",
                    self._capacity,
                    self.queue_depth,
                )
                raise sqlite3.OperationalError(
                    "write_queue_timeout: SQLite writer queue remained full"
                ) from exc
        _record_writer_metric("submitted", 1)
        _record_writer_max("max_queue_depth", self.queue_depth)
        # Once accepted, wait for a definitive commit or rollback. Timing out here
        # would leave the caller unable to know whether an authoritative write landed.
        return future.result()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._queue.put(_WRITE_QUEUE_STOP)
        self._thread.join(timeout=5)
        if self._thread.is_alive():
            raise RuntimeError("SQLite writer lane did not stop cleanly")

    def _run(self) -> None:
        try:
            conn = _open_connection(self._path)
        except BaseException as exc:
            self._startup_error = exc
            self._started.set()
            return
        _local.conn = conn
        _local.generation = _connection_generation
        self._started.set()
        try:
            while True:
                job = self._queue.get()
                try:
                    if job is _WRITE_QUEUE_STOP:
                        return
                    assert isinstance(job, _WriteJob)
                    self._execute_job(conn, job)
                finally:
                    self._queue.task_done()
        finally:
            conn.close()
            _local.conn = None

    def _execute_job(self, conn: sqlite3.Connection, job: _WriteJob) -> None:
        queue_wait_ms = (time.perf_counter() - job.enqueued_at) * 1000.0
        _record_writer_max("max_queue_wait_ms", queue_wait_ms)
        wait_ms = 0.0
        acquired_at = time.perf_counter()
        outcome = "begin_failed"
        try:
            wait_ms = acquire_write_lock()
            acquired_at = time.perf_counter()
            _record_writer_max("max_lock_wait_ms", wait_ms)
            conn.execute("BEGIN IMMEDIATE" if job.immediate else "BEGIN")
            try:
                result = job.operation(conn)
                conn.commit()
                outcome = "committed"
            except BaseException:
                outcome = "rolled_back"
                conn.rollback()
                raise
        except BaseException as exc:
            _record_writer_metric("failed", 1)
            job.future.set_exception(exc)
        else:
            _record_writer_metric("completed", 1)
            job.future.set_result(result)
        finally:
            if write_lock_held_by_current_thread():
                transaction_ms = (time.perf_counter() - acquired_at) * 1000.0
                _record_writer_max("max_transaction_ms", transaction_ms)
                release_write_lock()
                verbose = os.environ.get("ALETHEIA_SQLITE_TIMING", "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                log_args = (queue_wait_ms, wait_ms, transaction_ms, outcome)
                if queue_wait_ms >= 100.0 or transaction_ms >= 500.0:
                    _logger.warning(
                        "sqlite_writer_slow queue_wait_ms=%.1f lock_wait_ms=%.1f transaction_ms=%.1f outcome=%s",
                        *log_args,
                    )
                elif verbose:
                    _logger.info(
                        "sqlite_writer queue_wait_ms=%.1f lock_wait_ms=%.1f transaction_ms=%.1f outcome=%s",
                        *log_args,
                    )


_writer_lane_lock = threading.Lock()
_writer_lane: _SQLiteWriterLane | None = None
_writer_lane_pid: int | None = None


def _get_writer_lane() -> _SQLiteWriterLane:
    global _writer_lane, _writer_lane_pid

    pid = os.getpid()
    with _writer_lane_lock:
        if _writer_lane_pid is not None and _writer_lane_pid != pid:
            _writer_lane = None
        if _writer_lane is None:
            _writer_lane = _SQLiteWriterLane(Path(_injected_db_path or db_path()))
            _writer_lane_pid = pid
        return _writer_lane


def run_write(
    operation: Callable[[sqlite3.Connection], _T],
    *,
    immediate: bool = False,
) -> _T:
    """Run a short database-only closure atomically and return its exact result."""

    db = get_db()
    if db.in_transaction:
        return operation(db)
    # Tests explicitly inject connections with deliberate PRAGMA choices. Keep
    # their writes on that exact connection instead of silently opening a second
    # production-configured connection with different constraint semantics.
    if _injected_db_active:
        wait_ms = acquire_write_lock()
        _record_writer_max("max_lock_wait_ms", wait_ms)
        started = time.perf_counter()
        try:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                result = operation(db)
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise
        finally:
            _record_writer_max("max_transaction_ms", (time.perf_counter() - started) * 1000.0)
            release_write_lock()
    return _get_writer_lane().submit(operation, immediate=immediate)


@dataclass(frozen=True)
class WriteResult:
    """Thread-safe subset of sqlite3.Cursor metadata used by write callers."""

    rowcount: int
    lastrowid: int | None


def execute_write(
    sql: str,
    parameters=(),
    *,
    immediate: bool = False,
) -> WriteResult:
    """Execute one mutating statement through the writer lane."""

    if not _is_mutating_sql(sql):
        raise ValueError("execute_write accepts only mutating SQL")

    def write(db: sqlite3.Connection) -> WriteResult:
        cursor = db.execute(sql, parameters)
        return WriteResult(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)

    return run_write(write, immediate=immediate)


def execute_many_write(
    sql: str,
    seq_of_parameters,
    *,
    immediate: bool = False,
) -> WriteResult:
    """Execute one homogeneous mutating batch through the writer lane."""

    if not _is_mutating_sql(sql):
        raise ValueError("execute_many_write accepts only mutating SQL")
    values = list(seq_of_parameters)

    def write(db: sqlite3.Connection) -> WriteResult:
        cursor = db.executemany(sql, values)
        return WriteResult(rowcount=cursor.rowcount, lastrowid=cursor.lastrowid)

    return run_write(write, immediate=immediate)


def writer_metrics_snapshot() -> dict[str, float | int | bool]:
    with _writer_metrics_lock:
        metrics: dict[str, float | int | bool] = dict(_writer_metrics)
    lane = _writer_lane
    metrics.update(
        {
            "capacity": lane.capacity if lane is not None else _WRITE_QUEUE_CAPACITY,
            "queue_depth": lane.queue_depth if lane is not None else 0,
            "thread_alive": bool(lane and lane.thread_alive),
        }
    )
    return metrics


def shutdown_writer() -> None:
    global _writer_lane, _writer_lane_pid

    with _writer_lane_lock:
        lane = _writer_lane
        _writer_lane = None
        _writer_lane_pid = None
    if lane is not None:
        lane.close()


def db_path() -> Path:
    return get_data_dir() / "app.db"


@contextmanager
def readonly_db():
    """Open the effective database without creation or write-oriented PRAGMAs.

    File-backed test injection follows the injected path. In-memory injection has
    no second connection to open, so it yields the existing test connection and
    deliberately leaves its lifecycle with the fixture.
    """

    if _injected_db_active and _injected_db_path is None:
        yield get_db()
        return

    path = Path(_injected_db_path or db_path())
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


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
        conn = _open_connection(Path(_injected_db_path or db_path()))
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
    global _connection_generation, _injected_db_path, _injected_db_active

    shutdown_writer()
    close_db()
    _connection_generation += 1
    _injected_db_path = None
    _injected_db_active = False


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
        global _connection_generation, _injected_db_path, _injected_db_active

        shutdown_writer()
        _local.conn = value
        _connection_generation += 1
        _local.generation = _connection_generation
        _injected_db_active = value is not None
        if value is None:
            _injected_db_path = None
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
