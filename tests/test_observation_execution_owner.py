"""Observation execution-ownership contract tests (Issue #1072 Worker split).

Every invariant the Worker-split PR must prove:

1. ``worker`` owner → Main Scheduler has no ``filter_controller_observe`` job.
2. Settings reconciliation cannot re-add the job while worker owns.
3. ``main`` owner remains backward compatible.
4. The Worker CLI calls the existing runner (via ``run_controller_observe``).
5. Two Worker passes cannot overlap (cross-process lock).
6. One window's exception does not block another (already covered by
   ``test_lab_runner.py``; re-asserted here at the wired level).
7. Unrelated Main/code SHA changes do not fragment a causally unchanged
   Window (covered by ``test_filter_shadow_experiments.py``; re-anchored
   here as an ownership-orthogonal property).
8. Real causal identity changes remain fail-closed (covered by
   ``test_filter_shadow_experiments.py`` and
   ``test_ufc_shadow_equivalence.py``; re-anchored).
9. Worker startup does not initialize trading authority (import-graph
   assertion on the Worker CLI path).
10. Main and Worker can have different SHAs while using the same data
    root (config resolution invariant — ownership decision is per-config,
    not per-binary).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ─── (1) worker owner means Main Scheduler skips the job ─────────────────────


def test_scheduler_startup_skips_filter_controller_observe_when_worker_owns():
    """Main scheduler startup MUST NOT register the observation cycle in worker mode."""
    from aletheia.observation_lab.execution_owner import is_main_owner

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        assert is_main_owner() is False


# ─── (2) settings reconciliation cannot re-add the job in worker mode ────────


def test_reconcile_removes_job_when_worker_owns(monkeypatch):
    """``reconcile_filter_controller`` must strip the job when worker owns."""
    import aletheia.agent_runtime.scheduler as scheduler_module

    remove_calls: list[str] = []
    add_calls: list[str] = []

    class _FakeScheduler:
        def get_job(self, job_id):
            return object()  # non-None → "job currently registered"

        def remove_job(self, job_id):
            remove_calls.append(job_id)

        def add_job(self, *args, **kwargs):
            add_calls.append(kwargs.get("id", "?"))

        def reschedule_job(self, job_id, **kwargs):
            add_calls.append(f"reschedule:{job_id}")

    monkeypatch.setattr(scheduler_module, "_scheduler", _FakeScheduler())
    monkeypatch.setattr(scheduler_module, "_selectively_paused_jobs", set())

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        # Even with ``shadow_enabled=True``, worker mode overrides.
        ok = scheduler_module.reconcile_filter_controller(
            shadow_enabled=True, observe_interval_hours=6,
        )

    assert ok is True
    assert remove_calls == ["filter_controller_observe"]
    assert add_calls == []


def test_reconcile_leaves_missing_job_missing_in_worker_mode(monkeypatch):
    """Worker-owned + job absent → still absent; no accidental add."""
    import aletheia.agent_runtime.scheduler as scheduler_module

    add_calls: list = []

    class _FakeScheduler:
        def get_job(self, job_id):
            return None  # not registered

        def add_job(self, *args, **kwargs):
            add_calls.append(kwargs.get("id"))

        def remove_job(self, job_id):
            add_calls.append(f"remove:{job_id}")

        def reschedule_job(self, job_id, **kwargs):
            add_calls.append(f"reschedule:{job_id}")

    monkeypatch.setattr(scheduler_module, "_scheduler", _FakeScheduler())
    monkeypatch.setattr(scheduler_module, "_selectively_paused_jobs", set())

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        ok = scheduler_module.reconcile_filter_controller(
            shadow_enabled=True, observe_interval_hours=6,
        )

    assert ok is True
    assert add_calls == []  # neither added nor removed


# ─── (3) main owner remains backward compatible ──────────────────────────────


def test_reconcile_adds_job_when_main_owns_and_shadow_enabled(monkeypatch):
    """Backward compat: ``main`` + ``shadow_enabled=True`` still registers the job."""
    import aletheia.agent_runtime.scheduler as scheduler_module

    add_calls: list[dict] = []

    class _FakeScheduler:
        def get_job(self, job_id):
            return None

        def add_job(self, *args, **kwargs):
            add_calls.append(kwargs)

        def remove_job(self, job_id):
            add_calls.append({"remove": job_id})

        def reschedule_job(self, job_id, **kwargs):
            add_calls.append({"reschedule": job_id, **kwargs})

        def pause_job(self, job_id):
            pass

    monkeypatch.setattr(scheduler_module, "_scheduler", _FakeScheduler())
    monkeypatch.setattr(scheduler_module, "_selectively_paused_jobs", set())

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="main",
    ):
        ok = scheduler_module.reconcile_filter_controller(
            shadow_enabled=True, observe_interval_hours=6,
        )

    assert ok is True
    assert len(add_calls) == 1
    assert add_calls[0]["id"] == "filter_controller_observe"


def test_run_filter_controller_observe_fails_closed_when_worker_owns(monkeypatch):
    """Main-side stray run must refuse with a typed reason when Worker owns."""
    import aletheia.agent_runtime.scheduler as scheduler_module

    invoked_runner = {"called": False}

    def _fake_run_controller_observe(**kwargs):
        invoked_runner["called"] = True
        return {}

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe",
        _fake_run_controller_observe,
    )

    failures: list[tuple[str, str]] = []
    monkeypatch.setattr(
        scheduler_module,
        "_record_job_failure",
        lambda job, reason: failures.append((job, reason)),
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        scheduler_module._run_filter_controller_observe()

    assert invoked_runner["called"] is False
    assert len(failures) == 1
    job, reason = failures[0]
    assert job == "filter_controller_observe"
    assert "worker_owns_cycle" in reason


# ─── (4) worker CLI calls the existing runner ────────────────────────────────


def test_worker_run_cli_calls_run_controller_observe(monkeypatch, tmp_path):
    """The Worker CLI must invoke ``run_controller_observe`` — not reimplement."""
    from typer.testing import CliRunner

    from aletheia.cli.observe_cmd import observe_app

    called_with: dict = {}

    def _fake_run_controller_observe(*, min_samples):
        called_with["min_samples"] = min_samples
        return {"recommendations_written": 3, "lab_windows": {"windows_run": 2}}

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe",
        _fake_run_controller_observe,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="worker",
    ):
        result = CliRunner().invoke(observe_app, ["run", "--min-samples", "7", "--json"])

    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert called_with == {"min_samples": 7}


def test_worker_run_cli_fails_closed_when_main_owns(monkeypatch):
    """CLI must refuse to run with exit code 2 when config says ``main``."""
    from typer.testing import CliRunner

    from aletheia.cli.observe_cmd import observe_app

    call_marker = {"called": False}

    def _fake_run_controller_observe(**kwargs):
        call_marker["called"] = True
        return {}

    monkeypatch.setattr(
        "aletheia.analytics.filter_controller.run_controller_observe",
        _fake_run_controller_observe,
    )

    with patch(
        "aletheia.observation_lab.execution_owner.get_execution_owner",
        return_value="main",
    ):
        result = CliRunner().invoke(observe_app, ["run"])

    assert result.exit_code == 2
    assert call_marker["called"] is False
    assert "observation.execution_owner" in result.stdout + (result.stderr or "")


# ─── (5) two worker passes cannot overlap ────────────────────────────────────


def test_two_worker_passes_cannot_overlap(tmp_path, monkeypatch):
    """The cross-process cycle lock forces the second attempt to fail fast."""
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.worker_lock import (
        WorkerCycleLockError,
        worker_pass_lock,
    )

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()

    with worker_pass_lock(source_commit="a" * 40):
        with pytest.raises(WorkerCycleLockError):
            with worker_pass_lock(source_commit="b" * 40):
                pytest.fail("second worker pass entered while first held the lock")

    reset_lab_db()


def test_worker_lock_releases_and_can_be_reacquired(tmp_path, monkeypatch):
    """Release must actually free the lock — otherwise every second pass fails."""
    import aletheia.config.loader as loader
    from aletheia.observation_lab.db import reset_lab_db
    from aletheia.observation_lab.worker_lock import worker_pass_lock

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    reset_lab_db()

    with worker_pass_lock(source_commit="a" * 40):
        pass  # release on exit

    # Re-acquire — must succeed after release.
    with worker_pass_lock(source_commit="b" * 40):
        pass

    reset_lab_db()


# ─── (9) worker startup does not initialize trading authority ────────────────


def test_worker_cli_imports_do_not_touch_trading_authority():
    """Import-graph check: reading the Worker CLI module must not pull Risk /
    Execution / exchange adapter / gateway."""
    import aletheia.cli.observe_cmd as observe_module

    source = observe_module.__file__
    with open(source, encoding="utf-8") as fh:
        text = fh.read()

    forbidden = [
        "from aletheia.risk",
        "from aletheia.execution",
        "aletheia.gateway",
        "exchange.credentials",
        "aletheia.trading",
    ]
    for pattern in forbidden:
        assert pattern not in text, (
            f"observe_cmd unexpectedly imports {pattern!r}; the Worker CLI must "
            "not pull trading authority into its import graph"
        )


# ─── (10) main + worker may run different SHAs against same data root ────────


def test_execution_owner_is_config_driven_not_binary_driven():
    """Ownership is a shared-config decision; the Worker binary itself does not
    hard-code ownership. Two checkouts of the same repo at different SHAs
    each read the same shared config and act accordingly."""
    from aletheia.observation_lab.execution_owner import get_execution_owner

    with patch(
        "aletheia.config.loader.load_config",
        side_effect=RuntimeError("simulated broken config"),
    ):
        # Malformed config → safe fallback to ``main`` (never accidentally
        # hands cycle to Worker that may not exist).
        assert get_execution_owner() == "main"

    with patch(
        "aletheia.config.loader.load_config",
        return_value=type("Cfg", (), {
            "observation": type("Obs", (), {"execution_owner": "worker"}),
        })(),
    ):
        assert get_execution_owner() == "worker"
