"""#1456: scheduler shutdown drains both sync lanes before a clean exit.

Stop admission, cancel queued work, wait a bounded time for the running call,
and report the result so the lifespan records ``shutdown_clean`` truthfully.
"""

import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from aletheia.agent_runtime import scheduler
from tests.test_lifespan_startup_failure_cleanup import _COMMON, _PREPARE

LANES = ("_sync_timeout_worker", "_universe_audit_timeout_worker")


@pytest.fixture
def fresh_lanes(monkeypatch):
    for name in LANES:
        monkeypatch.setattr(scheduler, name, scheduler._PersistentSyncTimeoutWorker(name))
    monkeypatch.setattr(scheduler, "_SYNC_LANE_DRAIN_SEC", 0.2)


@pytest.mark.parametrize("lane_name", LANES)
def test_stop_cancels_queued_work_and_reports_running_call(fresh_lanes, lane_name):
    lane = getattr(scheduler, lane_name)
    started, release = threading.Event(), threading.Event()
    ran_after_stop: list[str] = []

    running = lane.submit(lambda: (started.set(), release.wait(5)))
    queued = lane.submit(lambda: ran_after_stop.append("queued write"))
    assert started.wait(1)

    assert scheduler.stop_scheduler() is False  # a call is still running
    assert queued.cancelled()
    with pytest.raises(RuntimeError, match="closed"):
        lane.submit(lambda: ran_after_stop.append("late submit"))
    # The old lane still runs: a new lifespan must not get a second owner.
    with pytest.raises(RuntimeError, match="shutdown incomplete"):
        scheduler._ensure_sync_lanes_ready()

    release.set()
    running.result(timeout=1)
    assert lane.join(1) is True
    assert ran_after_stop == []
    assert lane.pending == 0

    scheduler._ensure_sync_lanes_ready()
    fresh = getattr(scheduler, lane_name)
    assert fresh is not lane
    assert fresh.submit(lambda: "ok").result(timeout=1) == "ok"
    fresh.stop()
    assert fresh.join(1) is True


def test_stop_reports_drained_when_lanes_are_idle(fresh_lanes):
    assert scheduler._sync_timeout_worker.submit(lambda: "ok").result(timeout=1) == "ok"
    assert scheduler.stop_scheduler() is True
    assert not scheduler._sync_timeout_worker.thread_alive


_LIFESPAN_RUN = _COMMON + """
import asyncio
import threading
import aletheia.server.app as server
from aletheia.agent_runtime import scheduler
from aletheia.storage.sqlite import get_db

scheduler._SYNC_LANE_DRAIN_SEC = 0.2
started, release = threading.Event(), threading.Event()

async def check():
    async with server.lifespan(server.app):
        if BLOCK:
            scheduler._sync_timeout_worker.submit(lambda: (started.set(), release.wait(10)))
            assert started.wait(2)

asyncio.run(check())
row = get_db().execute("SELECT ended_at, shutdown_clean FROM runtime_sessions").fetchone()
release.set()
print("SESSION", bool(row[0]), row[1])
"""


@pytest.mark.parametrize(("block", "clean"), [(True, 0), (False, 1)])
def test_lifespan_marks_clean_only_when_sync_lanes_drained(tmp_path, block, clean):
    environment = {**os.environ, "ALETHEIA_DATA_DIR": str(tmp_path), "ALETHEIA_RUNTIME_MODE": "current"}
    environment.pop("PYTEST_CURRENT_TEST", None)
    for phase, script in (("prepare", _PREPARE), ("runtime", f"BLOCK = {block!r}\n" + _LIFESPAN_RUN)):
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr[-3000:]}"
    assert f"SESSION True {clean}" in result.stdout
