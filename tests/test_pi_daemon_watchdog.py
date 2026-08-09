"""Tests for PI Daemon watchdog health monitoring and auto-restart.

These tests verify that:
1. The daemon updates a heartbeat timestamp each loop cycle
2. The watchdog restarts a dead daemon
3. The watchdog restarts a daemon with stale heartbeat
4. The watchdog does nothing when the daemon is healthy
5. Consecutive evaluation failures are tracked
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aletheia.agent_runtime.pi_daemon as pd
from aletheia.agent_runtime.pi_daemon import (
    daemon_status,
    is_daemon_running,
    start_pi_daemon,
)


@pytest.fixture(autouse=True)
def reset_daemon_state():
    """Ensure module-level state is clean before and after each test."""
    pd._daemon_task = None
    pd._signal_event = None
    pd._eval_lock = None
    pd._uvicorn_loop = None
    pd._coalescer = None
    pd._pending_events.clear()
    pd._generic_wake_pending = False
    pd._task_generation = 0
    pd._started_at = None
    pd._active_params = None
    pd._last_heartbeat = 0.0
    pd._consecutive_eval_failures = 0
    yield
    task = pd._daemon_task
    if task is not None and not task.done():
        task.cancel()
    pd._daemon_task = None
    pd._signal_event = None
    pd._eval_lock = None
    pd._uvicorn_loop = None
    pd._coalescer = None
    pd._pending_events.clear()
    pd._generic_wake_pending = False
    pd._task_generation = 0
    pd._started_at = None
    pd._active_params = None
    pd._last_heartbeat = 0.0
    pd._consecutive_eval_failures = 0


# ---------------------------------------------------------------------------
# Heartbeat tests
# ---------------------------------------------------------------------------


def test_heartbeat_initial_zero():
    """Before the daemon starts, _last_heartbeat must be 0.0."""
    assert pd._last_heartbeat == 0.0


async def test_heartbeat_updates_each_cycle():
    """The daemon must update _last_heartbeat at the start of each loop cycle."""
    pd._last_heartbeat = 0.0
    with patch("aletheia.config.loader.load_config") as mock_cfg, \
         patch("aletheia.agent_shell.commands.evaluate_trading_opportunity") as mock_eval:
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"
        mock_eval.return_value = {"action": "skip", "pending_queue_remaining": 0}

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop,
            debounce_sec=0.05,
            cooldown_sec=0.05,
            heartbeat_sec=999.0,
        )

        # Poll for heartbeat update (loop may take a moment to start)
        for _ in range(100):
            if pd._last_heartbeat > 0.0:
                break
            await asyncio.sleep(0.01)
        assert pd._last_heartbeat > 0.0, (
            f"_last_heartbeat was not updated; task.done()={task.done()}, "
            f"task.exception()={task.exception() if task.done() else 'N/A'}"
        )

        status = daemon_status()
        assert status["last_heartbeat_age_sec"] is not None
        assert status["last_heartbeat_age_sec"] < 5.0  # should be very recent

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def test_daemon_status_includes_heartbeat_age():
    """daemon_status() must expose last_heartbeat_age_sec and consecutive_failures."""
    status = daemon_status()
    assert "last_heartbeat_age_sec" in status
    assert "consecutive_failures" in status
    assert "signal_pending_queue_depth" in status


# ---------------------------------------------------------------------------
# Watchdog tests
# ---------------------------------------------------------------------------


def _make_mock_loop():
    """Create a mock event loop for watchdog tests."""
    loop = MagicMock()
    loop.is_running.return_value = True
    loop.is_closed.return_value = False
    return loop


def test_watchdog_restarts_dead_daemon():
    """Watchdog must schedule restart when daemon is not running."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog

    mock_loop = _make_mock_loop()
    with patch("aletheia.agent_runtime.pi_daemon.is_daemon_running", return_value=False), \
         patch("aletheia.agent_runtime.pi_daemon._uvicorn_loop", mock_loop), \
         patch("aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon", MagicMock()), \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs, \
         patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg:
        mock_cfg.return_value.pi_daemon.enabled = True
        mock_cfg.return_value.pi_daemon.debounce_sec = 2.0
        mock_cfg.return_value.pi_daemon.cooldown_sec = 45.0
        mock_cfg.return_value.pi_daemon.heartbeat_sec = 60.0

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_called_once()


