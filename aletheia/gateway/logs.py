"""Gateway log management.

The gateway's stderr (uvicorn + tracebacks) streams to data/logs/errors.log via a
raw subprocess redirect, so it is not a logging.Handler and a RotatingFileHandler
cannot manage it. Instead we size-cap it explicitly: before each spawn the
supervisor calls rotate_if_needed(), which rolls the file to a single .1 backup
once it exceeds the cap. This bounds a crash-loop that would otherwise fill the
disk. tail_logs() reads only the file's tail so a large log can never OOM the CLI.
"""

import os
from pathlib import Path

from aletheia.config.loader import get_data_dir

# Roll the log once it grows past this size; keep a single previous generation.
_MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MiB
# Bytes to read from the end when tailing (enough for a few hundred lines).
_TAIL_READ_BYTES = 256 * 1024


def log_path() -> Path:
    return get_data_dir() / "logs" / "gateway.log"


def errors_log_path() -> Path:
    return get_data_dir() / "logs" / "errors.log"


def rotate_if_needed(path: Path | None = None, max_bytes: int = _MAX_LOG_BYTES) -> bool:
    """Roll `path` to `path.1` if it exceeds max_bytes. Returns True if rotated.

    Best-effort and never raises: log rotation must not be able to crash the
    supervisor's spawn path. On Windows, a child process holding the file open
    prevents rename — fall back to truncation to bound growth.
    """
    p = path or errors_log_path()
    try:
        if not p.exists() or p.stat().st_size <= max_bytes:
            return False
        backup = p.with_suffix(p.suffix + ".1")
        try:
            if backup.exists():
                backup.unlink()
            os.replace(p, backup)
            return True
        except PermissionError:
            # Windows: another process holds the file open (inherited handle).
            # Truncate in place to prevent unbounded growth.
            p.open("w").close()
            return True
    except OSError:
        return False


def _tail_file(p: Path, lines: int) -> list[str]:
    """Return the last `lines` lines of p, reading only the tail of the file."""
    if not p.exists():
        return []
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > _TAIL_READ_BYTES:
                f.seek(-_TAIL_READ_BYTES, os.SEEK_END)
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-lines:]
    except OSError:
        return []


def tail_logs(lines: int = 50) -> list[str]:
    """Return the last N lines of the gateway log (tail-only read, never loads all)."""
    return _tail_file(log_path(), lines)
