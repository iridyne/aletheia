"""Integration tests for exchange connectivity probe + auto-pause/resume pipeline.

These tests exercise the FULL behavior chain, not just structure:
  - Simulated probe failure sequences → scheduler pause + config persistence + audit trail
  - Auto-resume loop: simulated recovery → scheduler resume + config persistence
  - Startup gate: exchange down at boot → immediate pause
  - Smart self-heal interaction: stale data + exchange down → no restart
"""

import asyncio
from datetime import datetime
import sqlite3
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


def setup_env(tmp_path, monkeypatch):
    """Wire up an isolated data dir + fresh DB for each test."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader._invalidate_config_cache()
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _reset_connectivity_state():
    """Reset module-level globals between tests."""
    import aletheia.agent_runtime.connectivity as conn_mod
    conn_mod._consecutive_failures = 0
    conn_mod._consecutive_successes = 0
    conn_mod._exchange_failure_generation = 0
    conn_mod._last_probe_result = None
    conn_mod._last_external_probe_result = None
    conn_mod._external_outage_started_at = None
    conn_mod._external_last_recovery_at = None
    conn_mod._active_resume_task = None
    conn_mod._uvicorn_loop = None


def _claim_connectivity_pause(cfg, generation: str = "test-connectivity-generation"):
    cfg.runtime.acquire_pause(
        "exchange_connectivity",
        reason="exchange probe failed",
        generation=generation,
        acquired_at="2026-08-07T00:00:00+00:00",
    )
    return cfg


@pytest.mark.asyncio
async def test_network_pause_persists_owner_generation_and_timestamp(
    tmp_path, monkeypatch
):
    """A connectivity pause carries restart-safe evidence for its exact owner."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    resume_loop = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: None,
    )
    monkeypatch.setattr(conn_mod, "_exchange_auto_resume_loop", resume_loop)
    monkeypatch.setattr(
        "aletheia.notifications.notify_operator",
        lambda *args, **kwargs: None,
    )

    await conn_mod._pause_for_network(
        cfg,
        {
            "reachable": False,
            "latency_ms": None,
            "error": "timeout",
            "exchange": cfg.exchange.name,
        },
    )
    await asyncio.sleep(0)

    loader._invalidate_config_cache()
    persisted = load_config()
    generation = persisted.runtime.connectivity_pause_generation
    acquired_at = persisted.runtime.connectivity_pause_acquired_at
    assert persisted.runtime.paused is True
    assert persisted.runtime.paused_by_connectivity is True
    assert generation
    assert acquired_at
    datetime.fromisoformat(acquired_at)
    resume_loop.assert_awaited_once_with(
        cfg.health.exchange_auto_resume_interval_sec,
        generation,
    )
    from aletheia.files.jsonl import read_jsonl

    pause_events = [
        event
        for event in read_jsonl("audit.jsonl")
        if event.get("event_type") == "exchange_connectivity_paused"
    ]
    assert pause_events[-1]["after"]["pause_generation"] == generation


@pytest.mark.asyncio
async def test_stale_recovery_after_reload_cannot_release_new_generation(monkeypatch):
    """A recovery snapshot from outage N cannot release persisted outage N+1."""
    import aletheia.agent_runtime.connectivity as conn_mod
    from aletheia.config.schema import AletheiaConfig

    _reset_connectivity_state()
    stale_cfg = AletheiaConfig()
    _claim_connectivity_pause(stale_cfg, generation="outage-n")
    fresh_cfg = stale_cfg.model_copy(deep=True)
    fresh_cfg.runtime.acquire_pause(
        "exchange_connectivity",
        reason="new outage",
        generation="outage-n-plus-one",
    )

    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: fresh_cfg.model_copy(deep=True),
    )
    persisted = []
    monkeypatch.setattr(
        "aletheia.config.loader.save_config",
        lambda cfg: persisted.append(cfg),
    )
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    recovery_attempt_finished = await conn_mod._resume_from_network(stale_cfg)

    assert recovery_attempt_finished is True
    assert persisted == []
    assert resumed == []
    assert fresh_cfg.runtime.paused is True


