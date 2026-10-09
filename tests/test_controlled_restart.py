"""#1454: a controlled restart runs the graceful lifespan shutdown, then exits 42.

Drives a real uvicorn process through the real restart route while a browser-like
client holds the real operator event stream open (uvicorn waits for every open
response before running the lifespan shutdown).
"""

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx

from tests.test_lifespan_startup_failure_cleanup import _COMMON, _PREPARE

_PROBE_APP = '''
from contextlib import asynccontextmanager
from fastapi import FastAPI
from aletheia.server import controlled_restart
import aletheia.server.routes.operator as operator
from aletheia.server.routes.gateway import router as gateway_router

operator.list_events = lambda limit=50, **_: []

@asynccontextmanager
async def lifespan(app):
    yield
    print("LIFESPAN_CLEANUP_RAN", flush=True)
    controlled_restart.exit_if_restart_requested()

app = FastAPI(lifespan=lifespan)
app.state._owns_pid = False  # a supervisor owns the pid file
app.include_router(gateway_router)
app.include_router(operator.router)
'''


def test_restart_route_shuts_down_gracefully_and_exits_42(tmp_path, unused_tcp_port):
    (tmp_path / "probe_app.py").write_text(_PROBE_APP)
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "ALETHEIA_DATA_DIR": str(tmp_path / "data"),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(root / "core"), str(root))),
    }
    env.pop("ALETHEIA_DEV_RELOAD", None)
    env.pop("PYTEST_CURRENT_TEST", None)
    base = f"http://127.0.0.1:{unused_tcp_port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "probe_app:app", "--port", str(unused_tcp_port)],
        cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                httpx.get(f"{base}/openapi.json", timeout=1)
                break
            except httpx.TransportError:
                assert time.monotonic() < deadline, "probe server never started"
                time.sleep(0.2)

        stream_ended = threading.Event()

        def hold_stream():
            try:
                with httpx.stream("GET", f"{base}/api/operator/events/stream", timeout=30) as response:
                    assert response.status_code == 200
                    for _ in response.iter_raw():
                        pass
            except httpx.HTTPError:
                pass
            stream_ended.set()

        threading.Thread(target=hold_stream, daemon=True).start()
        time.sleep(1.0)

        response = httpx.post(f"{base}/api/gateway/restart", json={"reason": "manual"}, timeout=5)
        assert response.status_code == 200 and response.json()["mode"] == "cold"

        # The supervisor's port-lost watchdog would kill a worker after 15s.
        code = proc.wait(timeout=15)
        output = proc.stdout.read() if proc.stdout else ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    assert stream_ended.wait(1), "the open event stream held the shutdown"
    assert "LIFESPAN_CLEANUP_RAN" in output, output
    assert code == 42, output


_LIFESPAN_RUN = _COMMON + """
import asyncio
import aletheia.server.app as server
from aletheia.server import controlled_restart
from aletheia.storage.sqlite import get_db

exits = []
def record_exit(code):
    row = get_db().execute("SELECT ended_at, shutdown_clean FROM runtime_sessions").fetchone()
    exits.append((code, bool(row[0]), row[1]))
controlled_restart._os_exit = record_exit

async def check():
    async with server.lifespan(server.app):
        controlled_restart._requested.set()  # what the restart route / self-heal set

asyncio.run(check())
print("EXITS", exits)
"""


def test_lifespan_exits_42_only_after_its_full_cleanup(tmp_path):
    environment = {**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "current"}
    environment.pop("PYTEST_CURRENT_TEST", None)
    for phase, script in (("prepare", _PREPARE), ("runtime", _LIFESPAN_RUN)):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr[-3000:]}"
    # Exactly one exit 42, after the Runtime Session was ended clean.
    assert "EXITS [(42, True, 1)]" in result.stdout, result.stdout
