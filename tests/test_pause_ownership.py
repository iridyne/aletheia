"""Durable multi-owner pause coordination regression tests (Issue #391)."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import MagicMock

from aletheia.config.schema import AletheiaConfig, RuntimeConfig


def _isolated_config(tmp_path, monkeypatch) -> None:
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(
        "aletheia.runtime_home_migration.assert_runtime_home_ready",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(loader, "_emit_consistency_events", lambda _cfg: None)
    loader._invalidate_config_cache()


def test_two_pause_owners_release_independently() -> None:
    runtime = RuntimeConfig()

    connectivity = runtime.acquire_pause(
        "exchange_connectivity",
        reason="exchange probe failed",
        generation="outage-1",
        acquired_at="2026-08-08T00:00:00+00:00",
    )
    startup = runtime.acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
        acquired_at="2026-08-08T00:00:01+00:00",
    )

    assert runtime.paused is True
    assert set(runtime.pause_owners) == {
        "exchange_connectivity",
        "startup_behavior_gate",
    }
    assert connectivity.generation == "outage-1"
    assert startup.generation == "startup-1"

    assert runtime.release_pause("exchange_connectivity", generation="outage-1") is True
    assert runtime.paused is True
    assert set(runtime.pause_owners) == {"startup_behavior_gate"}

    assert runtime.release_pause("startup_behavior_gate", generation="startup-1") is True
    assert runtime.paused is False
    assert runtime.pause_owners == {}


def test_stale_generation_cannot_release_newer_pause() -> None:
    runtime = RuntimeConfig()
    runtime.acquire_pause(
        "exchange_connectivity",
        reason="new outage",
        generation="outage-2",
        acquired_at="2026-08-08T00:01:00+00:00",
    )

    assert runtime.release_pause("exchange_connectivity", generation="outage-1") is False
    assert runtime.paused is True
    assert runtime.pause_owners["exchange_connectivity"].generation == "outage-2"


def test_manual_pause_assignment_coexists_with_connectivity_owner() -> None:
    runtime = RuntimeConfig()
    runtime.acquire_pause(
        "exchange_connectivity",
        reason="exchange probe failed",
        generation="outage-1",
        acquired_at="2026-08-08T00:00:00+00:00",
    )

    runtime.paused = True

    assert set(runtime.pause_owners) == {"exchange_connectivity", "manual"}
    assert runtime.paused_by_connectivity is True
    assert runtime.connectivity_pause_generation == "outage-1"

    runtime.paused = False

    assert set(runtime.pause_owners) == {"exchange_connectivity"}
    assert runtime.paused is True


def test_legacy_connectivity_pause_migrates_to_owner_lease() -> None:
    cfg = AletheiaConfig.model_validate(
        {
            "runtime": {
                "paused": True,
                "paused_by_connectivity": True,
                "connectivity_pause_generation": "legacy-outage",
                "connectivity_pause_acquired_at": "2026-08-07T00:00:00+00:00",
            }
        }
    )

    lease = cfg.runtime.pause_owners["exchange_connectivity"]
    assert lease.generation == "legacy-outage"
    assert lease.acquired_at == "2026-08-07T00:00:00+00:00"
    assert cfg.runtime.paused is True


def test_legacy_unowned_pause_migrates_honestly_to_manual_owner() -> None:
    cfg = AletheiaConfig.model_validate({"runtime": {"paused": True}})

    lease = cfg.runtime.pause_owners["manual"]
    assert lease.generation is None
    assert lease.acquired_at is None
    assert lease.reason == "legacy_unowned_pause"


def test_persisted_owner_release_resumes_only_after_final_owner(
    tmp_path, monkeypatch
) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime.pause_ownership import acquire_pause, release_pause

    resumed: list[bool] = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )

    acquire_pause(
        "exchange_connectivity",
        reason="exchange probe failed",
        generation="outage-1",
    )
    acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
    )

    first = release_pause(
        "exchange_connectivity",
        generation="outage-1",
        resume_when_clear=True,
    )
    assert first.released is True
    assert first.resumed is False
    assert first.remaining_owners == ("startup_behavior_gate",)
    assert resumed == []

    final = release_pause(
        "startup_behavior_gate",
        generation="startup-1",
        resume_when_clear=True,
    )
    assert final.released is True
    assert final.resumed is True
    assert final.remaining_owners == ()
    assert resumed == [True]


def test_persisted_stale_release_never_resumes(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime.pause_ownership import acquire_pause, release_pause

    resumed: list[bool] = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True),
    )
    acquire_pause(
        "exchange_connectivity",
        reason="new outage",
        generation="outage-2",
    )

    result = release_pause(
        "exchange_connectivity",
        generation="outage-1",
        resume_when_clear=True,
    )

    assert result.released is False
    assert result.resumed is False
    assert result.remaining_owners == ("exchange_connectivity",)
    assert resumed == []
    from aletheia.files.jsonl import read_jsonl

    rejected = [
        event
        for event in read_jsonl("audit.jsonl")
        if event.get("event_type") == "pause_owner_release_rejected"
    ]
    assert rejected
    assert rejected[-1]["metadata"]["pause_owner"] == "exchange_connectivity"
    assert rejected[-1]["metadata"]["requested_pause_generation"] == "outage-1"


def test_scheduler_resume_failure_restores_owner_lease(tmp_path, monkeypatch) -> None:
    """A scheduler failure cannot leave config claiming the gateway resumed."""
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime.pause_ownership import acquire_pause, release_pause
    from aletheia.config.loader import load_config

    acquire_pause(
        "exchange_connectivity",
        reason="exchange probe failed",
        generation="outage-1",
    )

    def _resume_fails() -> None:
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        _resume_fails,
    )

    result = release_pause(
        "exchange_connectivity",
        generation="outage-1",
        resume_when_clear=True,
    )

    persisted = load_config()
    assert result.released is False
    assert result.resume_failed is True
    assert persisted.runtime.paused is True
    assert persisted.runtime.pause_owners[
        "exchange_connectivity"
    ].generation == "outage-1"


def test_public_scheduler_resume_refuses_durable_pause_owner(
    tmp_path, monkeypatch
) -> None:
    """No caller may bypass durable ownership through the public scheduler API."""
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import scheduler
    from aletheia.config.loader import save_config

    cfg = AletheiaConfig()
    cfg.runtime.acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
    )
    save_config(cfg)

    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    monkeypatch.setattr(
        scheduler,
        "_selectively_paused_jobs",
        {"paper_trader"},
    )

    resumed = scheduler.resume_scheduler()

    assert resumed is False
    fake_scheduler.resume.assert_not_called()
    fake_scheduler.resume_job.assert_not_called()
    assert scheduler._selectively_paused_jobs == {"paper_trader"}


def test_final_owner_release_uses_owner_aware_scheduler_resume(
    tmp_path, monkeypatch
) -> None:
    """The authoritative final-owner path may re-enter the shared transition lock."""
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import scheduler
    from aletheia.agent_runtime.pause_ownership import acquire_pause, release_pause

    acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
    )

    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    result = release_pause(
        "startup_behavior_gate",
        generation="startup-1",
        resume_when_clear=True,
    )

    assert result.released is True
    assert result.resumed is True
    assert result.remaining_owners == ()
    fake_scheduler.resume.assert_called_once_with()


def test_settings_scheduler_enable_cannot_bypass_pause_owner(
    tmp_path, monkeypatch
) -> None:
    """Settings may enable scheduling in config without resuming owned pauses."""
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import scheduler
    from aletheia.config.loader import load_config, save_config
    from aletheia.server.routes import settings

    cfg = AletheiaConfig()
    cfg.runtime.scheduler_enabled = False
    cfg.runtime.acquire_pause(
        "startup_behavior_gate",
        reason="reconciliation incident",
        generation="startup-1",
    )
    save_config(cfg)

    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda _event: None)

    result = settings.update_runtime_config(
        settings.RuntimeConfigUpdate(scheduler_enabled=True)
    )

    persisted = load_config()
    assert result == {"updated": {"scheduler_enabled": True}}
    assert persisted.runtime.scheduler_enabled is True
    assert set(persisted.runtime.pause_owners) == {"startup_behavior_gate"}
    fake_scheduler.resume.assert_not_called()
    fake_scheduler.resume_job.assert_not_called()


def test_resume_check_and_action_are_atomic_with_pause_acquire(
    tmp_path, monkeypatch
) -> None:
    """A new owner cannot slip between the durable check and scheduler resume."""
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

    from aletheia.agent_runtime import pause_ownership, scheduler
    from aletheia.config.loader import load_config, save_config

    save_config(AletheiaConfig())
    monkeypatch.setattr(pause_ownership, "_audit", lambda *args, **kwargs: None)

    resume_entered = threading.Event()
    allow_resume = threading.Event()
    acquire_started = threading.Event()
    acquire_done = threading.Event()
    call_order: list[str] = []

    class BlockingScheduler:
        state = STATE_PAUSED

        def resume(self) -> None:
            call_order.append("resume_enter")
            resume_entered.set()
            assert allow_resume.wait(timeout=2)
            self.state = STATE_RUNNING
            call_order.append("resume_exit")

        def pause(self) -> None:
            self.state = STATE_PAUSED
            call_order.append("pause")

        def get_jobs(self):
            return []

    fake_scheduler = BlockingScheduler()
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    resume_thread = threading.Thread(target=scheduler.resume_scheduler)

    def _acquire() -> None:
        acquire_started.set()
        pause_ownership.acquire_pause(
            "startup_behavior_gate",
            reason="reconciliation incident",
            generation="startup-1",
            layered=False,
        )
        acquire_done.set()

    acquire_thread = threading.Thread(target=_acquire)
    resume_thread.start()
    assert resume_entered.wait(timeout=2)
    acquire_thread.start()
    assert acquire_started.wait(timeout=2)
    acquired_during_resume = acquire_done.wait(timeout=0.2)
    allow_resume.set()
    resume_thread.join(timeout=2)
    acquire_thread.join(timeout=2)

    assert not resume_thread.is_alive()
    assert not acquire_thread.is_alive()
    assert acquired_during_resume is False
    assert call_order == ["resume_enter", "resume_exit", "pause"]
    assert fake_scheduler.state == STATE_PAUSED
    assert set(load_config().runtime.pause_owners) == {"startup_behavior_gate"}


def test_llm_resume_gate_still_resumes_after_releasing_final_owners(
    tmp_path, monkeypatch
) -> None:
    """Owner enforcement must not deadlock the legitimate LLM-gated resume path."""
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import resume_gate, scheduler
    from aletheia.config.loader import load_config, save_config

    cfg = AletheiaConfig()
    cfg.health.startup_behavior_gate_enabled = False
    cfg.health.require_llm_at_startup = True
    cfg.runtime.acquire_pause(
        "manual",
        reason="operator pause",
        generation="manual-1",
    )
    cfg.runtime.acquire_pause(
        "llm_startup_gate",
        reason="no usable LLM provider",
        generation="llm-1",
    )
    save_config(cfg)

    async def _usable_probe(_cfg, *, max_retries=None):
        return {
            "any_usable": True,
            "checked": 1,
            "results": [{"provider": "test", "usable": True, "error": None}],
        }

    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)
    monkeypatch.setattr(
        resume_gate,
        "probe_llm_providers_at_startup",
        _usable_probe,
    )
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda _event: None)

    result = asyncio.run(
        resume_gate.resume_if_llm_available(
            actor="agent_shell",
            source="command_center",
            max_retries=1,
        )
    )

    assert result["allowed"] is True
    assert load_config().runtime.pause_owners == {}
    fake_scheduler.resume.assert_called_once_with()


def test_trading_brakes_are_durable_scoped_owners(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    from aletheia.risk import kill_switch

    monkeypatch.setattr("aletheia.agent_runtime.scheduler.pause_scheduler", lambda *a, **k: None)
    kill_switch.activate(reason="panic", actor="human", scope="global")

    cfg = load_config()
    lease = cfg.runtime.pause_owners["kill_switch"]
    assert cfg.runtime.kill_switch_active is True
    assert cfg.runtime.paused is True
    assert lease.scope == "global"
    assert lease.source == "dashboard"
    assert lease.generation

    kill_switch.deactivate(actor="human", scope="global")
    cfg = load_config()
    assert cfg.runtime.kill_switch_active is False
    assert "kill_switch" not in cfg.runtime.pause_owners


def test_brake_release_never_bypasses_another_owner(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime.pause_ownership import acquire_pause, release_pause
    from aletheia.config.loader import load_config
    from aletheia.risk import kill_switch

    monkeypatch.setattr("aletheia.agent_runtime.scheduler.pause_scheduler", lambda *a, **k: None)
    resumed: list[bool] = []
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True) or True,
    )
    acquire_pause("manual", reason="operator pause", generation="manual-1")
    kill_switch.activate(reason="panic", actor="human")
    kill_switch.deactivate(actor="human")

    cfg = load_config()
    assert set(cfg.runtime.pause_owners) == {"manual"}
    assert resumed == []

    result = release_pause("manual", generation="manual-1", resume_when_clear=True)
    assert result.released is True
    assert result.resumed is True
    assert resumed == [True]


def test_resume_gate_honors_hard_brake_before_dependency_probe(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config
    from aletheia.risk import kill_switch

    monkeypatch.setattr("aletheia.agent_runtime.scheduler.pause_scheduler", lambda *a, **k: None)
    kill_switch.activate(reason="panic", actor="human")

    async def _probe_must_not_run(_cfg, *, max_retries=None):
        raise AssertionError("hard brake must block before probing")

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", _probe_must_not_run)
    result = asyncio.run(
        resume_gate.resume_if_llm_available(
            actor="human",
            source="dashboard",
            max_retries=1,
        )
    )

    assert result["allowed"] is False
    assert result["reason"] == "trading_brake_active"
    assert result["blockers"] == ["kill_switch"]
    assert load_config().runtime.kill_switch_active is True


def test_scheduler_resume_refuses_hard_brake_owner(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import scheduler
    from aletheia.config.loader import save_config

    cfg = AletheiaConfig()
    cfg.runtime.acquire_pause(
        "kill_switch",
        reason="panic",
        generation="brake-1",
        source="dashboard",
        scope="global",
    )
    save_config(cfg)
    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", fake_scheduler)

    assert scheduler.resume_scheduler() is False
    fake_scheduler.resume.assert_not_called()


def test_mode_brake_only_blocks_its_execution_pool() -> None:
    from aletheia.agent_runtime.pause_ownership import active_pause_blocker_names

    cfg = AletheiaConfig.model_validate(
        {
            "exchange": {"mode": "live"},
            "runtime": {
                "demo_brake_active": True,
            },
        }
    )

    assert cfg.runtime.paused is False
    assert active_pause_blocker_names(cfg) == ()
    cfg.exchange.mode = "demo"
    cfg.runtime.set_pause_scope(cfg.exchange.mode)
    assert cfg.runtime.paused is True
    assert active_pause_blocker_names(cfg) == ("demo_brake",)


def test_legacy_generationless_release_is_exact_not_a_newer_wildcard() -> None:
    legacy = AletheiaConfig.model_validate({"runtime": {"paused": True}})
    assert legacy.runtime.release_pause("manual", generation="") is True

    current = RuntimeConfig()
    current.acquire_pause("manual", reason="operator pause", generation="new-1")
    assert current.release_pause("manual", generation="") is False
    assert current.paused is True


def test_kill_switch_activate_is_idempotent_and_final_release_resumes(
    tmp_path, monkeypatch
) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    from aletheia.risk import kill_switch

    resumed: list[bool] = []
    monkeypatch.setattr("aletheia.agent_runtime.scheduler.pause_scheduler", lambda: None)
    monkeypatch.setattr(
        "aletheia.agent_runtime.scheduler.resume_scheduler",
        lambda: resumed.append(True) or True,
    )

    kill_switch.activate(reason="first", actor="human")
    first = load_config().runtime.pause_owners["kill_switch"]
    kill_switch.activate(reason="second", actor="human")
    second = load_config().runtime.pause_owners["kill_switch"]

    assert (second.generation, second.acquired_at, second.reason) == (
        first.generation,
        first.acquired_at,
        first.reason,
    )
    kill_switch.deactivate(actor="human")
    assert load_config().runtime.pause_owners == {}
    assert resumed == [True]


def test_resume_initializes_then_releases_nonblocking_mode_brake(
    tmp_path, monkeypatch
) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from apscheduler.schedulers.base import STATE_PAUSED

    from aletheia.agent_runtime import scheduler
    from aletheia.config.loader import save_config

    cfg = AletheiaConfig()
    cfg.exchange.mode = "live"
    cfg.runtime.acquire_pause(
        "demo_brake",
        reason="demo panic",
        generation="demo-1",
        scope="demo",
    )
    save_config(cfg)

    fake_scheduler = MagicMock()
    fake_scheduler.state = STATE_PAUSED
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "init_scheduler", lambda: setattr(scheduler, "_scheduler", fake_scheduler))
    monkeypatch.setattr(scheduler, "_selectively_paused_jobs", {"paper_trader"})

    assert scheduler.resume_scheduler() is True
    fake_scheduler.resume.assert_called_once_with()
    fake_scheduler.resume_job.assert_called_once_with("paper_trader")


def test_legacy_brake_state_migrates_without_fabricating_generation() -> None:
    cfg = AletheiaConfig.model_validate(
        {"runtime": {"kill_switch_active": True, "demo_brake_active": True}}
    )

    assert set(cfg.runtime.pause_owners) == {"kill_switch", "demo_brake"}
    assert cfg.runtime.pause_owners["kill_switch"].generation is None
    assert cfg.runtime.pause_owners["demo_brake"].scope == "demo"


def test_pause_owner_projection_reports_age_without_fabricating_legacy_time() -> None:
    from aletheia.agent_runtime.pause_ownership import active_pause_owners
    from aletheia.config.schema import PauseOwnerLease

    cfg = AletheiaConfig()
    cfg.runtime.acquire_pause(
        "manual",
        reason="operator pause",
        generation="manual-1",
        acquired_at="2020-01-01T00:00:00+00:00",
        source="operator",
    )
    cfg.runtime.pause_owners["legacy"] = PauseOwnerLease(
        reason="legacy pause", generation=None, acquired_at=None, source="legacy_config"
    )

    rows = {row["owner"]: row for row in active_pause_owners(cfg)}
    assert rows["manual"]["age_seconds"] is not None
    assert rows["manual"]["age_seconds"] >= 0
    assert rows["legacy"]["age_seconds"] is None


def test_resume_gate_probe_exception_is_durable_fail_closed(tmp_path, monkeypatch) -> None:
    _isolated_config(tmp_path, monkeypatch)
    from aletheia.agent_runtime import resume_gate
    from aletheia.config.loader import load_config, save_config

    cfg = AletheiaConfig()
    cfg.health.startup_behavior_gate_enabled = False
    cfg.health.require_llm_at_startup = True
    cfg.runtime.acquire_pause("manual", reason="operator pause", generation="manual-1")
    save_config(cfg)

    async def _probe_raises(_cfg, *, max_retries=None):
        raise RuntimeError("provider orchestration failed")

    monkeypatch.setattr(resume_gate, "probe_llm_providers_at_startup", _probe_raises)
    result = asyncio.run(
        resume_gate.resume_if_llm_available(
            actor="human",
            source="dashboard",
            max_retries=1,
        )
    )

    assert result["allowed"] is False
    assert result["reason"] == "llm_probe_exception"
    assert set(load_config().runtime.pause_owners) == {"manual", "llm_startup_gate"}
