"""Follow-up regressions for the deeper #1135 continuity boundaries."""

from __future__ import annotations


def _minimal_snapshot(*, checkpoint_reason: str = "fingerprint_match") -> dict:
    checkpoint_ok = checkpoint_reason == "fingerprint_match"
    return {
        "control": {
            "available": True,
            "disk_sha": "old-sha",
            "data_root": {"available": True, "identity": "root-1"},
        },
        "gateway": {
            "available": True,
            "boot_commit": "old-sha",
            "current_commit": "old-sha",
            "health_status": "healthy",
            "health_issues": [],
            "observation_execution": {
                "available": True,
                "owner": "worker",
                "exclusive": True,
                "main_cycle_registered": False,
            },
        },
        "observation": {
            "owner": "worker",
            "overview_available": True,
            "worker": {
                "available": True,
                "source_commit": "worker-sha",
                "acquired_at": "2026-08-19T00:00:00+00:00",
            },
            "identities": {"available": True, "ids": ["obs-1"]},
            "manifest_generations": {"obs-1": 2},
            "checkpoint": {
                "verified": {"ok": checkpoint_ok, "reason": checkpoint_reason}
            },
        },
    }


def test_preflight_allows_manifest_lifecycle_checkpoint_drift():
    from aletheia.update.continuity import evaluate_preflight

    snapshot = _minimal_snapshot(
        checkpoint_reason=(
            "fingerprint_mismatch: app_head(v35→v35) count(35→35) manifests(2→1)"
        )
    )

    result = evaluate_preflight(snapshot)

    assert result["ok"] is True
    assert result["failures"] == []
    assert "observation_checkpoint_changed" in result["warnings"]


def test_preflight_still_fails_closed_on_app_migration_checkpoint_drift():
    from aletheia.update.continuity import evaluate_preflight

    snapshot = _minimal_snapshot(
        checkpoint_reason=(
            "fingerprint_mismatch: app_head(v34→v35) count(34→35) manifests(2→2)"
        )
    )

    result = evaluate_preflight(snapshot)

    assert result["ok"] is False
    assert "observation_checkpoint_app_migration_drift" in result["failures"]


def test_worker_runtime_owner_proof_unavailable_is_preflight_proof_gap():
    from aletheia.update.continuity import evaluate_preflight

    snapshot = _minimal_snapshot()
    snapshot["gateway"]["observation_execution"] = {"available": False}

    result = evaluate_preflight(snapshot)

    assert result["ok"] is True
    assert "observation_runtime_owner_proof_unavailable" in result["proof_gaps"]


def test_postflight_wait_survives_initializing_then_healthy(monkeypatch):
    from aletheia.update import continuity

    initializing = _minimal_snapshot()
    initializing["control"]["disk_sha"] = "target-sha"
    initializing["gateway"].update(
        boot_commit="target-sha",
        current_commit="target-sha",
        health_status="initializing",
    )
    healthy = _minimal_snapshot()
    healthy["control"]["disk_sha"] = "target-sha"
    healthy["gateway"].update(
        boot_commit="target-sha",
        current_commit="target-sha",
        health_status="healthy",
    )
    snapshots = iter([initializing, healthy])
    sleeps: list[float] = []
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_fresh",
        lambda: next(snapshots),
    )
    monkeypatch.setattr(continuity.time, "sleep", lambda value: sleeps.append(value))

    result = continuity.capture_update_continuity_until_settled(
        "target-sha", timeout_sec=4, poll_sec=2
    )

    assert result["gateway"]["health_status"] == "healthy"
    assert sleeps == [2.0]


def test_preflight_proof_gap_cannot_be_erased_by_healthy_postflight(monkeypatch):
    from aletheia.update import applier, continuity

    before = _minimal_snapshot()
    after = _minimal_snapshot()
    after["control"]["disk_sha"] = "target-sha"
    after["gateway"].update(
        boot_commit="target-sha",
        current_commit="target-sha",
        health_status="healthy",
    )

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(
        continuity,
        "evaluate_preflight",
        lambda snapshot: {
            "ok": True,
            "failures": [],
            "warnings": [],
            "proof_gaps": ["worker_identity_baseline_unavailable"],
        },
    )
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_until_settled",
        lambda target: after,
    )
    monkeypatch.setattr(
        applier,
        "apply_update",
        lambda *, restart, progress=None: {
            "applied": True,
            "mode": "development",
            "old_commit": "old-sha",
            "new_commit": "target-sha",
            "deps_reinstalled": False,
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *args, **kwargs: None)

    result = continuity.apply_update_with_continuity(restart=True)

    assert result["continuity_verified"] is False
    assert result["continuity_state"] == "unproven"
    assert "worker_identity_baseline_unavailable" in result["continuity"]["comparison"]["proof_gaps"]


def test_auto_worker_preflight_requires_complete_evidence(monkeypatch):
    from aletheia.observation_lab import execution_owner
    from aletheia.update import auto, continuity

    before = _minimal_snapshot()
    monkeypatch.setattr(execution_owner, "get_execution_owner", lambda: "worker")
    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(
        continuity,
        "evaluate_preflight",
        lambda snapshot: {
            "ok": True,
            "failures": [],
            "warnings": [],
            "proof_gaps": ["data_root_baseline_unavailable"],
        },
    )

    result = auto._observer_auto_preflight()

    assert result["required"] is True
    assert result["ok"] is False
    assert "data_root_baseline_unavailable" in result["preflight"]["proof_gaps"]


def test_auto_main_owner_does_not_create_worker_coupling(monkeypatch):
    from aletheia.observation_lab import execution_owner
    from aletheia.update import auto, continuity

    monkeypatch.setattr(execution_owner, "get_execution_owner", lambda: "main")
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity",
        lambda: (_ for _ in ()).throw(AssertionError("must not capture Worker continuity")),
    )

    result = auto._observer_auto_preflight()

    assert result == {
        "required": False,
        "ok": True,
        "before": None,
        "preflight": None,
    }


def test_auto_refuses_cross_time_rollback_when_worker_advanced(monkeypatch):
    from aletheia.update import auto, continuity

    guard = {
        "required": True,
        "before": {
            "observation": {
                "worker": {
                    "available": True,
                    "source_commit": "worker-sha",
                    "acquired_at": "before",
                }
            }
        },
    }
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity",
        lambda: {
            "observation": {
                "worker": {
                    "available": True,
                    "source_commit": "worker-sha",
                    "acquired_at": "after",
                }
            }
        },
    )

    assert auto._observer_rollback_safe(guard) is False


def test_auto_allows_existing_rollback_when_worker_did_not_advance(monkeypatch):
    from aletheia.update import auto, continuity

    guard = {
        "required": True,
        "before": {
            "observation": {
                "worker": {
                    "available": True,
                    "source_commit": "worker-sha",
                    "acquired_at": "same",
                }
            }
        },
    }
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity",
        lambda: {
            "observation": {
                "worker": {
                    "available": True,
                    "source_commit": "worker-sha",
                    "acquired_at": "same",
                }
            }
        },
    )

    assert auto._observer_rollback_safe(guard) is True
