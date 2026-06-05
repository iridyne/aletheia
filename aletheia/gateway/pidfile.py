"""PID file management for the gateway process."""

import os
import platform
import signal
import socket
import subprocess
import time
from pathlib import Path

from aletheia.config.loader import get_data_dir

# Identifies the inner uvicorn server processes WE spawn (see _gateway_runner.py
# and lifecycle._spawn_background). Used to find/clean orphans we created without
# ever touching a non-Aletheia process.
_GATEWAY_CMD_MARKER = "aletheia.server.app:app"


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


# --- Process-tree teardown + port helpers (gateway restart hygiene) ---

def kill_pid_tree(pid: int) -> None:
    """Terminate a process AND its children. Never raises.

    The gateway spawns a supervisor whose child is the inner uvicorn server. On
    Windows, killing only the supervisor orphans the uvicorn (it keeps holding the
    port), so we must kill the whole tree with taskkill /T. On POSIX the spawn uses
    start_new_session=True, so the supervisor leads its own process group and we can
    signal the group with killpg.
    """
    if pid <= 0:
        return
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=15,
            )
        else:
            try:
                pgid = os.getpgid(pid)
            except Exception:
                pgid = pid
            try:
                os.killpg(pgid, signal.SIGTERM)
            except Exception:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass  # best-effort teardown; never raise


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is listening on host:port (connect succeeds)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def wait_port_free(port: int, host: str = "127.0.0.1", timeout: float = 10.0) -> bool:
    """Poll until the port is free, up to timeout seconds. True if freed in time."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not port_in_use(port, host):
            return True
        time.sleep(0.2)
    return not port_in_use(port, host)


def _list_processes() -> list[tuple[int, str]]:
    """Return [(pid, command_line)] for running processes. Platform-specific.

    Best-effort: raises only if the underlying tool fails (caller catches).
    """
    if platform.system() == "Windows":
        # CIM gives reliable full command lines; PowerShell is always present on Win.
        ps = (
            "Get-CimInstance Win32_Process | "
            "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=20,
        ).stdout
        sep = "\t"
    else:
        out = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True, text=True, timeout=20,
        ).stdout
        sep = None  # split on whitespace, first token is pid
    procs: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if sep:
            pid_str, _, cmd = line.partition(sep)
        else:
            pid_str, _, cmd = line.partition(" ")
        try:
            procs.append((int(pid_str.strip()), cmd.strip()))
        except ValueError:
            continue
    return procs


def find_gateway_orphans(port: int) -> list[int]:
    """Find PIDs of OUR uvicorn server processes (by command-line marker).

    Only matches processes whose command line contains the Aletheia ASGI app
    target — never a foreign uvicorn or unrelated process. Returns [] on any error.
    The `port` arg is accepted for future narrowing but matching is by app marker,
    which is already unambiguous to this project.
    """
    try:
        procs = _list_processes()
    except Exception:
        return []
    out: list[int] = []
    for pid, cmd in procs:
        if _GATEWAY_CMD_MARKER in cmd and "uvicorn" in cmd:
            out.append(pid)
    return out
