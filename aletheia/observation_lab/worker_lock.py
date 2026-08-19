"""Cross-process cycle lock for the Observation Worker (Issue #1072).

One systemd timer + one CLI command yields at most one Worker pass at any time.
The same lock also provides a short non-mutating exclusion guard for exceptional
Main rollback windows: it prevents a new Worker pass from starting while Main
restores its own pre-update state, without rewriting the Worker's persisted
source/pass signature.

## Contract

- ``worker_pass_lock()`` acquires the exclusive lock and writes PID,
  ``acquired_at`` and Worker source commit for audit.
- ``worker_cycle_exclusion()`` acquires the exact same lock but does not alter
  the signature file contents. It is for brief integrity-critical exclusion,
  not normal Worker scheduling.
- both are fail-fast on contention; neither waits behind another owner.
"""

from __future__ import annotations

import contextlib
import logging
import os
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


class WorkerCycleLockError(RuntimeError):
    """Raised when another Worker pass/guard already holds the cycle lock."""


def _lock_path() -> Path:
    from aletheia.observation_lab.db import lab_db_path

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
def worker_pass_lock(*, source_commit: str | None = None):
    """Acquire the Observation Worker pass lock and persist its pass signature."""
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            _acquire_exclusive(fd, lock_path)
            acquired = True
        except WorkerCycleLockError:
            raise
        _write_lock_signature(fd, source_commit=source_commit)
        yield lock_path
    finally:
        if acquired:
            _release_exclusive(fd)
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


@contextlib.contextmanager
def worker_cycle_exclusion():
    """Hold the Worker cycle lock without changing its persisted signature.

    Intended for the exceptional Main rollback window only. The caller can read
    the old signature while this guard is held and know that no new Worker pass
    can start until the guarded operation completes.
    """
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        _acquire_exclusive(fd, lock_path)
        acquired = True
        yield lock_path
    finally:
        if acquired:
            _release_exclusive(fd)
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


__all__ = [
    "WorkerCycleLockError",
    "worker_cycle_exclusion",
    "worker_pass_lock",
]
