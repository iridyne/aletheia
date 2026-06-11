"""Operator shell spawn helpers and supervision."""

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


def _build_operator_shell_env(cfg) -> dict[str, str]:
    return {
        "ALETHEIA_GATEWAY_BASE_URL": f"http://{cfg.app.bind_host}:{cfg.app.dashboard_port}",
        "ALETHEIA_OPERATOR_PERMISSION_MODE": cfg.operator.permission_mode,
        "ALETHEIA_OPERATOR_HEARTBEAT_SEC": str(cfg.operator.heartbeat_interval_sec),
        "ALETHEIA_OPERATOR_MAIN_SESSION_ID": cfg.operator.main_session_id,
    }


def operator_shell_log_path() -> Path:
    return get_data_dir() / "logs" / "operator-shell-errors.log"


def spawn_operator_shell(cfg):
    cmd = build_operator_shell_command(cfg)
    if cmd is None:
        return None

    log_path = operator_shell_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(_build_operator_shell_env(cfg))
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

        self.proc = None
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
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
