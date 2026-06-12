"""Runtime-aware gateway process helpers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir
from aletheia.gateway.pidfile import _no_window_kwargs, _resolve_real_python
from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import get_db


def shadow_port_for(cfg) -> int:
    return int(cfg.app.dashboard_port) + int(cfg.runtime.runtime_continuity_shadow_port_offset)


def runtime_workdir(runtime: dict | None) -> Path:
    artifact_path = str((runtime or {}).get("artifact_path") or "").strip()
    if artifact_path:
        path = Path(artifact_path)
        if path.exists():
            return path
    return _PROJECT_ROOT


def runtime_pythonpath(workdir: Path, env: dict[str, str]) -> str:
    current = env.get("PYTHONPATH", "")
    parts = [str(workdir)]
    if current:
        parts.append(current)
    return os.pathsep.join(parts)


def runtime_process_env(
    *,
    base_env: dict[str, str] | None,
    runtime_id: str,
    runtime_mode: str,
    runtime_state: str,
    port: int,
    workdir: Path,
    env_patch: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    env.update(env_patch or {})
    env["PYTHONPATH"] = runtime_pythonpath(workdir, env)
    env["ALETHEIA_RUNTIME_ID"] = runtime_id
    env["ALETHEIA_RUNTIME_MODE"] = runtime_mode
    env["ALETHEIA_RUNTIME_STATE"] = runtime_state
    env["ALETHEIA_RUNTIME_PORT"] = str(port)
    return env


def current_runtime_record() -> dict | None:
    run_migrations()
    row = get_db().execute(
        "SELECT * FROM runtime_registry WHERE state = 'current' "
        "ORDER BY COALESCE(promoted_at, created_at) DESC, rowid DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def current_gateway_launch_context(cfg) -> dict:
    runtime = current_runtime_record() or {"runtime_id": "runtime-local-current", "state": "current"}
    workdir = runtime_workdir(runtime)
    return {
        "runtime": runtime,
        "runtime_id": str(runtime.get("runtime_id") or "runtime-local-current"),
        "runtime_mode": "current",
        "runtime_state": str(runtime.get("state") or "current"),
        "port": int(cfg.app.dashboard_port),
        "host": cfg.app.bind_host,
        "workdir": workdir,
    }


def wait_http_ready(base_url: str, *, timeout_sec: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    url = f"{base_url.rstrip('/')}/api/health"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=0.75) as response:
                if 200 <= response.status < 500:
                    return True
        except Exception:
            time.sleep(0.25)
    return False


def _runtime_log_path(runtime_id: str, mode: str) -> Path:
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in runtime_id)
    path = get_data_dir() / "logs" / f"runtime-{mode}-{safe_id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def spawn_shadow_gateway(
    cfg,
    runtime: dict,
    *,
    wait_ready: bool = True,
) -> dict:
    runtime_id = str(runtime.get("runtime_id") or "")
    if not runtime_id:
        return {"attempted": True, "started": False, "reason": "runtime_id_missing"}
    port = shadow_port_for(cfg)
    host = cfg.app.bind_host
    base_url = f"http://{host}:{port}"
    workdir = runtime_workdir(runtime)
    real_python, env_patch = _resolve_real_python()
    env = runtime_process_env(
        base_env=os.environ,
        runtime_id=runtime_id,
        runtime_mode="shadow",
        runtime_state=str(runtime.get("state") or "shadowing"),
        port=port,
        workdir=workdir,
        env_patch=env_patch,
    )
    cmd = [
        real_python,
        "-m",
        "uvicorn",
        "aletheia.server.app:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    log_path = _runtime_log_path(runtime_id, "shadow")
    popen_kwargs: dict = {
        "cwd": str(workdir),
        "stdout": subprocess.DEVNULL,
        "env": env,
        **_no_window_kwargs(),
    }
    with log_path.open("a", encoding="utf-8") as log_file:
        popen_kwargs["stderr"] = log_file
        proc = subprocess.Popen(cmd, **popen_kwargs)
    ready = wait_http_ready(base_url) if wait_ready else True
    return {
        "attempted": True,
        "started": proc.poll() is None,
        "ready": ready,
        "pid": proc.pid,
        "host": host,
        "port": port,
        "base_url": base_url,
        "workdir": str(workdir),
        "log_path": str(log_path),
    }
