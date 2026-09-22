"""Provisioned, paused Core lifecycle survives absent optional implementations.

This does not qualify active scheduling/trading or optional-free installation.
Preparation uses real production migrations before a fresh absent-package process.
"""

import os
from pathlib import Path
import subprocess
import sys


def test_provisioned_paused_lifecycle_without_optional_packages(tmp_path):
    environment = {**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path),
                   "ALETHEIA_RUNTIME_MODE": "current"}
    environment.pop("PYTEST_CURRENT_TEST", None)
    common = """
import asyncio
import importlib.abc
import socket
import sys
from aletheia.config import loader, secrets

loader._load_dotenv = lambda: None
credential_attempts = []
def forbidden_credentials(*args, **kwargs):
    credential_attempts.append(True)
    raise AssertionError("raw credentials are forbidden")
def offline(*args, **kwargs):
    raise OSError("isolated lifecycle test is offline")
secrets.get_secret = forbidden_credentials
# Public pricing/connectivity probes may run; neither DNS nor egress is allowed.
socket.getaddrinfo = offline
socket.socket.connect = offline
"""
    prepare = common + """
cfg = loader.bootstrap()
cfg.runtime.scheduler_enabled = False
cfg.runtime.paused = True
cfg.pi_daemon.enabled = False
cfg.operator.loop_enabled = False
cfg.network.proxy_host = ""
cfg.network.recompose_proxy_url()
loader.save_config(cfg)
from aletheia.storage.migrations import apply_migrations, assert_migrations_current
apply_migrations()
assert_migrations_current()
assert not credential_attempts
"""
    run = common + """
missing = (
    "extensions", "aletheia.learning", "aletheia.knowledge",
    "aletheia.counterfactual", "aletheia.observation_lab",
)
attempted = set()
class MissingOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in missing):
            attempted.add(fullname)
            raise ModuleNotFoundError("optional implementation absent", name=fullname)
sys.meta_path.insert(0, MissingOptional())

import httpx
import aletheia.server.app as server
from aletheia.storage.sqlite import get_db
from aletheia.uta.manager import build_uta_registry

async def check():
    async with server.lifespan(server.app):
        assert build_uta_registry().configured_identities() == ()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url="http://test"
        ) as client:
            response = await client.get("/_infra/livez")
            assert response.status_code == 200, response.text
        assert get_db().execute("SELECT COUNT(*) FROM runtime_sessions").fetchone()[0] == 1
    assert not server._lifespan_tasks
    session = get_db().execute("SELECT ended_at, shutdown_clean FROM runtime_sessions").fetchone()
    assert session[0] and session[1] == 1
    for table in ("trade_proposals", "risk_decisions", "orders", "fills", "positions"):
        assert get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    assert loader.load_config(persist_migrations=False).runtime.paused is True

asyncio.run(check())
assert {"extensions", "aletheia.learning", "aletheia.knowledge"} <= attempted
assert not any(name == root or name.startswith(root + ".") for name in sys.modules for root in missing)
assert not credential_attempts
"""
    for phase, script in (("prepare", prepare), ("runtime", run)):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr}"
