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
from aletheia.agent_runtime.wake_events import (
    CATASTROPHIC,
    HORIZON_DUE,
    STOP_LOSS_NEAR,
    TAKE_PROFIT_NEAR,
    WakeEvent,
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
    pd._last_error_summary = None


def test_notify_noop_when_not_started():
    """notify_pi_daemon() must never raise when the daemon is not running."""
    assert not is_daemon_running()
    assert notify_pi_daemon(source="test") is False
    assert daemon_status() == {
        "running": False,
        "event_set": False,
        "lock_held": False,
        "task_generation": 0,
        "started_at": None,
        "active_params": None,
        "last_error_summary": None,
    }


def test_notify_from_sync_thread():
    """call_soon_threadsafe correctly sets the event from a non-async thread."""
    loop = asyncio.new_event_loop()
    event = asyncio.Event()
    pd._uvicorn_loop = loop
    pd._signal_event = event

    assert notify_pi_daemon(source="test_sync") is True
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


async def test_failed_evaluation_rearms_daemon_signal():
    """A failed evaluation must be retried without waiting for another producer."""
    results = [-1, 0]
    eval_count = 0

    async def fake_run_evaluation(_triggered_by, _wake_context=None):
        nonlocal eval_count
        result = results[min(eval_count, len(results) - 1)]
        eval_count += 1
        return result

    with patch.object(pd, "_run_evaluation", new=fake_run_evaluation), \
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
        notify_pi_daemon(source="failure_retry_test")
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    assert eval_count == 2


async def _run_horizon_review_retry(first_result):
    """Return the exact portfolio-review contexts observed across one retry."""
    captured: list[dict] = []
    entry_evaluations: list[dict] = []

    async def fake_review(params):
        captured.append(params)
        if len(captured) == 1:
            if isinstance(first_result, BaseException):
                raise first_result
            return first_result
        return {"action": "hold", "review_completed": True}

    async def fake_entry_evaluation(params):
        entry_evaluations.append(params)
        return {"action": "skip", "pending_queue_remaining": 0}

    with patch(
        "aletheia.agent_shell.commands.review_portfolio_state",
        new=fake_review,
    ), patch(
        "aletheia.agent_shell.commands.evaluate_trading_opportunity",
        new=fake_entry_evaluation,
    ), patch.object(pd, "_has_pending_signal_work", return_value=False), patch(
        "aletheia.config.loader.load_config"
    ) as mock_cfg, patch.object(
        pd, "_PORTFOLIO_RETRY_BASE_SEC", 0.03
    ), patch.object(
        pd, "_PORTFOLIO_RETRY_MAX_SEC", 0.05
    ):
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"

        task = start_pi_daemon(
            loop=asyncio.get_running_loop(),
            debounce_sec=0.0,
            cooldown_sec=0.01,
            heartbeat_sec=999.0,
            coalesce_window_sec=0.0,
        )
        await asyncio.sleep(0.02)
        notify_pi_daemon(
            source=HORIZON_DUE,
            event=WakeEvent(
                position_id="horizon-retry-position",
                kind=HORIZON_DUE,
                ts=0.0,
            ),
        )
        await asyncio.sleep(0.25)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return captured, entry_evaluations


def test_portfolio_retry_backoff_deduplicates_new_same_kind_wakes():
    original = {
        "position_id": "horizon-retry-position",
        "max_priority": 1,
        "events": [{"kind": HORIZON_DUE, "priority": 1, "payload": {}}],
    }
    duplicate = {
        "position_id": "horizon-retry-position",
        "max_priority": 1,
        "events": [{"kind": HORIZON_DUE, "priority": 1, "payload": {"age": 3.0}}],
    }

    assert pd._restore_wake_context({"wake_batches": [original]}, now=100.0) == 1
    assert pd._ready_wake_batches([duplicate], now=399.9) == []
    assert pd._ready_wake_batches([duplicate], now=400.0) == [original]


def test_portfolio_retry_backoff_grows_and_caps_without_losing_batch(monkeypatch):
    monkeypatch.setattr(pd, "_PORTFOLIO_RETRY_BASE_SEC", 10.0)
    monkeypatch.setattr(pd, "_PORTFOLIO_RETRY_MAX_SEC", 25.0)
    batch = {
        "position_id": "retry-cap-position",
        "max_priority": 1,
        "events": [{"kind": HORIZON_DUE, "priority": 1, "payload": {}}],
    }
    context = {"wake_batches": [batch]}

    pd._restore_wake_context(context, now=0.0)
    assert pd._next_retry_delay(0.0) == 10.0
    pd._restore_wake_context(context, now=10.0)
    assert pd._next_retry_delay(10.0) == 20.0
    pd._restore_wake_context(context, now=30.0)
    assert pd._next_retry_delay(30.0) == 25.0
    assert pd._ready_wake_batches([], now=55.0) == [batch]

    pd._clear_retry_wake_context(context)
    assert pd._next_retry_delay(55.0) is None


async def test_review_unavailable_retries_the_same_horizon_wake_batch():
    captured, entry_evaluations = await _run_horizon_review_retry({
        "action": "review_unavailable",
        "reason": "operator_review_unavailable",
        "review_completed": False,
    })

    assert len(captured) == 2
    assert captured[1]["wake_batches"] == captured[0]["wake_batches"]
    assert entry_evaluations == []


async def test_review_timeout_retries_the_same_horizon_wake_batch():
    captured, entry_evaluations = await _run_horizon_review_retry(
        asyncio.TimeoutError()
    )

    assert len(captured) == 2
    assert captured[1]["wake_batches"] == captured[0]["wake_batches"]
    assert entry_evaluations == []


async def test_failed_close_review_retries_instead_of_reporting_daemon_success():
    captured, entry_evaluations = await _run_horizon_review_retry({
        "action": "close_one",
        "positions_closed": 0,
        "close_error": "protective_mark_unavailable",
        "review_completed": False,
    })

    assert len(captured) == 2
    assert captured[1]["wake_batches"] == captured[0]["wake_batches"]
    assert entry_evaluations == []


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


# ── M1.5: wake-event path (coalescing + rate limit) ──────────────────────────

async def _run_daemon_with_event(event, *, wake_capacity=15):
    """Start the daemon, deliver one WakeEvent, return the list of params the fake
    evaluate received."""
    captured: list = []

    async def fake_evaluate(params):
        captured.append(params)
        # Wake-event tests exercise the portfolio-review route.  Return a valid
        # terminal portfolio result so the daemon does not correctly treat this
        # test double as an incomplete review and keep retrying after the test.
        return {"action": "hold", "review_completed": True}

    with patch("aletheia.agent_shell.commands.review_portfolio_state", new=fake_evaluate), \
         patch("aletheia.config.loader.load_config") as mock_cfg:
        mock_cfg.return_value.runtime.paused = False
        mock_cfg.return_value.agent.permission_mode = "Paper"

        loop = asyncio.get_running_loop()
        task = start_pi_daemon(
            loop=loop, debounce_sec=0.02, cooldown_sec=0.02, heartbeat_sec=999.0,
            coalesce_window_sec=0.0, wake_capacity=wake_capacity,
        )
        await asyncio.sleep(0.02)
        notify_pi_daemon(source=event.kind, event=event)
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return captured


async def test_event_wake_passes_context_to_eval():
    """A stop-loss-proximity WakeEvent reaches the evaluation as coalesced context."""
    ev = WakeEvent(position_id="p1", kind=STOP_LOSS_NEAR, ts=0.0, payload={"dist_pct": 0.3})
    captured = await _run_daemon_with_event(ev)

    with_batches = [p for p in captured if p.get("wake_batches")]
    assert with_batches, f"no wake_batches in {captured}"
    batch = with_batches[0]["wake_batches"][0]
    assert batch["position_id"] == "p1"
    assert batch["events"][0]["kind"] == STOP_LOSS_NEAR
    assert batch["events"][0]["payload"] == {"dist_pct": 0.3}


async def test_ratelimited_event_does_not_wake():
    """With zero token budget, a low-priority (P3) event is dropped → no evaluation."""
    ev = WakeEvent(position_id="p1", kind=TAKE_PROFIT_NEAR, ts=0.0)
    captured = await _run_daemon_with_event(ev, wake_capacity=0)
    assert captured == [], f"expected no eval, got {captured}"


async def test_catastrophic_event_bypasses_ratelimit():
    """A P0 catastrophic event evaluates even with zero token budget."""
    ev = WakeEvent(position_id="p1", kind=CATASTROPHIC, ts=0.0)
    captured = await _run_daemon_with_event(ev, wake_capacity=0)
    with_batches = [p for p in captured if p.get("wake_batches")]
    assert with_batches, f"catastrophic must bypass rate limit; got {captured}"
    assert with_batches[0]["wake_batches"][0]["max_priority"] == 0
