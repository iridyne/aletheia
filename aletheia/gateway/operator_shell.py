"""Operator shell spawn helpers and supervision."""

from contextlib import contextmanager
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from aletheia.config.loader import get_data_dir

CREATE_NO_WINDOW = 0x08000000


def operator_shell_root() -> Path:
    return Path(__file__).resolve().parents[2] / "operator-shell"


def build_operator_shell_command(cfg) -> list[str] | None:
    root = operator_shell_root()
    cli = root / "node_modules" / "tsx" / "dist" / "cli.mjs"
    entry = root / "src" / "index.ts"
    if not cli.exists() or not entry.exists():
        return None
    return ["node", str(cli), str(entry)]


def _current_runtime_id() -> str:
    try:
        from aletheia.runtime_continuity.service import RuntimeContinuityService

        return RuntimeContinuityService().current_runtime_id()
    except Exception:
        return "runtime-local-current"


def _build_operator_shell_env(
    cfg,
    *,
    runtime_id: str | None = None,
    runtime_mode: str = "current",
    runtime_state: str = "current",
) -> dict[str, str]:
    effective_runtime_id = runtime_id or _current_runtime_id()
    return {
        "ALETHEIA_GATEWAY_BASE_URL": f"http://{cfg.app.bind_host}:{cfg.app.dashboard_port}",
        "ALETHEIA_OPERATOR_PERMISSION_MODE": cfg.operator.permission_mode,
        "ALETHEIA_OPERATOR_HEARTBEAT_SEC": str(cfg.operator.heartbeat_interval_sec),
        "ALETHEIA_OPERATOR_MAIN_SESSION_ID": cfg.operator.main_session_id,
        "ALETHEIA_RUNTIME_ID": effective_runtime_id,
        "ALETHEIA_RUNTIME_MODE": runtime_mode,
        "ALETHEIA_RUNTIME_STATE": runtime_state,
    }


def operator_shell_log_path() -> Path:
    return get_data_dir() / "logs" / "operator-shell-errors.log"


def operator_shell_pid_path() -> Path:
    return get_data_dir() / "operator-shell.pid"


def _operator_shell_pid_lock_path() -> Path:
    return get_data_dir() / ".operator-shell.pid.lock"


def _read_operator_shell_pid() -> int | None:
    path = operator_shell_pid_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_operator_shell_pid(pid: int) -> None:
    path = operator_shell_pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def _clear_operator_shell_pid(expected_pid: int | None = None) -> None:
    path = operator_shell_pid_path()
    if not path.exists():
        return
    if expected_pid is not None and _read_operator_shell_pid() != expected_pid:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        try:
            from aletheia.gateway._win32_proc import pid_alive_win32

            return pid_alive_win32(pid, ("",))
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_process_command_line(pid: int) -> str | None:
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return None
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    try:
        if proc_cmdline.exists():
            raw = proc_cmdline.read_bytes()
            return raw.replace(b"\0", b" ").decode("utf-8", errors="replace")
    except OSError:
        pass
    try:
        return subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except Exception:
        return None


def _operator_shell_markers(cmd: list[str]) -> tuple[str, ...]:
    markers = ["operator-shell"]
    if len(cmd) > 1:
        markers.append(Path(cmd[1]).name)
    if len(cmd) > 2:
        markers.append(Path(cmd[2]).name)
    return tuple(marker for marker in markers if marker)


def _operator_shell_process_matches(pid: int, cmd: list[str]) -> bool:
    """True when pid appears to be this project's operator shell process."""
    markers = _operator_shell_markers(cmd)
    if not markers:
        return False
    if platform.system() == "Windows":
        try:
            from aletheia.gateway._win32_proc import pid_alive_win32

            return all(pid_alive_win32(pid, (marker, marker.replace("/", "\\"))) for marker in markers)
        except Exception:
            return False
    cmdline = _read_process_command_line(pid)
    if not cmdline:
        return False
    normalized = cmdline.replace("\\", "/").lower()
    return all(marker.replace("\\", "/").lower() in normalized for marker in markers)


def _normalized_cmdline(value: str) -> str:
    return value.replace("\\", "/").lower()


def _direct_operator_shell_cmdline_match(cmdline: str) -> bool:
    normalized = _normalized_cmdline(cmdline)
    root_marker = str(operator_shell_root()).replace("\\", "/").lower()
    if "--once" in normalized:
        return False
    return (
        root_marker in normalized
        and "operator-shell" in normalized
        and "index.ts" in normalized
        and ("node_modules/tsx" in normalized or "cli.mjs" in normalized or "loader.mjs" in normalized)
    )