@pytest.mark.asyncio
async def test_generation_changed_during_llm_await_keeps_pause(tmp_path, monkeypatch):
    """A durable owner change during await invalidates the in-flight recovery."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg, generation="outage-n")
    cfg.health.require_llm_at_startup = True
    save_config(cfg)

    async def _llm_ok_after_new_generation(_cfg, max_retries=1):
        fresh = load_config()
        fresh.runtime.acquire_pause(
            "exchange_connectivity",
            reason="new outage",
            generation="outage-n-plus-one",
        )
        save_config(fresh)
        return {"any_usable": True, "checked": 1, "results": []}

    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup",
        _llm_ok_after_new_generation,
    )
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    recovery_attempt_finished = await conn_mod._resume_from_network(cfg)

    persisted = load_config()
    assert recovery_attempt_finished is True
    assert resumed == []
    assert persisted.runtime.paused is True
    assert persisted.runtime.connectivity_pause_generation == "outage-n-plus-one"


def test_connectivity_status_retains_outage_and_recovery_transitions(monkeypatch):
    import aletheia.agent_runtime.connectivity as conn_mod
    from types import SimpleNamespace

    _reset_connectivity_state()
    monkeypatch.setattr(
        "aletheia.config.loader.load_config",
        lambda: SimpleNamespace(
            health=SimpleNamespace(exchange_probe_interval_sec=60)
        ),
    )
    clock = iter((1000.0, 1060.0, 1061.0))
    monkeypatch.setattr(conn_mod.time, "time", lambda: next(clock))
    monkeypatch.setattr("aletheia.infra.events.emit_infra_event", lambda **_kwargs: None)

    conn_mod._record_external_probe_result({
        "reachable": False,
        "latency_ms": None,
        "error_code": "ConnectTimeout",
    })
    conn_mod._record_external_probe_result({
        "reachable": True,
        "latency_ms": 20.0,
        "error_code": None,
    })

    status = conn_mod.get_status()
    external = status["external_connectivity"]
    assert external["outage_started_at"] == 1000.0
    assert external["last_recovery_at"] == 1060.0
    assert external["state"] == "recovering"
    assert external["severity"] == "info"
    assert external["priority"] == 2


@pytest.mark.asyncio
async def test_exchange_failure_with_working_internet_is_not_global_outage(
    tmp_path, monkeypatch
):
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    cfg.health.exchange_probe_failure_threshold = 5
    save_config(cfg)
    monkeypatch.setattr(
        conn_mod,
        "probe_exchange",
        AsyncMock(return_value={
            "reachable": False,
            "latency_ms": None,
            "error": "HTTP 503",
            "exchange": "bitget",
        }),
    )
    monkeypatch.setattr(
        conn_mod,
        "probe_external_network",
        AsyncMock(return_value={
            "reachable": True,
            "latency_ms": 25.0,
            "error_code": None,
        }),
    )

    await conn_mod.run_connectivity_check()

    assert conn_mod.get_status()["external_connectivity"]["state"] == "reachable"


def test_external_outage_emits_one_root_cause_warning(monkeypatch):
    import aletheia.agent_runtime.connectivity as conn_mod
    from aletheia.infra.events import clear_infra_events, list_infra_events

    _reset_connectivity_state()
    clear_infra_events()
    monkeypatch.setattr(conn_mod.time, "time", lambda: 1000.0)

    result = {"reachable": False, "latency_ms": None, "error_code": "ConnectTimeout"}
    conn_mod._record_external_probe_result(result)
    conn_mod._record_external_probe_result(result)

    warnings = [
        event
        for event in list_infra_events()
        if event["code"] == "external_connectivity_unavailable"
    ]
    assert len(warnings) == 1
    assert warnings[0]["severity"] == "warning"
    assert "network" in warnings[0]["action"].lower()


def test_external_recovery_emits_informational_transition():
    import aletheia.agent_runtime.connectivity as conn_mod
    from aletheia.infra.events import clear_infra_events, list_infra_events

    _reset_connectivity_state()
    clear_infra_events()
    conn_mod._record_external_probe_result(
        {"reachable": False, "latency_ms": None, "error_code": "ConnectTimeout"},
        observed_at=1000.0,
    )
    conn_mod._record_external_probe_result(
        {"reachable": True, "latency_ms": 20.0, "error_code": None},
        observed_at=1060.0,
    )

    recoveries = [
        event
        for event in list_infra_events()
        if event["code"] == "external_connectivity_restored"
    ]
    assert len(recoveries) == 1
    assert recoveries[0]["severity"] == "info"
    assert "no restart" in recoveries[0]["action"].lower()


@pytest.mark.asyncio
async def test_stop_exchange_auto_resume_cancels_and_clears_state():
    import aletheia.agent_runtime.connectivity as conn_mod

    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    conn_mod._active_resume_task = task
    conn_mod._uvicorn_loop = asyncio.get_running_loop()
    await started.wait()

    await conn_mod.stop_exchange_auto_resume_task()

    assert task.cancelled()
    assert conn_mod._active_resume_task is None
    assert conn_mod._uvicorn_loop is None
    await conn_mod.stop_exchange_auto_resume_task()  # idempotent shutdown


# ---------------------------------------------------------------------------
# Test 1: N consecutive probe failures → scheduler paused + config persisted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_consecutive_failures_trigger_pause(tmp_path, monkeypatch):
    """3 consecutive probe failures → scheduler.pause() called + runtime.paused=True persisted."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.exchange_probe_failure_threshold = 3
    cfg.exchange.name = "okx"
    cfg.runtime.paused = False
    cfg.runtime.scheduler_enabled = True
    save_config(cfg)

    pause_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: pause_called.append(True),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )
    # Prevent the resume task from actually spawning (it would run forever in the test loop)
    import aletheia.agent_runtime.connectivity as conn_mod
    monkeypatch.setattr(conn_mod, "_exchange_auto_resume_loop", AsyncMock())

    # Simulate probe always failing
    fake_probe = AsyncMock(return_value={
        "reachable": False, "latency_ms": None,
        "error": "ConnectTimeout", "exchange": "okx",
    })
    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import run_connectivity_check

    # First 2 failures: no pause yet
    r1 = await run_connectivity_check()
    assert r1["action"] is None
    r2 = await run_connectivity_check()
    assert r2["action"] is None

    # 3rd failure: should trigger pause
    r3 = await run_connectivity_check()
    assert r3["action"] == "paused"
    assert pause_called == [True]

    # Verify config was persisted as paused
    cfg_after = load_config()
    assert cfg_after.runtime.paused is True

    # Verify audit trail
    from aletheia.files.jsonl import read_jsonl
    events = list(read_jsonl("audit.jsonl"))
    pause_events = [e for e in events if e.get("event_type") == "exchange_connectivity_paused"]
    assert len(pause_events) >= 1
    assert "okx" in pause_events[-1]["message"]


