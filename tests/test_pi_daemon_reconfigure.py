"""Tests for pi_daemon runtime reconfiguration (issue #402).

Covers the contract: POST /api/settings/pi_daemon applies the requested
runtime state immediately rather than waiting for a gateway restart.
"""

import asyncio
from unittest.mock import patch

import pytest

import aletheia.agent_runtime.pi_daemon as pd
from aletheia.agent_runtime.pi_daemon import (
    daemon_status,
    is_daemon_running,
    reconfigure_pi_daemon,
    start_pi_daemon,
    stop_pi_daemon,
)


@pytest.fixture(autouse=True)
def reset_daemon_state():
    """Clean module-level state before and after every test."""
    pd._daemon_task = None
    pd._signal_event = None
    pd._eval_lock = None
    pd._uvicorn_loop = None
    pd._coalescer = None
    pd._pending_events.clear()
    pd._retry_wake_batches.clear()
    pd._generic_wake_pending = False
    pd._task_generation = 0
    pd._started_at = None
    pd._active_params = None
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
    pd._retry_wake_batches.clear()
    pd._generic_wake_pending = False
    pd._task_generation = 0
    pd._started_at = None
    pd._active_params = None


def _paused_cfg(paused=False, mode="Paper"):
    """Return a mock config object with the requested runtime state."""
    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.runtime.paused = paused
    cfg.agent.permission_mode = mode
    cfg.health.startup_behavior_gate_enabled = False
    return cfg


# ---------------------------------------------------------------------------
# enable-after-boot
# ---------------------------------------------------------------------------

async def test_enable_after_boot_starts_daemon():
    """reconfigure(enabled=True) starts the daemon when it is not running."""
    assert not is_daemon_running()

    loop = asyncio.get_running_loop()
    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result == {"configured": True, "running": True, "blocker": None}
    assert is_daemon_running()
    assert pd._task_generation == 1
    assert pd._started_at is not None
    assert pd._active_params == {"debounce_sec": 0.0, "cooldown_sec": 0.0, "heartbeat_sec": 999.0}


# ---------------------------------------------------------------------------
# disable-while-running
# ---------------------------------------------------------------------------

async def test_disable_while_running_stops_daemon():
    """reconfigure(enabled=False) cancels and awaits the running task."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0)

    assert is_daemon_running()
    gen_before = pd._task_generation

    result = await reconfigure_pi_daemon(
        enabled=False,
        debounce_sec=0.0,
        cooldown_sec=0.0,
        heartbeat_sec=999.0,
        loop=loop,
    )

    assert result == {"configured": False, "running": False, "blocker": None}
    assert not is_daemon_running()
    assert pd._daemon_task is None
    assert pd._uvicorn_loop is None
    assert pd._active_params is None
    # generation counter is not reset on stop (intentional — it's a lifetime counter)
    assert pd._task_generation == gen_before


async def test_disable_prevents_evaluation_after_complete():
    """After reconfigure(enabled=False) returns, no new evaluation can run."""
    eval_count = 0

    async def fake_evaluate():
        nonlocal eval_count
        eval_count += 1
        return {"action": "skip"}

    loop = asyncio.get_running_loop()
    with patch("aletheia.agent_shell.entry_planner.run_entry_planner_batch", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0)
        await asyncio.sleep(0.02)  # let loop initialise _signal_event

    result = await reconfigure_pi_daemon(
        enabled=False,
        debounce_sec=0.0,
        cooldown_sec=0.0,
        heartbeat_sec=999.0,
        loop=loop,
    )
    assert not result["running"]

    # Attempt to notify after disable — must be a no-op
    from aletheia.agent_runtime.pi_daemon import notify_pi_daemon
    delivered = notify_pi_daemon(source="post_disable_test")
    assert not delivered
    await asyncio.sleep(0.05)
    assert eval_count == 0, "No evaluation must occur after disable completes"


# ---------------------------------------------------------------------------
# parameter update (controlled restart)
# ---------------------------------------------------------------------------

async def test_parameter_update_restarts_daemon_with_new_params():
    """Changing debounce/cooldown/heartbeat restarts the daemon atomically."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=10.0, cooldown_sec=60.0, heartbeat_sec=300.0)

    assert is_daemon_running()
    old_task = pd._daemon_task
    gen_before = pd._task_generation

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=1.0,
            cooldown_sec=5.0,
            heartbeat_sec=120.0,
            loop=loop,
        )

    assert result == {"configured": True, "running": True, "blocker": None}
    assert is_daemon_running()
    assert pd._daemon_task is not old_task, "Must be a new task after parameter change"
    assert pd._task_generation == gen_before + 1
    assert pd._active_params == {"debounce_sec": 1.0, "cooldown_sec": 5.0, "heartbeat_sec": 120.0}
    # old task must be done (cancelled during restart)
    assert old_task.done()


async def test_noop_when_already_running_with_same_params():
    """reconfigure with identical params does not restart the daemon."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=2.0, cooldown_sec=45.0, heartbeat_sec=60.0)

    original_task = pd._daemon_task
    gen_before = pd._task_generation

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=2.0,
            cooldown_sec=45.0,
            heartbeat_sec=60.0,
            loop=loop,
        )

    assert result == {"configured": True, "running": True, "blocker": None}
    assert pd._daemon_task is original_task, "Task must not be replaced when params are identical"
    assert pd._task_generation == gen_before


# ---------------------------------------------------------------------------
# gate-blocked enable
# ---------------------------------------------------------------------------

async def test_startup_behavior_gate_blocks_enable():
    """reconfigure returns blocker='startup_behavior_gate' when gate rejects."""
    loop = asyncio.get_running_loop()

    def gate_rejects(_cfg=None):
        return False

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()), \
         patch("aletheia.agent_runtime.startup_behavior_gate.gate_allows_startup", side_effect=gate_rejects):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result == {"configured": True, "running": False, "blocker": "startup_behavior_gate"}
    assert not is_daemon_running()


async def test_runtime_paused_blocks_enable():
    """reconfigure returns blocker='runtime_paused' when runtime.paused=True."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result["blocker"] == "runtime_paused"
    assert not result["running"]
    assert not is_daemon_running()


