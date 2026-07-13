"""Tests for the System Operator event-driven daemon (pi_daemon.py)."""

import asyncio
from unittest.mock import patch

import pytest

import aletheia.agent_runtime.pi_daemon as pd
from aletheia.agent_runtime.pi_daemon import (
    daemon_status,
    is_daemon_running,
    notify_pi_daemon,
    start_pi_daemon,
    stop_pi_daemon,
)


@pytest.fixture(autouse=True)
def reset_daemon_state():
    """Ensure module-level state is clean before and after each test."""
    pd._daemon_task = None
    pd._signal_event = None
    pd._eval_lock = None
    pd._uvicorn_loop = None
    yield
    task = pd._daemon_task
    if task is not None and not task.done():
        task.cancel()
    pd._daemon_task = None
    pd._signal_event = None
    pd._eval_lock = None
    pd._uvicorn_loop = None


def test_notify_noop_when_not_started():
    """notify_pi_daemon() must never raise when the daemon is not running."""
    assert not is_daemon_running()
    notify_pi_daemon(source="test")
    assert daemon_status() == {"running": False, "event_set": False, "lock_held": False}


def test_notify_from_sync_thread():
    """call_soon_threadsafe correctly sets the event from a non-async thread."""
    loop = asyncio.new_event_loop()
    event = asyncio.Event()
    pd._uvicorn_loop = loop
    pd._signal_event = event

    notify_pi_daemon(source="test_sync")
    loop.run_until_complete(asyncio.sleep(0.01))
    assert event.is_set()

    loop.close()
    pd._uvicorn_loop = None
    pd._signal_event = None


async def test_debounce_batches_signals():
    """Three rapid signals must trigger exactly one evaluate call."""
    eval_count = 0

    async def fake_evaluate(_params):
        nonlocal eval_count
        eval_count += 1
        return {"action": "skip"}

    with patch("aletheia.agent_shell.commands.evaluate_trading_opportunity", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop,
            debounce_sec=0.05,
            cooldown_sec=0.05,
            heartbeat_sec=999.0,
        )

        # Let task initialize _signal_event
        await asyncio.sleep(0.02)

        notify_pi_daemon(source="test_1")
        notify_pi_daemon(source="test_2")
        notify_pi_daemon(source="test_3")

        await asyncio.sleep(0.6)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert eval_count == 1, f"Expected 1 evaluation, got {eval_count}"


async def test_daemon_drains_reported_pending_backlog():
    """One notification keeps evaluating until the command reports no queue backlog."""
    remaining = [2, 1, 0]
    eval_count = 0

    async def fake_evaluate(_params):
        nonlocal eval_count
        value = remaining[min(eval_count, len(remaining) - 1)]
        eval_count += 1
        return {"action": "skip", "pending_queue_remaining": value}

    with patch("aletheia.agent_shell.commands.evaluate_trading_opportunity", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop,
            debounce_sec=0.0,
            cooldown_sec=0.01,
            heartbeat_sec=999.0,
        )
        await asyncio.sleep(0.02)
        notify_pi_daemon(source="backlog_test")
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert eval_count == 3


async def test_heartbeat_triggers_evaluation():
    """When no signals arrive, the heartbeat timeout must trigger evaluation."""
    eval_count = 0

    async def fake_evaluate(_params):
        nonlocal eval_count
        eval_count += 1
        return {"action": "skip"}

    with patch("aletheia.agent_shell.commands.evaluate_trading_opportunity", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=0.05,
        )

        await asyncio.sleep(0.4)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert eval_count >= 1, "Heartbeat should have triggered at least one evaluation"


async def test_skips_when_paused():
    """Daemon must not call evaluate when runtime is paused."""
    eval_count = 0

    async def fake_evaluate(_params):
        nonlocal eval_count
        eval_count += 1
        return {"action": "skip"}

    with patch("aletheia.agent_shell.commands.evaluate_trading_opportunity", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = True
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=0.05,
        )

        await asyncio.sleep(0.3)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert eval_count == 0, "Evaluation must be skipped when paused"


async def test_start_stop_lifecycle():
    """start_pi_daemon creates a running task; stop_pi_daemon cancels it."""
    with patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = True
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0)

        assert is_daemon_running()
        assert task is not None

        task2 = start_pi_daemon(loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0)
        assert task2 is task

        stop_pi_daemon()
        await asyncio.sleep(0.05)

        assert not is_daemon_running()