# ---------------------------------------------------------------------------
# Test 2: Auto-resume loop — 2 consecutive successes → scheduler resumed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auto_resume_on_connectivity_restored(tmp_path, monkeypatch):
    """After pause, auto-resume loop probes until 2 successes → resumes scheduler."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.name = "okx"
    _claim_connectivity_pause(cfg)
    cfg.health.exchange_auto_resume_interval_sec = 0
    cfg.health.exchange_auto_resume_success_threshold = 2
    save_config(cfg)

    resume_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resume_called.append(True),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )

    # Simulate: first probe fails, next 2 succeed
    probe_results = [
        {"reachable": False, "latency_ms": None, "error": "timeout", "exchange": "okx"},
        {"reachable": True, "latency_ms": 50.0, "error": None, "exchange": "okx"},
        {"reachable": True, "latency_ms": 45.0, "error": None, "exchange": "okx"},
    ]
    call_idx = {"n": 0}

    async def fake_probe(cfg=None):
        r = probe_results[min(call_idx["n"], len(probe_results) - 1)]
        call_idx["n"] += 1
        return r

    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    # #146: connectivity resume now also verifies the LLM is up before clearing the
    # shared paused flag — mock it usable so this test isolates the connectivity path.
    async def _llm_ok(cfg, max_retries=1):
        return {"any_usable": True, "checked": 1, "results": []}
    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup", _llm_ok
    )

    # Patch sleep at the module level so the loop doesn't actually wait
    import aletheia.agent_runtime.connectivity as conn_mod
    monkeypatch.setattr(conn_mod.asyncio, "sleep", AsyncMock(return_value=None))

    from aletheia.agent_runtime.connectivity import _exchange_auto_resume_loop
    await _exchange_auto_resume_loop(interval_sec=0)

    # Should have resumed
    assert resume_called == [True]

    # Config persisted as not paused
    cfg_after = load_config()
    assert cfg_after.runtime.paused is False

    # Audit trail
    from aletheia.files.jsonl import read_jsonl
    events = list(read_jsonl("audit.jsonl"))
    resume_events = [e for e in events if e.get("event_type") == "exchange_connectivity_resumed"]
    assert len(resume_events) >= 1


# ---------------------------------------------------------------------------
# Test 3: Startup gate — exchange unreachable at boot → immediate pause
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_gate_pauses_when_exchange_down(tmp_path, monkeypatch):
    """startup_exchange_gate() pauses immediately if exchange is unreachable."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.name = "bitget"
    cfg.runtime.paused = False
    cfg.health.exchange_probe_failure_threshold = 3
    save_config(cfg)

    pause_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: pause_called.append(True),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )
    # Prevent dangling resume task
    import aletheia.agent_runtime.connectivity as conn_mod
    monkeypatch.setattr(conn_mod, "_exchange_auto_resume_loop", AsyncMock())

    fake_probe = AsyncMock(return_value={
        "reachable": False, "latency_ms": None,
        "error": "Network unreachable", "exchange": "bitget",
    })
    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import startup_exchange_gate
    await startup_exchange_gate(cfg)

    assert pause_called == [True]
    cfg_after = load_config()
    assert cfg_after.runtime.paused is True


