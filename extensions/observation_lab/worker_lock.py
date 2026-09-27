"""Cross-process lock for one complete Observation cycle (Issues #1072, #1386).

One systemd timer + one CLI command yields at most one Worker pass at any time.
Main uses the same lock for its complete cycle and for exceptional rollback
windows, without rewriting the Worker's persisted source/pass signature.

## Contract

- ``worker_pass_lock()`` acquires the exclusive lock and writes PID,
  ``acquired_at`` and Worker source commit for audit.
- ``worker_cycle_exclusion()`` acquires the exact same lock but does not alter
  the signature file contents. Main holds it for each complete cycle and for
  brief integrity-critical exclusion.
- both are fail-fast on contention; neither waits behind another owner.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows only
    fcntl = None

try:
    import msvcrt
except ImportError:
    msvcrt = None

logger = logging.getLogger("aletheia.observation_lab.worker_lock")

_cycle_state = threading.local()


class WorkerCycleLockError(RuntimeError):
    """Raised when another Worker pass/guard already holds the cycle lock."""


def _lock_path() -> Path:
    from extensions.observation_lab.db import lab_db_path

    return lab_db_path().parent / "worker.lock"


def _write_lock_signature(fd: int, *, source_commit: str | None) -> None:
    payload = (
        f"pid={os.getpid()}\n"
        f"acquired_at={datetime.now(timezone.utc).isoformat(timespec='microseconds')}\n"
        f"source_commit={source_commit or 'unknown'}\n"
    ).encode("utf-8")
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
    except OSError as exc:  # pragma: no cover - defensive
        logger.debug("worker_lock: signature write failed (non-fatal): %s", exc)


def _acquire_exclusive(fd: int, lock_path: Path) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkerCycleLockError(
                f"another Observation Worker pass/guard is holding {lock_path} "
                f"({exc}); refusing concurrent access"
            ) from exc
        return
    if msvcrt is not None:  # pragma: no cover - Windows
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise WorkerCycleLockError(
                f"another Observation Worker pass/guard is holding {lock_path} "
                f"({exc}); refusing concurrent access"
            ) from exc
        return
    raise WorkerCycleLockError(
        "no fcntl/msvcrt lock primitive is available; exclusive Worker-cycle "
        "coordination cannot be proven on this host"
    )


def _release_exclusive(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError as exc:  # pragma: no cover - defensive
        logger.debug("worker_lock: release failed (non-fatal): %s", exc)


@contextlib.contextmanager
def _hold_cycle_lock(*, source_commit: str | None = None, write_signature: bool = False):
    lock_path = _lock_path()
    depth = getattr(_cycle_state, "depth", 0)
    if depth:
        if write_signature:
            _write_lock_signature(_cycle_state.fd, source_commit=source_commit)
        _cycle_state.depth = depth + 1
        try:
            yield lock_path
        finally:
            _cycle_state.depth -= 1
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        _acquire_exclusive(fd, lock_path)
        acquired = True
        _cycle_state.depth = 1
        _cycle_state.fd = fd
        if write_signature:
            _write_lock_signature(fd, source_commit=source_commit)
        yield lock_path
    finally:
        _cycle_state.depth = 0
        _cycle_state.fd = None
        if acquired:
            _release_exclusive(fd)
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            logger.debug("observation_lab: closing the cycle-lock fd failed", exc_info=True)


@contextlib.contextmanager
def worker_pass_lock(*, source_commit: str | None = None):
    """Acquire the Observation Worker pass lock and persist its pass signature."""
    with _hold_cycle_lock(source_commit=source_commit, write_signature=True) as lock_path:
        yield lock_path


@contextlib.contextmanager
def worker_cycle_exclusion():
    """Hold the process-wide cycle lock; same-thread nesting is reentrant."""
    with _hold_cycle_lock() as lock_path:
        yield lock_path


__all__ = [
    "WorkerCycleLockError",
    "worker_cycle_exclusion",
    "worker_pass_lock",
]
