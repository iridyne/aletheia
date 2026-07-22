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

# TCP connect timeout for port-liveness probes.  On Windows, connecting to a
# port with no listener does NOT return ECONNREFUSED immediately — it waits
# the full timeout.  Kept short (0.5s) for production; monkeypatched to ~0ms
# in tests via conftest._fast_port_probe.
_PORT_PROBE_TIMEOUT: float = 0.5

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


def _venv_shim_from_syspath() -> Path | None:
    """Find the venv's python shim from sys.path entries, or None.

    The autostart watchdog (autostart._task_arguments) launches the BASE
    pythonw.exe and grafts the venv at runtime via site.addsitedir(<venv>/Lib/
    site-packages). Scan sys.path for that site-packages dir — identified by a
    sibling pyvenv.cfg at the venv root — and return <venv>/Scripts/python.exe,
    the launcher that __PYVENV_LAUNCHER__ should point at so a spawned child
    reactivates the venv at its own startup. Base (non-venv) site-packages dirs
    carry no pyvenv.cfg, so they are correctly skipped.

    Windows-only by construction (the sole caller returns early on POSIX), hence
    the hardcoded Scripts/python.exe layout.
    """
    import sys as _sys

    for entry in _sys.path:
        try:
            p = Path(entry)
        except (TypeError, ValueError):
            continue
        if p.name != "site-packages":
            continue
        venv_root = p.parent.parent  # <venv>/Lib/site-packages → <venv>
        if (venv_root / "pyvenv.cfg").exists():
            shim = venv_root / "Scripts" / "python.exe"
            if shim.exists():
                return shim
    return None


def _resolve_real_python() -> tuple[str, dict]:
    """Resolve the real Python interpreter, bypassing uv venv trampoline shims.

    uv-created venv shims (.venv/Scripts/python.exe) are Console-subsystem
    executables that internally spawn the real python.exe via CreateProcessW
    WITHOUT passing CREATE_NO_WINDOW. This causes a console allocation (and a
    Windows Terminal flash on Win11) even when the parent used DETACHED_PROCESS.

    Returns (real_python_path, env_patch) where env_patch contains
    __PYVENV_LAUNCHER__ so the real Python still finds the venv's site-packages.
    On POSIX or if no venv is detected, returns (sys.executable, {}).

    Two Windows cases — and BOTH must hand the child a venv graft, or the child
    dies with `ModuleNotFoundError: No module named 'aletheia'` → exit 1:
      A) sys.executable IS a venv shim (manual `aletheia gateway start` / `uv
         run`): resolve to base python via pyvenv.cfg, graft via the shim.
      B) sys.executable is the BASE interpreter (the autostart watchdog: Task
         Scheduler runs base pythonw.exe + site.addsitedir): that runtime graft
         lives only in THIS process's sys.path, not in the environment, so a
         spawned child inherits nothing. Recover the venv from sys.path and
         graft via its shim. Missing this case was the silent
         "watchdog-never-revives-the-gateway" bug — every 5-minute tick spawned
         a venv-blind supervisor that exited 1 before the gateway came up.
    """
    import sys as _sys

    if platform.system() != "Windows":
        return (_sys.executable, {})

    exe = Path(_sys.executable).resolve()
    venv_dir = exe.parent.parent  # .venv/Scripts/python.exe → .venv/
    pyvenv_cfg = venv_dir / "pyvenv.cfg"

    if not pyvenv_cfg.exists():
        # Case B: base interpreter (watchdog). Graft the venv recovered from
        # sys.path so the spawned child is not venv-blind.
        venv_shim = _venv_shim_from_syspath()
        if venv_shim is not None:
            return (str(exe), {"__PYVENV_LAUNCHER__": str(venv_shim)})
        return (str(exe), {})

    # Case A: venv shim. Parse pyvenv.cfg 'home' for the real Python directory.
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


