"""Runtime ownership proof tests for Observation Worker split (#1135)."""

from __future__ import annotations


class _FakeScheduler:
    def __init__(self, *, registered: bool):
        self.registered = registered

    def get_job(self, job_id: str):
        assert job_id == "filter_controller_observe"
        return object() if self.registered else None


def test_worker_owner_without_main_job_is_exclusive(monkeypatch):
    from aletheia.agent_runtime import scheduler as scheduler_mod
    from aletheia.observation_lab import runtime_status

    monkeypatch.setattr(runtime_status, "get_execution_owner", lambda: "worker")
    monkeypatch.setattr(scheduler_mod, "_scheduler", _FakeScheduler(registered=False))

    snapshot = runtime_status.main_scheduler_ownership_snapshot()

    assert snapshot["available"] is True
    assert snapshot["owner"] == "worker"
    assert snapshot["main_cycle_registered"] is False
    assert snapshot["exclusive"] is True


def test_worker_owner_with_main_job_is_conflict(monkeypatch):
    from aletheia.agent_runtime import scheduler as scheduler_mod
    from aletheia.observation_lab import runtime_status

    monkeypatch.setattr(runtime_status, "get_execution_owner", lambda: "worker")
    monkeypatch.setattr(scheduler_mod, "_scheduler", _FakeScheduler(registered=True))

    snapshot = runtime_status.main_scheduler_ownership_snapshot()

    assert snapshot["available"] is True
    assert snapshot["main_cycle_registered"] is True
    assert snapshot["exclusive"] is False


def test_absent_main_scheduler_is_positive_exclusivity_proof(monkeypatch):
    from aletheia.agent_runtime import scheduler as scheduler_mod
    from aletheia.observation_lab import runtime_status

    monkeypatch.setattr(runtime_status, "get_execution_owner", lambda: "worker")
    monkeypatch.setattr(scheduler_mod, "_scheduler", None)

    snapshot = runtime_status.main_scheduler_ownership_snapshot()

    assert snapshot["available"] is True
    assert snapshot["main_cycle_registered"] is False
    assert snapshot["exclusive"] is True


def test_main_owner_registration_is_not_duplicate_ownership(monkeypatch):
    from aletheia.agent_runtime import scheduler as scheduler_mod
    from aletheia.observation_lab import runtime_status

    monkeypatch.setattr(runtime_status, "get_execution_owner", lambda: "main")
    monkeypatch.setattr(scheduler_mod, "_scheduler", _FakeScheduler(registered=True))

    snapshot = runtime_status.main_scheduler_ownership_snapshot()

    assert snapshot["available"] is True
    assert snapshot["owner"] == "main"
    assert snapshot["main_cycle_registered"] is True
    assert snapshot["exclusive"] is True
