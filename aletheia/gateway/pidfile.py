"""PID file management for the gateway process."""

import os
from pathlib import Path

from aletheia.config.loader import get_data_dir


def pid_path() -> Path:
    return get_data_dir() / "gateway.pid"


def write_pid() -> None:
    pid_path().write_text(str(os.getpid()), encoding="utf-8")


def read_pid() -> int | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def clear_pid() -> None:
    p = pid_path()
    if p.exists():
        p.unlink(missing_ok=True)


def is_running() -> bool:
    """Check if the gateway PID is still alive."""
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        clear_pid()
        return False