def supervisor_owns_pid() -> bool:
    """True if the pidfile points at a DIFFERENT process than ours.

    In supervised mode the supervisor (run_supervised) writes the pidfile with
    its OWN pid, then spawns the inner uvicorn as a child with a different pid.
    So inside the inner process, ``read_pid() != os.getpid()`` ⟺ a supervisor
    owns the pidfile and will respawn us on a non-zero exit. False in
    foreground/bare mode where our own exit is fatal (no one to respawn).

    Lets the health-check job (a scheduler THREAD with no FastAPI app.state)
    make the same "is a supervisor present" decision the restart route makes via
    app.state._owns_pid. Never raises."""
    try:
        pid = read_pid()
        return pid is not None and pid != os.getpid()
    except Exception:
        return False


def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is currently alive AND is our gateway.

    On Windows, uses pure ctypes Win32 API (no subprocess spawn — avoids console
    window flash on Win11). On POSIX, os.kill(pid, 0) + /proc cmdline check.
    """
    if pid <= 0:
        return False
    # The current process is necessarily alive. This also keeps the helper
    # correct in test and embedded-runtime contexts whose command line does
    # not carry a gateway marker.
    if pid == os.getpid():
        return True
    if platform.system() == "Windows":
        from aletheia.gateway._win32_proc import pid_alive_win32
        return pid_alive_win32(pid, (_GATEWAY_SUPERVISOR_MARKER, "uvicorn"))
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_text()
        return _GATEWAY_SUPERVISOR_MARKER in cmdline or "uvicorn" in cmdline
    except Exception:
        return True


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
    Windows, uses pure ctypes TerminateProcess + tree traversal (no taskkill.exe
    spawn — avoids console window flash on Win11). On POSIX the spawn uses
    start_new_session=True, so the supervisor leads its own process group and we
    can signal the group with killpg.
    """
    if pid <= 0:
        return
    try:
        if platform.system() == "Windows":
            from aletheia.gateway._win32_proc import kill_process_tree_win32
            kill_process_tree_win32(pid)
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
            s.settimeout(_PORT_PROBE_TIMEOUT)
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

    On Windows, uses pure ctypes (CreateToolhelp32Snapshot + NtQueryInformation)
    to avoid spawning powershell.exe which can flash a console window on Win11.
    Best-effort: raises only if the underlying tool fails (caller catches).
    """
    if platform.system() == "Windows":
        from aletheia.gateway._win32_proc import list_processes_win32
        return list_processes_win32()

    out = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True, text=True, timeout=20,
    ).stdout
    procs: list[tuple[int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
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


def find_port_holders(port: int) -> list[int]:
    """Find PIDs holding a specific port (LISTEN state). Last resort for orphan cleanup.

    Catches uvicorn multiprocessing workers whose cmdline lacks our markers but
    still hold the gateway socket (inherited from a dead parent). Returns [] on
    any error. Excludes PID 0 and our own PID.
    """
    my_pid = os.getpid()
    pids: set[int] = set()
    try:
        if platform.system() == "Windows":
            raw = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, timeout=10,
                **_no_window_kwargs(),
            ).stdout
            # Decode with replacement so localized netstat bytes (e.g. on
            # Windows UTF-8 runtime) don't raise UnicodeDecodeError (#232).
            # Port/PID fields are ASCII so replacement chars never affect parsing.
            out = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and "LISTENING" in parts[3]:
                    addr = parts[1]
                    if addr.endswith(f":{port}"):
                        try:
                            pid = int(parts[4])
                            if pid > 0 and pid != my_pid:
                                pids.add(pid)
                        except ValueError:
                            pass
        else:
            out = subprocess.run(
                ["lsof", "-nP", "-t", "-sTCP:LISTEN", f"-iTCP:{port}"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                try:
                    pid = int(line.strip())
                    if pid > 0 and pid != my_pid:
                        pids.add(pid)
                except ValueError:
                    pass
    except Exception:
        pass
    return list(pids)
