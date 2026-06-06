"""PID file management for the gateway process."""

import os
import platform
import signal
import socket
import subprocess
import time
from pathlib import Path

from aletheia.config.loader import get_data_dir

# Markers used to find OUR own gateway processes for orphan cleanup. Two distinct
# roles must both be matched so a still-alive supervisor can't respawn an inner we
# just killed:
#   - INNER  : the uvicorn server (cmdline contains the ASGI app target)
#   - SUPER  : the supervisor (cmdline contains _gateway_runner.py)
# Foreign processes / unrelated uvicorns never match.
_GATEWAY_INNER_MARKER = "aletheia.server.app:app"
_GATEWAY_SUPERVISOR_MARKER = "_gateway_runner"

# Hosts on which `bind` listens broadly but `connect` is invalid/misleading. When
# probing port liveness we redirect these to loopback (the actual reachable name).
_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "*"}

# Win32 process-creation flag that prevents the OS from allocating a console for a
# spawned child. On Win11, an inherited or fresh console for a child of a detached
# parent is hosted via Windows Terminal (`wt.exe`), which flashes a black window
# for every short-lived shell-out (tasklist, schtasks, git, pip, ...). Splat
# **_no_window_kwargs() into every subprocess.run/Popen call reachable from a
# detached parent (the supervisor / inner uvicorn / their children).
_CREATE_NO_WINDOW = 0x08000000


def _no_window_kwargs() -> dict:
    """Return {'creationflags': CREATE_NO_WINDOW} on Windows, {} elsewhere.

    Splat into subprocess.run/Popen kwargs to suppress the OS-allocated console
    window without affecting any other behavior — capture_output, text, timeout,
    cwd, and stdout/stderr redirects all pass through unchanged.
    """
    if platform.system() == "Windows":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def _resolve_real_python() -> tuple[str, dict]:
    """Resolve the real Python interpreter, bypassing uv venv trampoline shims.

    uv-created venv shims (.venv/Scripts/python.exe) are Console-subsystem
    executables that internally spawn the real python.exe via CreateProcessW
    WITHOUT passing CREATE_NO_WINDOW. This causes a console allocation (and a
    Windows Terminal flash on Win11) even when the parent used DETACHED_PROCESS.

    Returns (real_python_path, env_patch) where env_patch contains
    __PYVENV_LAUNCHER__ so the real Python still finds the venv's site-packages.
    On POSIX or if no shim is detected, returns (sys.executable, {}).
    """
    import sys as _sys

    if platform.system() != "Windows":
        return (_sys.executable, {})

    exe = Path(_sys.executable).resolve()
    venv_dir = exe.parent.parent  # .venv/Scripts/python.exe → .venv/
    pyvenv_cfg = venv_dir / "pyvenv.cfg"

    if not pyvenv_cfg.exists():
        return (str(exe), {})

    # Parse pyvenv.cfg for the 'home' key (points to real Python directory).
    home = None
    for line in pyvenv_cfg.read_text(encoding="utf-8").splitlines():
        if line.lower().startswith("home"):
            _, _, val = line.partition("=")
            home = val.strip()
            break

    if not home:
        return (str(exe), {})

    real_python = Path(home) / "python.exe"
    if not real_python.exists():
        return (str(exe), {})

    # __PYVENV_LAUNCHER__ tells CPython's site.py to activate the venv.
    env_patch = {"__PYVENV_LAUNCHER__": str(exe)}
    return (str(real_python), env_patch)


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
                **_no_window_kwargs(),
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
                **_no_window_kwargs(),
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
    """True if something is listening on host:port (connect succeeds).

    Wildcard bind hosts (0.0.0.0 / :: / "") are normalized to loopback before the
    connect probe — connecting TO 0.0.0.0 is invalid on Windows and misleading on
    POSIX, so a server bound to 0.0.0.0 must be probed via 127.0.0.1.
    """
    probe_host = "127.0.0.1" if host in _WILDCARD_HOSTS else host
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((probe_host, port)) == 0
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
            **_no_window_kwargs(),
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


def find_gateway_orphans() -> list[int]:
    """Find PIDs of OUR own gateway processes by command-line marker.

    Returns supervisor PIDs first, then inner uvicorn PIDs. Caller must kill in
    that order so a still-alive supervisor cannot respawn an inner we just killed.
    Only matches processes whose command line contains an Aletheia gateway marker
    (`_gateway_runner` for supervisors, `aletheia.server.app:app` + uvicorn for
    inner servers) — never a foreign uvicorn or unrelated process. Returns [] on
    any error.
    """
    try:
        procs = _list_processes()
    except Exception:
        return []
    supervisors: list[int] = []
    inners: list[int] = []
    for pid, cmd in procs:
        if _GATEWAY_SUPERVISOR_MARKER in cmd:
            supervisors.append(pid)
        elif _GATEWAY_INNER_MARKER in cmd and "uvicorn" in cmd:
            inners.append(pid)
    return supervisors + inners
