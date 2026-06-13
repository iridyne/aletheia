"""Backend file watcher — auto-restarts the gateway on .py changes.

Requires: ALETHEIA_DEV_RELOAD=1 (set by dev.ps1)
Uses watchfiles (already installed as uvicorn dependency).

Safety:
  - Preflight syntax/import check before triggering restart
  - Debounce (2s) + cooldown (5s) + storm control (max 5/60s)
  - Refuses to run if live trading is enabled
"""

import os
import sys
import time
import subprocess
import py_compile
import urllib.request
import urllib.error
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ALETHEIA_DIR = PROJECT_ROOT / "aletheia"
GATEWAY_URL = "http://127.0.0.1:8787"

DEBOUNCE_SEC = 2.0
COOLDOWN_SEC = 5.0
MAX_RESTARTS_PER_MINUTE = 5
HEALTH_POLL_TIMEOUT = 15.0


def log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [watch] {msg}", flush=True)


def check_live_guard() -> bool:
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from aletheia.config.loader import load_config
        cfg = load_config()
        if cfg.live.state != "live_disabled":
            log("REFUSED: live trading is not disabled. Exiting.")
            return False
    except Exception as e:
        log(f"WARNING: could not check live state: {e}")
    return True


def preflight(changed_files: list[str]) -> bool:
    py_files = [f for f in changed_files if f.endswith(".py")]
    for path in py_files:
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            log(f"PREFLIGHT FAILED (syntax): {e}")
            return False

    try:
        result = subprocess.run(
            [sys.executable, "-c", "from aletheia.server.app import app"],
            capture_output=True, text=True, timeout=10,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode != 0:
            log(f"PREFLIGHT FAILED (import): {result.stderr.strip()}")
            return False
    except subprocess.TimeoutExpired:
        log("PREFLIGHT FAILED: import check timed out")
        return False
    except Exception as e:
        log(f"PREFLIGHT FAILED: {e}")
        return False

    return True


def trigger_restart(changed_files: list[str]) -> bool:
    import json
    body = json.dumps({
        "reason": "file_change",
        "changed_files": changed_files[:10],
    }).encode()
    req = urllib.request.Request(
        f"{GATEWAY_URL}/api/gateway/restart",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            if not data.get("ok"):
                log(f"Restart declined: {data.get('message', 'unknown')}")
                return False
            log("Restart triggered, waiting for gateway...")
    except (urllib.error.URLError, TimeoutError) as e:
        log(f"Restart request failed: {e}")
        return False
    return True


def wait_healthy() -> bool:
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(1.0)
        try:
            req = urllib.request.Request(f"{GATEWAY_URL}/api/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    log("Gateway healthy.")
                    return True
        except Exception:
            pass
    log("WARNING: Gateway did not become healthy within timeout.")
    return False


def main():
    if os.environ.get("ALETHEIA_DEV_RELOAD") != "1":
        print("ERROR: ALETHEIA_DEV_RELOAD=1 not set. Exiting.", flush=True)
        sys.exit(1)

    if not check_live_guard():
        sys.exit(1)

    from watchfiles import watch, Change

    log(f"Watching {ALETHEIA_DIR} for .py changes...")
    log(f"Debounce={DEBOUNCE_SEC}s, Cooldown={COOLDOWN_SEC}s, MaxRestarts={MAX_RESTARTS_PER_MINUTE}/min")

    restart_times: list[float] = []
    last_restart = 0.0
    storm_disabled = False

    for changes in watch(
        ALETHEIA_DIR,
        debounce=int(DEBOUNCE_SEC * 1000),
        watch_filter=lambda change, path: (
            change != Change.deleted
            and path.endswith(".py")
            and "dashboard_static" not in path
            and "__pycache__" not in path
        ),
    ):
        if storm_disabled:
            continue

        now = time.monotonic()

        # Cooldown check
        if now - last_restart < COOLDOWN_SEC:
            log(f"Cooldown active ({COOLDOWN_SEC}s), skipping.")
            continue

        # Storm control
        restart_times = [t for t in restart_times if now - t <= 60]
        if len(restart_times) >= MAX_RESTARTS_PER_MINUTE:
            log(f"STORM CONTROL: {MAX_RESTARTS_PER_MINUTE} restarts in 60s. Auto-reload disabled.")
            storm_disabled = True
            continue

        changed = [str(path) for _, path in changes]
        short = [os.path.relpath(p, PROJECT_ROOT) for p in changed[:5]]
        log(f"Changed: {', '.join(short)}")

        if not preflight(changed):
            log("Skipping restart due to preflight failure.")
            continue

        if trigger_restart(changed):
            last_restart = time.monotonic()
            restart_times.append(last_restart)
            wait_healthy()


if __name__ == "__main__":
    main()