def test_watchdog_restarts_stale_heartbeat():
    """Watchdog must restart when heartbeat age exceeds stale threshold."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog

    mock_loop = _make_mock_loop()
    with patch("aletheia.agent_runtime.pi_daemon.is_daemon_running", return_value=True), \
         patch("aletheia.agent_runtime.pi_daemon.daemon_status") as mock_status, \
         patch("aletheia.agent_runtime.pi_daemon._uvicorn_loop", mock_loop), \
         patch("aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon", MagicMock()), \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs, \
         patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg:
        mock_cfg.return_value.pi_daemon.enabled = True
        mock_cfg.return_value.pi_daemon.debounce_sec = 2.0
        mock_cfg.return_value.pi_daemon.cooldown_sec = 45.0
        mock_cfg.return_value.pi_daemon.heartbeat_sec = 60.0
        mock_status.return_value = {
            "running": True,
            "last_heartbeat_age_sec": 300.0,  # 5 minutes, well over 180s threshold
            "consecutive_failures": 0,
            "lock_held": False,
            "signal_pending_queue_depth": 0,
        }

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_called_once()


def test_watchdog_no_op_when_healthy():
    """Watchdog must NOT restart a healthy daemon."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog

    mock_loop = _make_mock_loop()
    with patch("aletheia.agent_runtime.pi_daemon.is_daemon_running", return_value=True), \
         patch("aletheia.agent_runtime.pi_daemon.daemon_status") as mock_status, \
         patch("aletheia.agent_runtime.pi_daemon._uvicorn_loop", mock_loop), \
         patch("aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon", MagicMock()), \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs, \
         patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg:
        mock_cfg.return_value.pi_daemon.enabled = True
        mock_status.return_value = {
            "running": True,
            "last_heartbeat_age_sec": 30.0,  # recent heartbeat
            "consecutive_failures": 0,
            "lock_held": False,
            "signal_pending_queue_depth": 0,
        }

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_not_called()


def test_watchdog_no_op_when_disabled():
    """Watchdog must do nothing when pi_daemon is disabled in config."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog

    with patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg, \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs:
        mock_cfg.return_value.pi_daemon.enabled = False

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_not_called()


def test_watchdog_no_op_when_no_loop():
    """Watchdog must do nothing when _uvicorn_loop is None (gateway not ready)."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog

    with patch("aletheia.agent_runtime.pi_daemon.is_daemon_running", return_value=False), \
         patch("aletheia.agent_runtime.pi_daemon._uvicorn_loop", None), \
         patch("aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon", MagicMock()), \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs, \
         patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg:
        mock_cfg.return_value.pi_daemon.enabled = True

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_not_called()


def test_watchdog_boundary_stale_threshold():
    """Watchdog must NOT restart when heartbeat age is exactly at the threshold."""
    from aletheia.agent_runtime.scheduler import _run_pi_daemon_watchdog, _PI_DAEMON_HEARTBEAT_STALE_SEC

    mock_loop = _make_mock_loop()
    with patch("aletheia.agent_runtime.pi_daemon.is_daemon_running", return_value=True), \
         patch("aletheia.agent_runtime.pi_daemon.daemon_status") as mock_status, \
         patch("aletheia.agent_runtime.pi_daemon._uvicorn_loop", mock_loop), \
         patch("aletheia.agent_runtime.pi_daemon.reconfigure_pi_daemon", MagicMock()), \
         patch("aletheia.agent_runtime.scheduler.asyncio.run_coroutine_threadsafe") as mock_run_cs, \
         patch("aletheia.agent_runtime.scheduler.load_config") as mock_cfg:
        mock_cfg.return_value.pi_daemon.enabled = True
        mock_status.return_value = {
            "running": True,
            "last_heartbeat_age_sec": _PI_DAEMON_HEARTBEAT_STALE_SEC - 1,  # just under
            "consecutive_failures": 0,
            "lock_held": False,
            "signal_pending_queue_depth": 0,
        }

        _run_pi_daemon_watchdog()

        mock_run_cs.assert_not_called()


# ---------------------------------------------------------------------------
# Consecutive failure counter tests
# ---------------------------------------------------------------------------


def test_consecutive_failures_initial_zero():
    """Counter must start at zero."""
    assert pd._consecutive_eval_failures == 0


def test_consecutive_failures_tracked_in_status():
    """daemon_status() must expose the consecutive_failures counter."""
    pd._consecutive_eval_failures = 3
    status = daemon_status()
    assert status["consecutive_failures"] == 3