def _relative_tsx_parent_cmdline_match(cmdline: str) -> bool:
    normalized = _normalized_cmdline(cmdline)
    if "--once" in normalized:
        return False
    return "cli.mjs" in normalized and "src/index.ts" in normalized


def _operator_shell_process_roots(
    processes: list[tuple[int, int, str]],
) -> list[int]:
    """Return root PIDs for this project's current operator-shell processes.

    The Python supervisor launches tsx with absolute paths, but a manual
    `npm start` from operator-shell/ produces a relative parent command line
    (`node_modules/tsx/dist/cli.mjs src/index.ts`). Its child still contains the
    absolute project path via the tsx loader. We therefore match direct project
    command lines first, then pull in their tsx parent so killing/adopting a
    process tree works reliably on Windows.
    """
    by_pid = {pid: (parent_pid, cmdline) for pid, parent_pid, cmdline in processes}
    matched: set[int] = {
        pid for pid, _parent_pid, cmdline in processes if _direct_operator_shell_cmdline_match(cmdline)
    }

    for pid in list(matched):
        parent_pid, _cmdline = by_pid.get(pid, (0, ""))
        parent = by_pid.get(parent_pid)
        if parent is not None and _relative_tsx_parent_cmdline_match(parent[1]):
            matched.add(parent_pid)

    for pid, parent_pid, cmdline in processes:
        if parent_pid in matched and _direct_operator_shell_cmdline_match(cmdline):
            matched.add(pid)

    roots = [
        pid
        for pid in matched
        if by_pid.get(pid, (0, ""))[0] not in matched
    ]
    return sorted(roots)


