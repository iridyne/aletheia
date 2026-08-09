from types import SimpleNamespace

from aletheia.agent_runtime import scheduler


def test_scheduler_snapshot_distinguishes_config_from_runtime_and_tracks_tick(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler,
        "load_config",
        lambda: SimpleNamespace(runtime=SimpleNamespace(scheduler_enabled=True)),
    )
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "_scheduler_last_tick", None)

    assert scheduler.status_snapshot() == {
        "enabled": True,
        "running": False,
        "state": "not_started",
        "job_count": 0,
        "last_tick": None,
    }

    scheduler._record_scheduler_tick(None)
    assert scheduler.status_snapshot()["last_tick"] is not None