async def test_wrong_permission_mode_blocks_enable():
    """reconfigure returns a blocker when permission_mode is Observe."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(mode="Observe")):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result["blocker"].startswith("permission_mode:")
    assert not result["running"]


# ---------------------------------------------------------------------------
# daemon_status reflects active params
# ---------------------------------------------------------------------------

async def test_daemon_status_exposes_active_params():
    """daemon_status() returns task_generation and active_params after start."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=5.0, cooldown_sec=30.0, heartbeat_sec=120.0)

    status = daemon_status()
    assert status["running"] is True
    assert status["task_generation"] == 1
    assert status["started_at"] is not None
    assert status["active_params"] == {
        "debounce_sec": 5.0,
        "cooldown_sec": 30.0,
        "heartbeat_sec": 120.0,
    }

    stop_pi_daemon()
    status = daemon_status()
    assert status["running"] is False
    assert status["active_params"] is None
    assert status["started_at"] is None


# ---------------------------------------------------------------------------
# pending wake events survive restart
# ---------------------------------------------------------------------------

async def test_pending_events_survive_parameter_restart():
    """#1410: accepted wakes buffered before a parameter restart are evaluated by
    the next generation exactly once, with their original per-position context."""
    from aletheia.agent_runtime.wake_events import HORIZON_DUE, STOP_LOSS_NEAR, WakeEvent

    loop = asyncio.get_running_loop()
    evaluations: list[tuple[str, dict]] = []

    async def record(triggered_by, wake_context=None):
        evaluations.append((triggered_by, wake_context or {}))
        return 0

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=False)), \
            patch.object(pd, "_run_evaluation", record), \
            patch.object(pd, "_has_pending_signal_work", lambda: False):
        start_pi_daemon(
            loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0,
            coalesce_window_sec=0.05,
        )
        await asyncio.sleep(0.02)
        assert pd._task_generation == 1

        # Accepted before the restart, not yet evaluated: a generic wake, a
        # HORIZON_DUE exit-review event, and a P1 batch held in the coalescer.
        pd._generic_wake_pending = True
        pd._pending_events.append(WakeEvent("pos-X", HORIZON_DUE, ts=1.0, payload={"horizon": "4h"}))
        assert pd._coalescer is not None
        pd._coalescer.add(WakeEvent("pos-Y", STOP_LOSS_NEAR, ts=2.0, payload={"dist_pct": 0.4}), loop.time())

        await reconfigure_pi_daemon(
            enabled=True, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=0.1, loop=loop,
        )
        assert pd._task_generation == 2
        for _ in range(100):
            if len({b["position_id"] for _, ctx in evaluations for b in ctx.get("wake_batches", [])}) >= 2:
                break
            await asyncio.sleep(0.02)

    assert evaluations[0][0] == "signal", "generic wake must re-arm generation 2"
    batches = [b for _, ctx in evaluations for b in ctx.get("wake_batches", [])]
    by_position = {b["position_id"]: b for b in batches}
    assert sorted(b["position_id"] for b in batches) == ["pos-X", "pos-Y"]  # exactly once each
    assert by_position["pos-X"]["events"][0]["payload"] == {"horizon": "4h"}
    assert by_position["pos-Y"]["events"][0]["payload"] == {"dist_pct": 0.4}


async def test_full_stop_drops_pending_wake_state():
    loop = asyncio.get_running_loop()
    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg(paused=True)):
        start_pi_daemon(loop=loop, debounce_sec=0.0, cooldown_sec=0.0, heartbeat_sec=999.0)
        await asyncio.sleep(0.02)
    pd._generic_wake_pending = True
    stop_pi_daemon()
    assert pd._generic_wake_pending is False
    assert pd._coalescer is None


# ---------------------------------------------------------------------------
# startup gate exception / import failure — fail-closed regression (#1103)
# ---------------------------------------------------------------------------

async def test_startup_gate_exception_blocks_enable():
    """gate_allows_startup() raising must block enablement (fail-closed)."""
    loop = asyncio.get_running_loop()

    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()), \
         patch("aletheia.agent_runtime.startup_behavior_gate.gate_allows_startup",
               side_effect=RuntimeError("injected gate failure")):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result == {"configured": True, "running": False, "blocker": "startup_behavior_gate_error"}
    assert not is_daemon_running()
    assert pd._task_generation == 0


async def test_startup_gate_import_failure_blocks_enable():
    """Gate module import failure must block enablement (fail-closed)."""
    import sys
    loop = asyncio.get_running_loop()

    module_name = "aletheia.agent_runtime.startup_behavior_gate"
    with patch("aletheia.config.loader.load_config", return_value=_paused_cfg()), \
         patch.dict(sys.modules, {module_name: None}):
        result = await reconfigure_pi_daemon(
            enabled=True,
            debounce_sec=0.0,
            cooldown_sec=0.0,
            heartbeat_sec=999.0,
            loop=loop,
        )

    assert result["blocker"] == "startup_behavior_gate_error"
    assert not result["running"]
    assert not is_daemon_running()
    assert pd._task_generation == 0
