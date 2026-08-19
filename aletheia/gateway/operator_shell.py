"""Operator shell spawn helpers and supervision."""

from contextlib import contextmanager
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
import urllib.request
from datetime import datetime, timezone

from aletheia.config.loader import get_data_dir
from aletheia.gateway.network import local_frontdoor_host
from aletheia.operator_service_identity import (
    OPERATOR_SERVICE_ENV,
    OPERATOR_SERVICE_HEADER,
    current_operator_service_token,
    ensure_operator_service_token,
    issue_session_token,
    revoke_session_token,
    rotate_operator_service_token,
)

CREATE_NO_WINDOW = 0x08000000


def operator_shell_root() -> Path:
    configured = os.environ.get("ALETHEIA_OPERATOR_SHELL_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "operator-shell"


def resolve_node_executable() -> str:
    """Resolve Node without relying on an interactive shell's PATH.

    macOS launchd does not inherit fnm/nvm shell initialization. Prefer an
    explicit override or PATH, then check stable locations used by common
    user-level Node managers and package managers.
    """
    override = os.environ.get("ALETHEIA_NODE_BINARY", "").strip()
    if override:
        return override

    discovered = shutil.which("node")
    if discovered:
        return discovered

    home = Path.home()
    candidates = (
        home / ".local" / "share" / "fnm" / "aliases" / "default" / "bin" / "node",
        home / ".volta" / "bin" / "node",
        home / ".asdf" / "shims" / "node",
        home / ".local" / "share" / "mise" / "shims" / "node",
        Path("/opt/homebrew/bin/node"),
        Path("/usr/local/bin/node"),
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "node"


def build_operator_shell_command(cfg) -> list[str] | None:
    root = operator_shell_root()
    preflight = root / "node_modules" / "tsx" / "dist" / "preflight.cjs"
    loader = root / "node_modules" / "tsx" / "dist" / "loader.mjs"
    entry = root / "src" / "index.ts"
    if not preflight.exists() or not loader.exists() or not entry.exists():
        return None
    # Launch the actual tsx loader process directly instead of invoking the tsx
    # CLI wrapper. On Windows the wrapper can transiently fork/exit, which makes
    # the Python supervisor track the short-lived parent instead of the durable
    # operator process.
    return [resolve_node_executable(), "--require", str(preflight), "--import", loader.as_uri(), str(entry)]


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
    service_token: str | None = None,
) -> dict[str, str]:
    effective_runtime_id = runtime_id or _current_runtime_id()
    env = {
        "ALETHEIA_GATEWAY_BASE_URL": f"http://{local_frontdoor_host()}:{cfg.app.dashboard_port}",
        "ALETHEIA_OPERATOR_PERMISSION_MODE": cfg.operator.permission_mode,
        "ALETHEIA_OPERATOR_HEARTBEAT_SEC": str(cfg.operator.heartbeat_interval_sec),
        "ALETHEIA_OPERATOR_MAIN_SESSION_ID": cfg.operator.main_session_id,
        "ALETHEIA_RUNTIME_ID": effective_runtime_id,
        "ALETHEIA_RUNTIME_MODE": runtime_mode,
        "ALETHEIA_RUNTIME_STATE": runtime_state,
    }
    if service_token:
        env[OPERATOR_SERVICE_ENV] = service_token
    return env


def _operator_service_auth_required(cfg) -> bool:
    tunnel_enabled = bool(getattr(getattr(cfg, "tunnel", None), "enabled", False))
    remote_control = getattr(cfg, "remote_control", None)
    return tunnel_enabled or bool(
        getattr(remote_control, "enabled", False)
        and getattr(remote_control, "require_token", False)
    )


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
    for value in cmd[1:]:
        if value.startswith("-"):
            continue
        name = Path(value).name
        if name in {"cli.mjs", "preflight.cjs", "loader.mjs", "index.ts"}:
            markers.append(name)
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


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in {"", "0.0.0.0", "::", "*"} else host


def _gateway_api_ready(cfg) -> bool:
    try:
        url = f"http://{_connect_host(local_frontdoor_host())}:{cfg.app.dashboard_port}/api/ready"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=1.0) as response:
            return response.status == 200
    except Exception:
        return False