# ---------------------------------------------------------------------------
# Test 4: Startup gate — exchange reachable → no pause
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_gate_passes_when_exchange_up(tmp_path, monkeypatch):
    """startup_exchange_gate() does nothing if exchange is reachable."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.exchange.name = "okx"
    cfg.runtime.paused = False
    save_config(cfg)

    fake_probe = AsyncMock(return_value={
        "reachable": True, "latency_ms": 42.0, "error": None, "exchange": "okx",
    })
    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import startup_exchange_gate
    await startup_exchange_gate(cfg)

    # Should NOT have paused
    cfg_after = load_config()
    assert cfg_after.runtime.paused is False


@pytest.mark.asyncio
async def test_startup_gate_logs_zero_latency_when_probe_has_no_latency(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    cfg.exchange.name = "local-paper"
    monkeypatch.setattr(
        conn_mod,
        "_probe_exchange_and_external",
        AsyncMock(return_value=(
            {"reachable": True, "latency_ms": None, "error": None, "exchange": "local-paper"},
            {"reachable": True, "latency_ms": 1.0, "error_code": None},
        )),
    )
    logged = []
    monkeypatch.setattr(conn_mod.logger, "info", lambda *args: logged.append(args))

    await conn_mod.startup_exchange_gate(cfg)

    assert logged == [("startup exchange gate: %s reachable (%.0fms)", "local-paper", 0)]


# ---------------------------------------------------------------------------
# Test 5: Smart self-heal: stale data + exchange unreachable → no restart
# ---------------------------------------------------------------------------

def test_self_heal_no_restart_when_exchange_down(tmp_path, monkeypatch):
    """market_data_stale + exchange unreachable → self-heal defers (no restart)."""
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)
    monkeypatch.setattr(health, "_self_heal_suppressed_alerted", False)

    exits: list[int] = []
    monkeypatch.setattr(health, "_os_exit", lambda code: exits.append(code))
    monkeypatch.setattr(health, "_send_alert", lambda msg: None)

    # Simulate: data is stale AND exchange is unreachable
    monkeypatch.setattr(
        health, "compute_health",
        lambda: {"healthy": False, "issues": ["market_data_stale:120min"]},
    )
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: False)

    health.run_health_check()

    # Must NOT restart — network issue, restart won't help
    assert exits == []


# ---------------------------------------------------------------------------
# Test 6: Self-heal: stale data + exchange reachable → restart (process bug)
# ---------------------------------------------------------------------------

def test_self_heal_restarts_when_exchange_up_but_data_stale(tmp_path, monkeypatch):
    """market_data_stale + exchange reachable → self-heal triggers restart."""
    setup_env(tmp_path, monkeypatch)

    from aletheia.agent_runtime import health
    import aletheia.gateway.pidfile as pidfile
    monkeypatch.setattr(pidfile, "supervisor_owns_pid", lambda: True)
    monkeypatch.setattr(health, "_last_alerted_unhealthy", False)
    monkeypatch.setattr(health, "_self_heal_suppressed_alerted", False)

    exits: list[int] = []
    monkeypatch.setattr(health, "_os_exit", lambda code: exits.append(code))
    monkeypatch.setattr(health, "_send_alert", lambda msg: None)

    monkeypatch.setattr(
        health, "compute_health",
        lambda: {"healthy": False, "issues": ["market_data_stale:120min"]},
    )
    monkeypatch.setattr(health, "_exchange_reachable", lambda cfg=None: True)

    health.run_health_check()

    # Should restart — network is fine, data still stale = process/code bug
    assert exits == [42]


# ---------------------------------------------------------------------------
# Test 7: Pause threshold is configurable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pause_threshold_respects_config(tmp_path, monkeypatch):
    """Threshold=5 means 4 failures don't pause, 5th does."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.exchange_probe_failure_threshold = 5
    cfg.exchange.name = "okx"
    cfg.runtime.paused = False
    cfg.runtime.scheduler_enabled = True
    save_config(cfg)

    pause_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: pause_called.append(True),
    )
    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.notify_operator",
        lambda *a, **kw: None,
        raising=False,
    )
    import aletheia.agent_runtime.connectivity as conn_mod
    monkeypatch.setattr(conn_mod, "_exchange_auto_resume_loop", AsyncMock())

    fake_probe = AsyncMock(return_value={
        "reachable": False, "latency_ms": None,
        "error": "timeout", "exchange": "okx",
    })
    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import run_connectivity_check

    # 4 failures: still no pause
    for _ in range(4):
        r = await run_connectivity_check()
        assert r["action"] is None

    assert pause_called == []

    # 5th failure: pause
    r = await run_connectivity_check()
    assert r["action"] == "paused"
    assert pause_called == [True]


