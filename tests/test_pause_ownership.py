"""Durable multi-owner pause coordination regression tests (Issue #391)."""

from __future__ import annotations

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