def _parse_utc_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _latest_operator_heartbeat_age_sec() -> float | None:
    try:
        from aletheia.agent_shell.operator_store import get_latest_heartbeat

        heartbeat = get_latest_heartbeat("operator")
    except Exception:
        return None
    if not heartbeat:
        return None
    ts = _parse_utc_ts(heartbeat.get("last_seen_at") or heartbeat.get("created_at"))
    if ts is None:
        return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


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
        # Live shells rotate the durable token on each respawn. Shadow / one-shot
        # shells get an independent ephemeral token so live-shell rotation cannot
        # invalidate a concurrent shadow run.
        service_token = issue_session_token() if once else rotate_operator_service_token()
        env = os.environ.copy()
        env.update(
            _build_operator_shell_env(
                cfg,
                runtime_id=runtime_id,
                runtime_mode=runtime_mode,
                runtime_state=runtime_state,
                service_token=service_token,
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
            proc = subprocess.Popen(cmd, **popen_kwargs)
        if once:
            _watch_and_revoke_session_token(proc, service_token)
        return proc

    if not once:
        with _exclusive_pidfile_lock():
            existing_pid = _read_operator_shell_pid()
            if existing_pid is not None:
                if _pid_exists(existing_pid) and _operator_shell_process_matches(existing_pid, cmd):
                    if not _operator_service_auth_required(cfg):
                        _cleanup_unmanaged_operator_shells(keep_pid=existing_pid)
                        return ExistingOperatorShellProcess(existing_pid, cmd)
                    # Under owner-auth, an orphaned/adopted Node process may have
                    # been started before service identity existed or with an old
                    # rotated token. Restart it so current credentials are proven.
                    try:
                        from aletheia.gateway.pidfile import kill_pid_tree

                        kill_pid_tree(existing_pid)
                    except Exception:
                        pass
                    _clear_operator_shell_pid(expected_pid=existing_pid)
                else:
                    _clear_operator_shell_pid(expected_pid=existing_pid)

            _cleanup_unmanaged_operator_shells()
            proc = _spawn_new()
            _write_operator_shell_pid(proc.pid)
            return proc

    return _spawn_new()


def _watch_and_revoke_session_token(proc, token: str) -> None:
    """Revoke an ephemeral session token when its once-process exits."""
    def _watcher():
        try:
            proc.wait()
        except Exception:
            pass
        finally:
            revoke_session_token(token)
    threading.Thread(target=_watcher, daemon=True, name="operator-session-token-watcher").start()


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


def _post_operator_heartbeat(cfg, payload: dict) -> bool:
    try:
        url = f"http://{_connect_host(local_frontdoor_host())}:{cfg.app.dashboard_port}/api/operator/internal/heartbeat"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        service_token = current_operator_service_token()
        if service_token:
            headers[OPERATOR_SERVICE_HEADER] = service_token
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers=headers,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2.0) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def _record_operator_shell_unavailable(
    cfg,
    message: str,
    *,
    status: str = "offline",
    preserve_latest: bool = False,
) -> bool:
    """Persist an honest, fresh operator status while the supervisor owns recovery."""
    latest: dict | None = None
    if preserve_latest:
        try:
            from aletheia.agent_shell.operator_store import get_latest_heartbeat

            latest = get_latest_heartbeat("operator")
        except Exception:
            latest = None

    payload = {
        "agent_id": "operator",
        "status": status,
        "mode": "Degraded",
        "permission_mode": (latest or {}).get("permission_mode") or cfg.operator.permission_mode,
        "is_busy": bool((latest or {}).get("is_busy")) if preserve_latest else False,
        "current_task_id": (latest or {}).get("current_task_id") if preserve_latest else None,
        "current_intent": message,
        "queue_size": int((latest or {}).get("queue_size") or 0) if preserve_latest else 0,
        "last_error": message,
        "model_provider": (latest or {}).get("model_provider") if preserve_latest else None,
        "session_id": (latest or {}).get("session_id") or cfg.operator.main_session_id,
        "runtime_id": (latest or {}).get("runtime_id"),
        "runtime_mode": (latest or {}).get("runtime_mode") or "current",
        "runtime_state": (latest or {}).get("runtime_state") or "current",
        "active_branch_id": (latest or {}).get("active_branch_id") if preserve_latest else None,
        "active_branch_kind": (latest or {}).get("active_branch_kind") if preserve_latest else None,
        "active_branch_title": (latest or {}).get("active_branch_title") if preserve_latest else None,
        "last_success_at": (latest or {}).get("last_success_at") if preserve_latest else None,
    }
    if _post_operator_heartbeat(cfg, payload):
        return True

    try:
        from aletheia.agent_shell.operator_store import record_heartbeat

        record_heartbeat(**payload)
        return True
    except Exception:
        return False


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
        self._proc_started_at: float | None = None
        self.degraded_until = 0.0
        self._failure_count = 0
        self._last_unavailable_heartbeat_at: float | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def _record_unavailable(
        self,
        message: str,
        *,
        now: float | None = None,
        force: bool = False,
        status: str = "offline",
        preserve_latest: bool = False,
    ) -> bool:
        effective_now = self.now_fn() if now is None else now
        interval = max(float(self.cfg.operator.heartbeat_interval_sec), 1.0)
        if (
            not force
            and self._last_unavailable_heartbeat_at is not None
            and effective_now - self._last_unavailable_heartbeat_at < interval
        ):
            return False
        recorded = _record_operator_shell_unavailable(
            self.cfg,
            message,
            status=status,
            preserve_latest=preserve_latest,
        )
        if recorded:
            self._last_unavailable_heartbeat_at = effective_now
        return recorded

    def tick_once(self) -> None:
        if not self.cfg.operator.enabled:
            return

        now = self.now_fn()
        if self.proc is None:
            if now < self.degraded_until:
                self._record_unavailable(
                    "Operator shell is in restart cooldown after repeated failures; supervisor will retry.",
                    now=now,
                )
                return
            if not _gateway_api_ready(self.cfg):
                return
            spawn_error: str | None = None
            try:
                proc = self.spawn_fn(self.cfg)
            except Exception as exc:
                proc = None
                spawn_error = f"Operator shell start failed: {exc}"
            if proc is None:
                self._failure_count += 1
                message = spawn_error or "Operator shell command is unavailable; supervisor cannot start it."
                self._record_unavailable(message, now=now)
                if self._failure_count >= self.cfg.operator.max_restart_failures:
                    self.degraded_until = now + self.cfg.operator.restart_after_sec
                    detail = f"; last error: {spawn_error}" if spawn_error else ""
                    degraded_message = (
                        "Operator shell command remained unavailable after "
                        f"{self._failure_count} start attempt(s); "
                        f"cooldown {self.cfg.operator.restart_after_sec}s"
                        f"{detail}"
                    )
                    self._record_unavailable(degraded_message, now=now, force=True)
                    self.record_event(
                        "operator_shell_degraded",
                        {"message": degraded_message},
                    )
                    self._failure_count = 0
                return
            self.proc = proc
            self._proc_started_at = now
            self._last_unavailable_heartbeat_at = None
            return

        code = self.proc.poll()
        if code is None:
            if self._operator_heartbeat_stale(now):
                proc_pid = getattr(self.proc, "pid", None)
                self._restart_stale_process(proc_pid)
            return

        proc_pid = getattr(self.proc, "pid", None)
        self.proc = None
        self._proc_started_at = None
        if proc_pid is not None:
            _clear_operator_shell_pid(expected_pid=proc_pid)
        if code == 0:
            self._failure_count = 0
            self._record_unavailable(
                "Operator shell exited cleanly; supervisor will restart it.",
                now=now,
            )
            return

        self._failure_count += 1
        if self._failure_count >= self.cfg.operator.max_restart_failures:
            self.degraded_until = now + self.cfg.operator.restart_after_sec
            degraded_message = (
                "Operator shell degraded after "
                f"{self._failure_count} repeated failures; "
                f"cooldown {self.cfg.operator.restart_after_sec}s"
            )
            self._record_unavailable(degraded_message, now=now, force=True)
            self.record_event(
                "operator_shell_degraded",
                {"message": degraded_message},
            )
            self._failure_count = 0
            return

        restart_message = (
            f"Operator shell exited with code {code}; "
            f"restart attempt {self._failure_count}"
        )
        self._record_unavailable(restart_message, now=now)
        self.record_event(
            "operator_shell_restart",
            {"message": restart_message},
        )

    def _operator_heartbeat_stale(self, now: float) -> bool:
        started_at = self._proc_started_at
        stale_after = float(self.cfg.operator.heartbeat_stale_after_sec)
        # Give a newly spawned/adopted process one stale window to boot, recover
        # state, and emit its first heartbeat. This avoids restart loops while
        # the backend is still settling immediately after gateway restart.
        if started_at is not None and now - started_at <= stale_after:
            return False
        if not _gateway_api_ready(self.cfg):
            return False
        age = _latest_operator_heartbeat_age_sec()
        if age is None:
            return started_at is not None and now - started_at > stale_after
        if age > stale_after:
            self._record_unavailable(
                "Operator shell process is alive but its internal heartbeat is delayed; supervisor is keeping status fresh.",
                status="online",
                preserve_latest=True,
            )
            return False
        return False

    def _restart_stale_process(self, proc_pid: int | None) -> None:
        if proc_pid is not None:
            try:
                from aletheia.gateway.pidfile import kill_pid_tree

                kill_pid_tree(proc_pid)
            except Exception:
                pass
            _clear_operator_shell_pid(expected_pid=proc_pid)
        self.proc = None
        self._proc_started_at = None
        self._failure_count += 1
        if self._failure_count >= self.cfg.operator.max_restart_failures:
            self.degraded_until = self.now_fn() + self.cfg.operator.restart_after_sec
            degraded_message = (
                "Operator shell heartbeat stayed stale after "
                f"{self._failure_count} restart attempt(s); "
                f"cooldown {self.cfg.operator.restart_after_sec}s"
            )
            self._record_unavailable(degraded_message, force=True)
            self.record_event(
                "operator_shell_degraded",
                {"message": degraded_message},
            )
            self._failure_count = 0
            return
        restart_message = (
            "Operator shell heartbeat is stale while the gateway is ready; "
            f"restart attempt {self._failure_count}"
        )
        self._record_unavailable(restart_message)
        self.record_event(
            "operator_shell_restart",
            {"message": restart_message},
        )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="operator-shell-monitor", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick_once()
            except Exception as exc:
                message = f"Operator shell monitor tick failed: {exc}"
                if self._record_unavailable(message):
                    self.record_event("operator_shell_degraded", {"message": message})
            self._stop_event.wait(self.poll_interval_sec)

    def stop(self) -> None:
        self._stop_event.set()
        if self.proc is not None:
            proc = self.proc
            self.proc = None
            self._proc_started_at = None
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
