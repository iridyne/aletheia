"""PID file management for the gateway process."""

import os
import platform
import subprocess
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


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently alive.

    On POSIX, os.kill(pid, 0) is a liveness probe. On Windows, os.kill calls
    TerminateProcess (it would KILL the process), so we must use tasklist instead.
    """
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_running() -> bool:
    """Check if the gateway PID is still alive."""
    pid = read_pid()
    if pid is None:
        return False
    if _pid_alive(pid):
        return True
    clear_pid()
    return False