# ---------------------------------------------------------------------------
# Test 8: Intermittent failure resets counter (no false pause)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_intermittent_failure_resets_counter(tmp_path, monkeypatch):
    """A success in the middle resets the consecutive failure counter — no false pause."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.exchange_probe_failure_threshold = 3
    cfg.exchange.name = "okx"
    cfg.runtime.paused = False
    cfg.runtime.scheduler_enabled = True
    save_config(cfg)

    pause_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: pause_called.append(True),
    )

    # Sequence: fail, fail, SUCCESS, fail, fail → never hits 3 consecutive
    results_seq = [
        {"reachable": False, "latency_ms": None, "error": "err", "exchange": "okx"},
        {"reachable": False, "latency_ms": None, "error": "err", "exchange": "okx"},
        {"reachable": True, "latency_ms": 30.0, "error": None, "exchange": "okx"},  # resets!
        {"reachable": False, "latency_ms": None, "error": "err", "exchange": "okx"},
        {"reachable": False, "latency_ms": None, "error": "err", "exchange": "okx"},
    ]
    call_idx = {"n": 0}

    async def fake_probe(cfg=None):
        r = results_seq[call_idx["n"]]
        call_idx["n"] += 1
        return r

    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import run_connectivity_check

    for _ in range(5):
        await run_connectivity_check()

    # Should never have paused — the success in the middle reset the counter
    assert pause_called == []


# ---------------------------------------------------------------------------
# Test 9: Already paused → no double pause
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_connectivity_adds_owner_when_already_manually_paused(tmp_path, monkeypatch):
    """An outage adds its own lease without erasing an existing manual pause."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.health.exchange_probe_failure_threshold = 2
    cfg.exchange.name = "okx"
    cfg.runtime.paused = True  # Already paused!
    save_config(cfg)

    pause_called = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.pause_scheduler",
        lambda: pause_called.append(True),
    )

    fake_probe = AsyncMock(return_value={
        "reachable": False, "latency_ms": None,
        "error": "timeout", "exchange": "okx",
    })
    monkeypatch.setattr("aletheia.agent_runtime.connectivity.probe_exchange", fake_probe)

    from aletheia.agent_runtime.connectivity import run_connectivity_check

    for _ in range(5):
        await run_connectivity_check()

    assert pause_called == [True]
    persisted = load_config()
    assert set(persisted.runtime.pause_owners) == {"manual", "exchange_connectivity"}


