"""Cross-process cycle lock for the Observation Worker (Issue #1072).

One systemd timer + one CLI command yields at most one Worker pass at any
time. But: an operator running ``aletheia observe run`` by hand while the
timer fires would create a duplicate concurrent pass. This file-level
advisory lock prevents that.

Same design shape ``aletheia.storage.sqlite._acquire_file_write_lock``
uses for the app.db writer permit — one owner per host, best-effort,
fail-fast rather than block.

## Contract

- ``worker_pass_lock()`` context manager acquires an exclusive
  ``fcntl.flock`` (Linux/macOS) or ``msvcrt.locking`` (Windows) on
  ``~/.aletheia/observation-lab/worker.lock``.
- On acquisition: writes the current PID + boot commit + ISO timestamp
  to the lock file so ``ps`` / ``cat`` reveals the holder.
- On contention: raises :class:`WorkerCycleLockError`. The command exits
  non-zero and the systemd timer records the failure — the SECOND
  concurrent invocation does NOT wait; it fails so the operator sees a
  clear signal.
- On release: the flock is dropped and the FD closed. The lock file
  itself stays (its size is bounded, its content is helpful audit info).

The lock lives inside the Lab state root so the sibling Worker checkout
sees the same file whether it invokes as source-checkout or systemd. It
is intentionally NOT in ``/var/run`` — the Worker never assumes
system-level tmpfs and never needs root.
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
    """Raised when another Worker pass already holds the cycle lock."""


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


@contextlib.contextmanager
def worker_pass_lock(*, source_commit: str | None = None):
    """Acquire the Observation Worker pass lock. Fail fast on contention.

    Usage:

        with worker_pass_lock(source_commit=head_sha):
            run_controller_observe(...)

    ``source_commit`` is written into the lock file for audit — pass the
    Worker checkout's HEAD SHA so ``cat ~/.aletheia/observation-lab/worker.lock``
    shows which build is currently holding the pass.
    """
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                os.close(fd)
                raise WorkerCycleLockError(
                    f"another Observation Worker pass is holding {lock_path} "
                    f"({exc}); refusing to run a concurrent cycle"
                ) from exc
        elif msvcrt is not None:  # pragma: no cover - Windows
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                os.close(fd)
                raise WorkerCycleLockError(
                    f"another Observation Worker pass is holding {lock_path} "
                    f"({exc}); refusing to run a concurrent cycle"
                ) from exc
        else:  # pragma: no cover - no lock primitive available
            logger.warning(
                "worker_lock: no fcntl/msvcrt available; running without "
                "cross-process protection (Worker isolation on this host is "
                "advisory only)"
            )
        _write_lock_signature(fd, source_commit=source_commit)
        try:
            yield lock_path
        finally:
            try:
                if fcntl is not None:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError as exc:  # pragma: no cover - defensive
                logger.debug("worker_lock: release failed (non-fatal): %s", exc)
    finally:
        try:
            os.close(fd)
        except OSError:  # pragma: no cover
            pass


__all__ = ["WorkerCycleLockError", "worker_pass_lock"]
