"""#1450: a startup failure before ``yield`` runs the one lifespan cleanup path.

Every owner already started is stopped once, the owned PID file is removed, and
the Runtime Session is recorded as an unclean end. Runs the real lifespan in a
fresh process against a migrated temp data dir (same harness as
test_optional_runtime_lifecycle).
"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

_COMMON = """
import socket
from aletheia.config import loader, secrets

loader._load_dotenv = lambda: None
def forbidden_credentials(*args, **kwargs):
    raise AssertionError("raw credentials are forbidden")
def offline(*args, **kwargs):
    raise OSError("isolated lifecycle test is offline")
secrets.get_secret = forbidden_credentials
socket.getaddrinfo = offline
socket.socket.connect = offline
"""

_PREPARE = _COMMON + """
cfg = loader.bootstrap()
cfg.runtime.scheduler_enabled = False
cfg.runtime.paused = True
cfg.pi_daemon.enabled = False
cfg.network.proxy_host = ""
cfg.network.recompose_proxy_url()
loader.save_config(cfg)
from aletheia.storage.migrations import apply_migrations
apply_migrations()
"""

_RUN = _COMMON + """
import asyncio
import aletheia.server.app as server
from aletheia.agent_runtime import scheduler
from aletheia.agent_runtime.job_queue import get_agent_job_manager
from aletheia.gateway.pidfile import pid_path
from aletheia.storage.sqlite import get_db

calls = {"stop_scheduler": 0, "job_manager_stop": 0}
real_stop_scheduler = scheduler.stop_scheduler
def counted_stop_scheduler():
    calls["stop_scheduler"] += 1
    real_stop_scheduler()
scheduler.stop_scheduler = counted_stop_scheduler
manager = get_agent_job_manager()
real_manager_stop = manager.stop
def counted_manager_stop(*args, **kwargs):
    calls["job_manager_stop"] += 1
    real_manager_stop(*args, **kwargs)
manager.stop = counted_manager_stop

class InjectedStartupFailure(Exception):
    pass
def boom(*args, **kwargs):
    raise InjectedStartupFailure(FAIL_AT)
if FAIL_AT == "mount_static_dashboard":
    server._mount_static_dashboard = boom
else:
    import aletheia.storage.migrations as migrations
    migrations.assert_migrations_current = boom

async def check():
    try:
        async with server.lifespan(server.app):
            raise AssertionError("startup must not reach yield")
    except InjectedStartupFailure:
        pass
    else:
        raise AssertionError("the original startup error must propagate")

asyncio.run(check())
assert not pid_path().exists(), "owned PID file left behind"
assert not server._lifespan_tasks
if FAIL_AT == "mount_static_dashboard":
    assert calls == {"stop_scheduler": 1, "job_manager_stop": 1}, calls
    rows = get_db().execute("SELECT ended_at, shutdown_clean FROM runtime_sessions").fetchall()
    assert len(rows) == 1 and rows[0][0] and rows[0][1] == 0, [tuple(r) for r in rows]
print("CLEANED")
"""


@pytest.mark.parametrize("fail_at", ["mount_static_dashboard", "assert_migrations_current"])
def test_startup_failure_before_yield_stops_started_owners_once(tmp_path, fail_at):
    environment = {**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "current"}
    environment.pop("PYTEST_CURRENT_TEST", None)
    for phase, script in (("prepare", _PREPARE), ("runtime", f"FAIL_AT = {fail_at!r}\n" + _RUN)):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr[-3000:]}"
    assert "CLEANED" in result.stdout