@pytest.mark.asyncio
async def test_network_recovery_keeps_a_pause_not_owned_by_connectivity(tmp_path, monkeypatch):
    """Recovery must not clear a pause another gate or operator owns."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    cfg.runtime.paused = True
    cfg.runtime.paused_by_connectivity = False
    save_config(cfg)
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._resume_from_network(cfg)

    assert resumed == []
    assert load_config().runtime.paused is True


@pytest.mark.asyncio
async def test_operator_pause_after_network_pause_coexists_with_connectivity(tmp_path, monkeypatch):
    """A later manual pause remains after connectivity releases only its lease."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg)
    save_config(cfg)
    # This is the same assignment made by existing manual/gate pause callers.
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._resume_from_network(cfg)

    assert resumed == []
    persisted = load_config()
    assert persisted.runtime.paused_by_connectivity is False
    assert persisted.runtime.connectivity_pause_generation is None
    assert set(persisted.runtime.pause_owners) == {"manual"}


@pytest.mark.asyncio
async def test_network_recovery_keeps_startup_gate_owner_paused(tmp_path, monkeypatch):
    """Exchange restoration releases connectivity only, never Startup Gate."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg, generation="outage-1")
    cfg.runtime.acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
    )
    save_config(cfg)
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._resume_from_network(cfg, pause_generation="outage-1")

    persisted = load_config()
    assert resumed == []
    assert persisted.runtime.paused is True
    assert set(persisted.runtime.pause_owners) == {"startup_behavior_gate"}
    from aletheia.files.jsonl import read_jsonl

    events = [
        event
        for event in read_jsonl("audit.jsonl")
        if event.get("event_type") == "exchange_connectivity_restored_paused"
    ]
    assert events
    assert events[-1]["after"]["remaining_pause_owners"] == [
        "startup_behavior_gate"
    ]


@pytest.mark.asyncio
async def test_network_recovery_clears_its_own_pause(tmp_path, monkeypatch):
    """Recovery clears the durable connectivity pause only after reloading it."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg)
    save_config(cfg)
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._resume_from_network(cfg)

    assert resumed == [True]
    assert load_config().runtime.paused is False
    assert load_config().runtime.paused_by_connectivity is False
    assert load_config().runtime.connectivity_pause_generation is None
    assert load_config().runtime.connectivity_pause_acquired_at is None


