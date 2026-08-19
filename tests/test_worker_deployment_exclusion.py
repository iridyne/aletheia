"""Regression coverage for the Worker lock's non-mutating deployment guard."""

from __future__ import annotations

from contextlib import contextmanager

import pytest


def test_worker_cycle_exclusion_preserves_persisted_signature(tmp_path, monkeypatch):
    from aletheia.observation_lab import worker_lock

    path = tmp_path / "worker.lock"
    original = (
        "pid=123\n"
        "acquired_at=2026-08-19T00:00:00+00:00\n"
        "source_commit=worker-sha\n"
    )
    path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(worker_lock, "_lock_path", lambda: path)

    with worker_lock.worker_cycle_exclusion():
        assert path.read_text(encoding="utf-8") == original

    assert path.read_text(encoding="utf-8") == original


def test_auto_rollback_guard_holds_exclusion_while_proving_signature(monkeypatch):
    from aletheia.observation_lab import worker_lock
    from aletheia.update import auto

    held = {"value": False}

    @contextmanager
    def fake_exclusion():
        held["value"] = True
        try:
            yield None
        finally:
            held["value"] = False

    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", fake_exclusion)

    def safe(_guard):
        assert held["value"] is True
        return True

    monkeypatch.setattr(auto, "_observer_rollback_safe", safe)

    with auto._observer_rollback_guard({"required": True}) as rollback_safe:
        assert rollback_safe is True
        assert held["value"] is True

    assert held["value"] is False


def test_auto_rollback_guard_does_not_reinterpret_body_exception(monkeypatch):
    from aletheia.observation_lab import worker_lock
    from aletheia.update import auto

    held = {"value": False}

    @contextmanager
    def fake_exclusion():
        held["value"] = True
        try:
            yield None
        finally:
            held["value"] = False

    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", fake_exclusion)
    monkeypatch.setattr(auto, "_observer_rollback_safe", lambda _guard: True)

    with pytest.raises(RuntimeError, match="rollback exploded"):
        with auto._observer_rollback_guard({"required": True}) as rollback_safe:
            assert rollback_safe is True
            assert held["value"] is True
            raise RuntimeError("rollback exploded")

    assert held["value"] is False
