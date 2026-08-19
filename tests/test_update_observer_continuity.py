"""Focused tests for Issue #1135 Observer-aware source updates."""

from __future__ import annotations


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
    checkpoint_ok: bool = True,
    runtime_owner: str | None = None,
    runtime_proof_available: bool = True,
    main_cycle_registered: bool = False,
):
    generations = generations or {"obs-a": 2}
    runtime_owner = runtime_owner or owner
    return {
        "schema_version": "update_observer_continuity/v1",
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
            "observation_execution": {
                "available": runtime_proof_available,
                "owner": runtime_owner,
                "job_id": "filter_controller_observe",
                "main_cycle_registered": (
                    main_cycle_registered if runtime_proof_available else None
                ),
                "exclusive": (
                    not (runtime_owner == "worker" and main_cycle_registered)
                    if runtime_proof_available
                    else None
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
                "available": True,
                "ids": list(observation_ids),
                "total": len(observation_ids),
            },
            "manifest_generations": generations,
            "checkpoint": {
                "verified": {"ok": checkpoint_ok, "reason": "ok" if checkpoint_ok else "drift"}
            },
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

    before = _snapshot()
    after = _snapshot(
        disk_sha="new222",
        gateway_sha="new222",
        observation_ids=("obs-a", "obs-b", "obs-c"),
        generations={"obs-a": 3, "obs-c": 1},
    )
    after["observation"]["experiments"]["collecting"] = 1
    after["observation"]["experiments"]["ready_for_review"] = 2

    result = compare_update_continuity(
        before, after, expected_target="new222", restart=True
    )

    assert result["ok"] is True
    assert result["failures"] == []
    assert result["worker_sha_before"] == "worker999"
    assert result["worker_sha_after"] == "worker999"
    assert result["data_root_unchanged"] is True
    assert result["main_observation_cycle_registered"] is False
    assert result["observation_execution_exclusive"] is True


def test_compare_detects_owner_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(owner="worker"),
        _snapshot(owner="main", disk_sha="new222", gateway_sha="new222"),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "observation_owner_changed" in result["failures"]


def test_compare_detects_worker_identity_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(worker_sha="worker111"),
        _snapshot(
            worker_sha="worker222", disk_sha="new222", gateway_sha="new222"
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "worker_identity_changed" in result["failures"]


def test_compare_detects_data_root_change():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(data_root="sha256:before"),
        _snapshot(
            data_root="sha256:after", disk_sha="new222", gateway_sha="new222"
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "data_root_changed" in result["failures"]


def test_compare_detects_observation_history_loss():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(observation_ids=("obs-a", "obs-b")),
        _snapshot(
            observation_ids=("obs-b",), disk_sha="new222", gateway_sha="new222"
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "observation_history_missing" in result["failures"]


def test_compare_detects_manifest_generation_regression():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(generations={"obs-a": 4}),
        _snapshot(
            generations={"obs-a": 3}, disk_sha="new222", gateway_sha="new222"
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "manifest_generation_regressed:obs-a" in result["failures"]


def test_preflight_fails_closed_on_structural_checkpoint_drift():
    from aletheia.update.continuity import evaluate_preflight

    result = evaluate_preflight(_snapshot(checkpoint_ok=False))

    assert result["ok"] is False
    assert result["failures"] == ["observation_checkpoint_drift"]


def test_preflight_detects_duplicate_worker_and_main_cycle_owner():
    from aletheia.update.continuity import evaluate_preflight

    result = evaluate_preflight(
        _snapshot(owner="worker", main_cycle_registered=True)
    )

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


def test_compare_detects_duplicate_observation_cycle_after_update():
    from aletheia.update.continuity import compare_update_continuity

    result = compare_update_continuity(
        _snapshot(),
        _snapshot(
            disk_sha="new222",
            gateway_sha="new222",
            main_cycle_registered=True,
        ),
        expected_target="new222",
        restart=True,
    )

    assert result["ok"] is False
    assert "duplicate_observation_cycle_owner" in result["failures"]


def test_compare_requires_runtime_owner_proof_for_restarted_worker_mode():
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

    assert result["ok"] is False
    assert "observation_runtime_owner_proof_unavailable" in result["failures"]


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

    assert result["ok"] is False
    assert "observation_runtime_owner_mismatch" in result["failures"]


def test_wrapper_preserves_existing_applier_and_adds_postflight(monkeypatch):
    from aletheia.update import applier, continuity

    snapshots = iter(
        [
            _snapshot(),
            _snapshot(disk_sha="new222", gateway_sha="new222"),
        ]
    )
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: next(snapshots))
    seen = {}

    def apply_update(*, restart):
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
    assert result["continuity_verified"] is True
    assert result["continuity"]["comparison"]["ok"] is True


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


def test_wrapper_reports_postflight_failure_without_claiming_rollback(monkeypatch):
    from aletheia.update import applier, continuity

    snapshots = iter(
        [
            _snapshot(worker_sha="worker111"),
            _snapshot(
                worker_sha="worker222", disk_sha="new222", gateway_sha="new222"
            ),
        ]
    )
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: next(snapshots))
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart: {
            "applied": True,
            "mode": "development",
            "old_commit": "old111",
            "new_commit": "new222",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["applied"] is True
    assert result["continuity_verified"] is False
    assert result["reason"] == "observer_continuity_failed"
    assert "worker_identity_changed" in result["continuity"]["comparison"]["failures"]