@pytest.mark.asyncio
async def test_network_recovery_keeps_pause_when_required_llm_probe_raises(
    tmp_path, monkeypatch
):
    """An unverified required LLM probe cannot authorize scheduler resume."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg)
    cfg.health.require_llm_at_startup = True
    save_config(cfg)

    async def _llm_probe_raises(cfg, max_retries=1):
        raise RuntimeError("provider probe unavailable")

    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup",
        _llm_probe_raises,
    )
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._resume_from_network(cfg)

    persisted = load_config()
    assert resumed == []
    assert persisted.runtime.paused is True
    assert persisted.runtime.paused_by_connectivity is True
    assert persisted.runtime.connectivity_pause_generation == "test-connectivity-generation"


@pytest.mark.asyncio
async def test_auto_resume_recollects_successes_after_failure_during_llm_probe(tmp_path, monkeypatch):
    """A fenced recovery stays active and resumes only after fresh successes."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg)
    cfg.health.require_llm_at_startup = True
    cfg.health.exchange_auto_resume_success_threshold = 1
    save_config(cfg)

    exchange_probe = {
        "reachable": True,
        "latency_ms": 1.0,
        "error": None,
        "exchange": cfg.exchange.name,
    }
    external_probe = {
        "reachable": True,
        "latency_ms": 1.0,
        "error_code": None,
    }
    probe_count = 0

    async def _exchange_is_reachable(_cfg):
        nonlocal probe_count
        probe_count += 1
        return exchange_probe, external_probe

    llm_probe_count = 0

    async def _llm_ok_with_one_new_exchange_failure(cfg, max_retries=1):
        nonlocal llm_probe_count
        llm_probe_count += 1
        if llm_probe_count == 1:
            conn_mod._record_probe_result(
                {
                    "reachable": False,
                    "latency_ms": None,
                    "error": "new outage",
                    "exchange": cfg.exchange.name,
                }
            )
        return {"any_usable": True, "checked": 1, "results": []}

    monkeypatch.setattr(conn_mod, "_probe_exchange_and_external", _exchange_is_reachable)
    monkeypatch.setattr(conn_mod.asyncio, "sleep", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup",
        _llm_ok_with_one_new_exchange_failure,
    )
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    await conn_mod._exchange_auto_resume_loop(interval_sec=0)

    assert probe_count == 2
    assert llm_probe_count == 2
    assert resumed == [True]
    assert load_config().runtime.paused is False


@pytest.mark.asyncio
async def test_network_recovery_keeps_pause_after_new_failure_during_llm_probe(tmp_path, monkeypatch):
    """A recovery attempt cannot cross a newer exchange failure generation."""
    setup_env(tmp_path, monkeypatch)
    _reset_connectivity_state()

    from aletheia.config.loader import load_config, save_config
    import aletheia.agent_runtime.connectivity as conn_mod

    cfg = load_config()
    _claim_connectivity_pause(cfg)
    cfg.health.require_llm_at_startup = True
    save_config(cfg)

    async def _llm_ok_after_new_exchange_failure(cfg, max_retries=1):
        conn_mod._record_probe_result(
            {
                "reachable": False,
                "latency_ms": None,
                "error": "new outage",
                "exchange": cfg.exchange.name,
            }
        )
        return {"any_usable": True, "checked": 1, "results": []}

    monkeypatch.setattr(
        "aletheia.agent_runtime.startup_gate.probe_llm_providers_at_startup",
        _llm_ok_after_new_exchange_failure,
    )
    resumed = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    recovery_attempt_finished = await conn_mod._resume_from_network(cfg)

    persisted = load_config()
    assert recovery_attempt_finished is False
    assert resumed == []
    assert persisted.runtime.paused is True
    assert persisted.runtime.paused_by_connectivity is True