def _list_operator_shell_processes() -> list[tuple[int, int, str]]:
    if platform.system() == "Windows":
        try:
            from aletheia.gateway._win32_proc import list_processes_by_exe_win32

            return list_processes_by_exe_win32({"node.exe"})
        except Exception:
            return []

    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,args="],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except Exception:
        return []

    rows: list[tuple[int, int, str]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_str, _, rest = line.partition(" ")
        parent_str, _, cmdline = rest.strip().partition(" ")
        try:
            rows.append((int(pid_str), int(parent_str), cmdline.strip()))
        except ValueError:
            continue
    return rows


def _cleanup_unmanaged_operator_shells(keep_pid: int | None = None) -> None:
    """Kill duplicate/current operator-shell process trees outside keep_pid."""
    try:
        roots = _operator_shell_process_roots(_list_operator_shell_processes())
    except Exception:
        return
    if not roots:
        return

    try:
        from aletheia.gateway.pidfile import kill_pid_tree
    except Exception:
        return

    for pid in roots:
        if keep_pid is not None and pid == keep_pid:
            continue
        kill_pid_tree(pid)
        _clear_operator_shell_pid(expected_pid=pid)


@contextmanager
def _exclusive_pidfile_lock():
    lock_path = _operator_shell_pid_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if platform.system() == "Windows":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class ExistingOperatorShellProcess:
    """Popen-like handle for an already running operator shell we do not own."""

    external = True

    def __init__(self, pid: int, cmd: list[str] | None = None):
        self.pid = pid
        self._cmd = cmd

    def poll(self):
        alive = _pid_exists(self.pid)
        if alive and self._cmd is not None:
            alive = _operator_shell_process_matches(self.pid, self._cmd)
        return None if alive else 0

    def terminate(self) -> None:
        return None

    def wait(self, timeout=None):  # noqa: ARG002
        return None if self.poll() is None else 0

    def kill(self) -> None:
        return None


def spawn_operator_shell(
    cfg,
    *,
    runtime_id: str | None = None,
    runtime_mode: str = "current",
    runtime_state: str = "current",
    once: bool = False,
):
    cmd = build_operator_shell_command(cfg)
    if cmd is None:
        return None
    if once and "--once" not in cmd:
        cmd = [*cmd, "--once"]

    def _spawn_new():
        log_path = operator_shell_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update(
            _build_operator_shell_env(
                cfg,
                runtime_id=runtime_id,
                runtime_mode=runtime_mode,
                runtime_state=runtime_state,
            )
        )
        if once:
            env["ALETHEIA_OPERATOR_ONCE"] = "1"
        popen_kwargs: dict = {
            "cwd": str(operator_shell_root()),
            "stdout": subprocess.DEVNULL,
            "env": env,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW

        with log_path.open("a", encoding="utf-8") as log_file:
            popen_kwargs["stderr"] = log_file
            return subprocess.Popen(cmd, **popen_kwargs)

    if not once:
        with _exclusive_pidfile_lock():
            existing_pid = _read_operator_shell_pid()
            if existing_pid is not None:
                if _pid_exists(existing_pid) and _operator_shell_process_matches(existing_pid, cmd):
                    _cleanup_unmanaged_operator_shells(keep_pid=existing_pid)
                    return ExistingOperatorShellProcess(existing_pid, cmd)
                _clear_operator_shell_pid(expected_pid=existing_pid)

            _cleanup_unmanaged_operator_shells()
            proc = _spawn_new()
            _write_operator_shell_pid(proc.pid)
            return proc

    return _spawn_new()


def spawn_shadow_operator_shell(cfg, runtime_id: str):
    return spawn_operator_shell(
        cfg,
        runtime_id=runtime_id,
        runtime_mode="shadow",
        runtime_state="shadowing",
        once=True,
    )


def _default_record_event(kind: str, payload: dict) -> None:
    try:
        from aletheia.files.jsonl import write_audit, write_error

        if kind == "operator_shell_degraded":
            write_audit(
                {
                    "actor": "system",
                    "source": "operator_shell",
                    "event_type": "operator_shell_degraded",
                    "entity_type": "operator",
                    "entity_id": "operator",
                    "message": payload.get("message", "operator shell degraded"),
                }
            )
            write_error(
                {
                    "source": "operator_shell",
                    "message": payload.get("message", "operator shell degraded"),
                }
            )
        elif kind == "operator_shell_restart":
            write_audit(
                {
                    "actor": "system",
                    "source": "operator_shell",
                    "event_type": "operator_shell_restart",
                    "entity_type": "operator",
                    "entity_id": "operator",
                    "message": payload.get("message", "operator shell restarted"),
                }
            )
    except Exception:
        pass


class OperatorShellMonitor:
    def __init__(
        self,
        cfg,
        *,
        spawn_fn: Callable | None = None,
        now_fn: Callable[[], float] | None = None,
        record_event: Callable[[str, dict], None] | None = None,
        poll_interval_sec: float = 1.0,
    ):
        self.cfg = cfg
        self.spawn_fn = spawn_fn or spawn_operator_shell
        self.now_fn = now_fn or time.monotonic
        self.record_event = record_event or _default_record_event
        self.poll_interval_sec = poll_interval_sec
        self.proc = None
        self.degraded_until = 0.0
        self._failure_count = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def tick_once(self) -> None:
        if not self.cfg.operator.enabled:
            return

        now = self.now_fn()
        if self.proc is None:
            if now < self.degraded_until:
                return
            self.proc = self.spawn_fn(self.cfg)
            return

        code = self.proc.poll()
        if code is None:
            return

        proc_pid = getattr(self.proc, "pid", None)
        self.proc = None
        if proc_pid is not None:
            _clear_operator_shell_pid(expected_pid=proc_pid)
        if code == 0:
            self._failure_count = 0
            return

        self._failure_count += 1
        if self._failure_count >= self.cfg.operator.max_restart_failures:
            self.degraded_until = now + self.cfg.operator.restart_after_sec
            self.record_event(
                "operator_shell_degraded",
                {
                    "message": (
                        "Operator shell degraded after "
                        f"{self._failure_count} repeated failures; "
                        f"cooldown {self.cfg.operator.restart_after_sec}s"
                    )
                },
            )
            self._failure_count = 0
            return

        self.record_event(
            "operator_shell_restart",
            {
                "message": (
                    f"Operator shell exited with code {code}; "
                    f"restart attempt {self._failure_count}"
                )
            },
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="operator-shell-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.tick_once()
            self._stop_event.wait(self.poll_interval_sec)

    def stop(self) -> None:
        self._stop_event.set()
        if self.proc is not None:
            proc = self.proc
            self.proc = None
            if getattr(proc, "external", False):
                proc = None
            if proc is not None:
                proc_pid = getattr(proc, "pid", None)
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                if proc_pid is not None:
                    _clear_operator_shell_pid(expected_pid=proc_pid)
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
