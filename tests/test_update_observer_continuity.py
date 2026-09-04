"""Focused tests for Issue #1135 Observer-aware source updates."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _settled_update_boundary(monkeypatch):
    from aletheia.observation_lab import runner
    from aletheia.update import applier
    monkeypatch.setattr(applier, "_stop_gateway", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": True})
    monkeypatch.setattr(runner, "settle_observation_continuity", lambda: {"ok": True})


def _snapshot(
    *,
    owner: str = "worker",
    disk_sha: str = "old111",
    gateway_sha: str = "old111",
    worker_sha: str | None = "worker999",
    data_root: str | None = "sha256:data",
    observation_ids: tuple[str, ...] = ("obs-a", "obs-b"),
    generations: dict[str, int] | None = None,
    overview_available: bool = True,
    identities_available: bool = True,
    checkpoint_ok: bool = True,
    runtime_owner: str | None = None,
    runtime_proof_available: bool = True,
    main_cycle_registered: bool = False,
    health_status: str | None = "healthy",
    capture_available: bool = True,
    capture_source: str = "fresh_target_process",
):
    generations = generations or {"obs-a": 2}
    runtime_owner = runtime_owner or owner
    return {
        "schema_version": "update_observer_continuity/v1",
        "capture_available": capture_available,
        "capture_source": capture_source,
        "control": {
            "available": True,
            "disk_sha": disk_sha,
            "running_sha": gateway_sha,
            "data_root": {
                "available": data_root is not None,
                "identity": data_root,
            },
        },
        "gateway": {
            "available": True,
            "boot_commit": gateway_sha,
            "current_commit": gateway_sha,
            "scheduler_running": True,
            "scheduler_job_count": 10,
            "health_status": health_status,
            "health_issues": [] if health_status == "healthy" else ["test_health"],
            "observation_execution": {
                "available": runtime_proof_available,
                "owner": runtime_owner,
                "job_id": "filter_controller_observe",
                "main_cycle_registered": (main_cycle_registered if runtime_proof_available else None),
                "exclusive": (
                    not (runtime_owner == "worker" and main_cycle_registered) if runtime_proof_available else None
                ),
            },
        },
        "observation": {
            "owner": owner,
            "exclusive_owner_contract": owner in {"main", "worker"},
            "worker": {
                "available": worker_sha is not None,
                "source_commit": worker_sha,
            },
            "overview_available": overview_available,
            "identities": {
                "available": identities_available,
                "ids": list(observation_ids) if identities_available else [],
                "total": len(observation_ids) if identities_available else None,
            },
            "manifest_generations": generations,
            "checkpoint": {"verified": {"ok": checkpoint_ok, "reason": "ok" if checkpoint_ok else "drift"}},
            "experiments": {
                "total": len(observation_ids),
                "collecting": len(observation_ids),
                "ready_for_review": 0,
                "invalidated": 0,
            },
            "ufc_equivalence": {"recent_total": 1, "recent_matched": 1},
        },
    }


def test_compare_allows_normal_observation_progress():
    from aletheia.update.continuity import compare_update_continuity

    before = _snapshot(capture_source="in_process")
    after = _snapshot(
        disk_sha="new222",
        gateway_sha="new222",
        observation_ids=("obs-a", "obs-b", "obs-c"),
        generations={"obs-a": 3, "obs-c": 1},
    )
    after["observation"]["experiments"]["collecting"] = 1
    after["observation"]["experiments"]["ready_for_review"] = 2

    result = compare_update_continuity(before, after, expected_target="new222", restart=True)

    assert result["ok"] is True
    assert result["failures"] == []
    assert result["proof_gaps"] == []
    assert result["worker_sha_before"] == "worker999"
    assert result["worker_sha_after"] == "worker999"
    assert result["data_root_unchanged"] is True
    assert result["main_health_status"] == "healthy"
    assert result["postflight_capture_source"] == "fresh_target_process"


def test_compare_detects_owner_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(owner="worker"),
        _snapshot(owner="main", disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert "observation_owner_changed" in result["failures"]


def test_compare_detects_worker_identity_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(worker_sha="worker111"),
        _snapshot(worker_sha="worker222", disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert "worker_identity_changed" in result["failures"]


def test_compare_detects_data_root_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(data_root="sha256:before"),
        _snapshot(data_root="sha256:after", disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert "data_root_changed" in result["failures"]


def test_compare_detects_observation_history_loss():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(observation_ids=("obs-a", "obs-b")),
        _snapshot(observation_ids=("obs-b",), disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert "observation_history_missing" in result["failures"]


def test_compare_detects_manifest_generation_regression():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(generations={"obs-a": 4}),
        _snapshot(generations={"obs-a": 3}, disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert "manifest_generation_regressed:obs-a" in result["failures"]


def test_preflight_fails_closed_on_structural_checkpoint_drift():
    from aletheia.update.continuity import evaluate_preflight

    result = evaluate_preflight(_snapshot(checkpoint_ok=False))

    assert result["ok"] is False
    assert result["failures"] == ["observation_checkpoint_drift"]


def test_preflight_detects_duplicate_worker_and_main_cycle_owner():
    from aletheia.update.continuity import evaluate_preflight

    result = evaluate_preflight(_snapshot(owner="worker", main_cycle_registered=True))

    assert result["ok"] is False
    assert "duplicate_observation_cycle_owner" in result["failures"]


def test_preflight_pending_business_state_is_not_a_gate():
    from aletheia.update.continuity import evaluate_preflight

    snapshot = _snapshot()
    snapshot["observation"]["experiments"] = {
        "total": 3,
        "collecting": 3,
        "ready_for_review": 0,
        "invalidated": 0,
    }

    result = evaluate_preflight(snapshot)

    assert result["ok"] is True
    assert result["failures"] == []


def test_missing_structural_baseline_is_proof_gap_not_verified():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(data_root=None, worker_sha=None),
        _snapshot(
            data_root=None,
            worker_sha=None,
            disk_sha="new222",
            gateway_sha="new222",
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["structural_ok"] is True
    assert result["ok"] is False
    assert "data_root_comparison_unavailable" in result["proof_gaps"]
    assert "worker_identity_comparison_unavailable" in result["proof_gaps"]


def test_compare_runtime_owner_proof_unavailable_is_unproven():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            runtime_proof_available=False,
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["failures"] == []
    assert "observation_runtime_owner_proof_unavailable" in result["proof_gaps"]
    assert result["ok"] is False


def test_compare_detects_runtime_owner_mismatch():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            runtime_owner="main",
        ),
        expected_target="new222",
        restart=True,
    )

    assert "observation_runtime_owner_mismatch" in result["failures"]


def test_compare_degraded_main_health_is_failure():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            health_status="degraded",
        ),
        expected_target="new222",
        restart=True,
    )

    assert "main_health_degraded" in result["failures"]


def test_compare_initializing_main_health_is_warning_only():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            health_status="initializing",
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["failures"] == []
    assert result["proof_gaps"] == []
    assert "main_health_initializing" in result["warnings"]


def test_no_restart_never_emits_verified_claim():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(disk_sha="new222"),
        expected_target="new222",
        restart=False,
    )

    assert result["ok"] is False
    assert result["failures"] == []
    assert result["proof_gaps"] == ["main_restart_required_for_full_verification"]


def test_fresh_capture_parses_target_process_payload(monkeypatch):
    from aletheia.update import continuity

    payload = _snapshot(disk_sha="new222", gateway_sha="new222")
    stdout = continuity._FRESH_CAPTURE_PREFIX + json.dumps(payload)
    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ALETHEIA_UPDATE_BEARER_TOKEN", "token")
    monkeypatch.setattr(continuity.subprocess, "run", fake_run)

    result = continuity.capture_update_continuity_fresh()

    assert result["capture_available"] is True
    assert result["capture_source"] == "fresh_target_process"
    assert result["control"]["disk_sha"] == "new222"
    assert "OPENAI_API_KEY" not in seen["env"]
    assert "ALETHEIA_UPDATE_BEARER_TOKEN" not in seen["env"]


def test_fresh_capture_failure_is_explicit(monkeypatch):
    from aletheia.update import continuity

    monkeypatch.setattr(
        continuity.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    result = continuity.capture_update_continuity_fresh()

    assert result["capture_available"] is False
    assert result["capture_error"] == "target_postflight_process_failed"


def test_minimal_collector_does_not_call_full_diagnostics():
    import inspect
    from aletheia.update import continuity

    source = inspect.getsource(continuity.capture_update_continuity)
    assert "build_control_status" not in source
    assert "_collect_overview" not in source
    assert "experiments" not in source
    assert "ufc" not in source.lower()


def test_unavailable_target_capture_is_only_a_proof_gap():
    from aletheia.update import continuity

    result = continuity.compare_update_continuity(
        _snapshot(),
        continuity._unavailable_fresh_snapshot("target_postflight_process_failed"),
        expected_target="new222",
        restart=True,
    )

    assert result["failures"] == []
    assert result["proof_gaps"] == ["target_postflight_projection_unavailable"]
    assert result["structural_ok"] is True


def test_unavailable_capture_preserves_direct_gateway_mismatch():
    from aletheia.update import continuity

    after = continuity._unavailable_fresh_snapshot("target_postflight_process_failed")
    after["gateway"] = {
        "boot_commit": "wrong",
        "current_commit": "wrong",
        "health_status": "healthy",
    }
    result = continuity.compare_update_continuity(_snapshot(), after, expected_target="new222", restart=True)

    assert result["failures"] == ["boot_commit_mismatch", "current_commit_mismatch"]
    assert result["proof_gaps"] == ["target_postflight_projection_unavailable"]


def test_wrapper_preserves_existing_applier_and_uses_fresh_postflight(monkeypatch):
    from aletheia.update import applier, continuity

    before = _snapshot(capture_source="in_process")
    after = _snapshot(disk_sha="new222", gateway_sha="new222")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: after["gateway"])
    monkeypatch.setattr(continuity, "capture_update_continuity_fresh", lambda **_: after)
    seen = {}

    def apply_update(*, restart, progress=None):
        seen["restart"] = restart
        return {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
            "frontend_built": True,
            "restart_requested": restart,
            "restart_completed": restart,
            "restart_required": not restart,
        }

    monkeypatch.setattr(applier, "apply_update", apply_update)
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)
    result = continuity.apply_update_with_continuity(restart=True)

    assert seen["restart"] is True
    assert result["applied"] is True
    assert result.get("rolled_back") is not True
    assert result["continuity_verified"] is True
    assert result["continuity_state"] == "verified"
    assert result["runtime_old_commit"] == "old111"
    assert result["continuity"]["comparison"]["postflight_capture_source"] == "fresh_target_process"


def test_wrapper_uses_runtime_boot_commit_when_source_is_already_current(monkeypatch):
    from aletheia.update import applier, continuity

    before = _snapshot(disk_sha="target-sha", gateway_sha="old-sha", capture_source="in_process")
    after = _snapshot(disk_sha="target-sha", gateway_sha="target-sha")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: after["gateway"])
    monkeypatch.setattr(continuity, "capture_update_continuity_fresh", lambda **_: after)
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "target-sha",
            "new_commit": "target-sha",
            "source_changed": False,
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)
    result = continuity.apply_update_with_continuity(restart=True)

    assert result["runtime_old_commit"] == "old-sha"
    assert result["old_commit"] == "target-sha"
    assert result["new_commit"] == "target-sha"


def test_wrapper_does_not_call_applier_when_preflight_fails(monkeypatch):
    from aletheia.update import applier, continuity

    monkeypatch.setattr(
        continuity,
        "capture_update_continuity",
        lambda: _snapshot(checkpoint_ok=False),
    )
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not update")),
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["reason"] == "observer_preflight_failed"
    assert result["continuity_state"] == "failed"


def test_wrapper_accepts_initializing_after_structural_proof(monkeypatch):
    from aletheia.update import applier, continuity

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: _snapshot())
    initializing = _snapshot(
        disk_sha="new222",
        gateway_sha="new222",
        health_status="initializing",
    )
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: initializing["gateway"])
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_fresh",
        lambda **_: initializing,
    )
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        applier,
        "rollback_applied_update",
        lambda result: (_ for _ in ()).throw(AssertionError("must not roll back warmup")),
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is True
    assert result["continuity_verified"] is True
    assert result["continuity_state"] == "verified"
    assert result["continuity"]["comparison"]["proof_gaps"] == []
    assert "main_health_initializing" in result["continuity"]["comparison"]["warnings"]


def test_postflight_readiness_uses_target_fresh_capture(monkeypatch):
    """A target config schema must not be parsed by the pre-pull updater."""
    from aletheia.update import applier, continuity

    before = _snapshot(capture_source="in_process")
    after = _snapshot(disk_sha="new222", gateway_sha="new222")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(
        continuity,
        "_gateway_status",
        lambda **_: (_ for _ in ()).throw(AssertionError("pre-pull gateway loader must not be used")),
    )
    fresh_calls = []

    def fresh_capture(**kwargs):
        fresh_calls.append(kwargs)
        return after

    monkeypatch.setattr(continuity, "capture_update_continuity_fresh", fresh_capture)
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["continuity_verified"] is True
    assert fresh_calls, "postflight readiness must use target-fresh capture"


def test_wrapper_rolls_back_postflight_failure(monkeypatch):
    from aletheia.update import applier, continuity

    monkeypatch.setattr(
        continuity,
        "capture_update_continuity",
        lambda: _snapshot(worker_sha="worker111"),
    )
    after = _snapshot(
        worker_sha="worker222",
        disk_sha="new222",
        gateway_sha="new222",
    )
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: after["gateway"])
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_fresh",
        lambda **_: after,
    )
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        applier,
        "rollback_applied_update",
        lambda result: {"rolled_back": True, "actual_head": "old111"},
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert result["rolled_back"] is True
    assert result["continuity_verified"] is False
    assert result["continuity_state"] == "failed"
    assert result["reason"] == "observer_continuity_failed"


def test_wrapper_reanchors_checkpoint_after_apply(monkeypatch):
    """After apply_update succeeds, the observation checkpoint is immediately
    re-anchored so the next preflight doesn't see stale migration state."""
    from aletheia.update import applier, continuity

    before = _snapshot()
    after = _snapshot(disk_sha="new222", gateway_sha="new222")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: after["gateway"])
    monkeypatch.setattr(continuity, "capture_update_continuity_fresh", lambda **_: after)
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    anchored = []
    monkeypatch.setattr(
        "aletheia.update.target_fresh.reanchor_joint_checkpoint",
        lambda **_: anchored.append("target-fresh") or {"ok": True},
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is True
    assert anchored == ["target-fresh"], "checkpoint must be re-anchored after successful apply"


def test_wrapper_does_not_reanchor_when_apply_fails(monkeypatch):
    """Checkpoint re-anchor is skipped when apply_update returns applied=False."""
    from aletheia.update import applier, continuity

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: _snapshot())
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {"applied": False, "reason": "git_pull_failed"},
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    anchored = []
    monkeypatch.setattr(
        "aletheia.update.target_fresh.reanchor_joint_checkpoint",
        lambda **_: anchored.append("target-fresh") or {"ok": True},
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is False
    assert anchored == [], "checkpoint must not be re-anchored when apply fails"


def test_wrapper_records_checkpoint_reanchor_failure(monkeypatch):
    from aletheia.update import applier, continuity

    before = _snapshot()
    after = _snapshot(disk_sha="new222", gateway_sha="new222")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(continuity, "_gateway_status", lambda **_: after["gateway"])
    monkeypatch.setattr(continuity, "capture_update_continuity_fresh", lambda **_: after)
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    audits = []
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: audits.append((args, kwargs)))
    monkeypatch.setattr(
        "aletheia.update.target_fresh.reanchor_joint_checkpoint",
        lambda **_: {"ok": False, "reason": "target_fresh_process_failed"},
    )

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is True
    assert "observation_checkpoint_reanchor_failed" in result["continuity"]["comparison"]["warnings"]
    assert any(args[0] == "update_observer_checkpoint_reanchor_failed" for args, _kwargs in audits)
