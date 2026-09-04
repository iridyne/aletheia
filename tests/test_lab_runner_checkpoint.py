"""Runner checkpoint wiring (G3) tests.

Cluster 5 built the ``verify_joint_checkpoint`` primitive; this file
proves the runner actually invokes it every pass, records the
fingerprint chain, and stays failure-isolated when the checkpoint step
itself raises.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_registry():
    from aletheia.observation_lab.runner import _WINDOWS, registered_windows

    snapshot = list(registered_windows())
    _WINDOWS.clear()
    yield
    _WINDOWS.clear()
    _WINDOWS.extend(snapshot)


def test_runner_saves_and_reports_joint_checkpoint(clean_registry):
    from aletheia.observation_lab.checkpoint import latest_joint_checkpoint
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    register_window(LabWindow(name="noop", kind="test", fn=lambda: {"status": "ok"}))
    summary = run_lab_windows()

    assert "joint_checkpoint" in summary
    cp = summary["joint_checkpoint"]
    assert cp["ok"] is True
    # First pass on a fresh ledger reports first_checkpoint, then saves it.
    assert cp["reason"] in {"first_checkpoint", "fingerprint_match"}
    assert cp["current_fingerprint"] is not None

    stored = latest_joint_checkpoint()
    assert stored is not None
    assert stored.joint_fingerprint == cp["current_fingerprint"]


def test_runner_reports_mismatch_on_manifest_change(clean_registry):
    """Publishing a new active manifest between passes surfaces as ``ok=False``."""
    from aletheia.observation_lab.manifest import build_manifest, publish_manifest
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    register_window(LabWindow(name="noop", kind="test", fn=lambda: {"status": "ok"}))

    # First pass — baseline checkpoint recorded.
    first = run_lab_windows()
    assert first["joint_checkpoint"]["ok"] is True

    # Publish a manifest between passes, but do NOT rerun the runner yet.
    manifest = build_manifest(
        observation_id="drift-check",
        generation=1,
        spec_hash="s",
        treatment_adapter_id="t",
        treatment_adapter_version="v1",
        assignment_policy="stable",
        assignment_salt="salt",
        assignment_unit_key_template="{k}",
        allocation_pct=0.5,
        control_payload={"m": 1.0},
        treatment_payload={"m": 1.1},
        eligible_population={"path_id": "test"},
        behavior_contract_hash="bh",
        interference_key="ifk",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"m": 1.0},
        unavailable_fallback="baseline",
    )
    publish_manifest(manifest)

    # Second pass sees the drift versus the last stored fingerprint.
    second = run_lab_windows()
    assert second["joint_checkpoint"]["ok"] is False
    assert "fingerprint_mismatch" in second["joint_checkpoint"]["reason"]

    # An unresolved mismatch must not be promoted to a new trusted baseline.
    third = run_lab_windows()
    assert third["joint_checkpoint"]["ok"] is False
    assert third["joint_checkpoint"]["save_skipped"] == "unresolved_checkpoint_mismatch"


def test_runner_isolates_checkpoint_error_from_window_execution(clean_registry):
    """A raise inside the checkpoint step must not stop windows from running."""
    from unittest.mock import patch

    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    ran = {"count": 0}

    def _fn():
        ran["count"] += 1
        return {"status": "ok"}

    register_window(LabWindow(name="alpha", kind="test", fn=_fn))

    with patch(
        "aletheia.observation_lab.checkpoint.verify_joint_checkpoint",
        side_effect=RuntimeError("simulated checkpoint failure"),
    ):
        summary = run_lab_windows()

    # Window still ran; checkpoint step reported the error.
    assert ran["count"] == 1
    assert summary["windows_run"] == 1
    assert summary["joint_checkpoint"]["ok"] is False
    assert "checkpoint_error" in summary["joint_checkpoint"]["reason"]


def test_runner_checkpoints_expected_append_only_window_writes(clean_registry):
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    counter = {"n": 0}

    def _append_evidence():
        counter["n"] += 1
        get_lab_db().execute(
            "INSERT INTO ufc_shadow_equivalence ("
            "id, observation_id, experiment_id, cohort_key, checked_at, matched"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"append-{counter['n']}", "obs", "exp", "cohort",
                f"2026-09-02T00:00:0{counter['n']}+00:00", 1,
            ),
        )
        return {"status": "ok"}

    register_window(LabWindow(name="append", kind="test", fn=_append_evidence))

    first = run_lab_windows()
    second = run_lab_windows()

    assert first["joint_checkpoint"]["ok"] is True
    assert second["joint_checkpoint"]["ok"] is True


def test_runner_imports_receipts_appended_after_previous_checkpoint(clean_registry):
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        import_delivery_receipts,
    )
    from aletheia.observation_lab.runner import LabWindow, register_window, run_lab_windows

    run_lab_windows()
    append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-runner-receipt",
        candidate_key="candidate-runner",
        resolution_reason="adapter_unavailable",
    )
    register_window(LabWindow(name="delivery", kind="delivery", fn=import_delivery_receipts))

    imported = run_lab_windows()
    settled = run_lab_windows()

    assert imported["joint_checkpoint"]["reason"] == "main_outbox_append_only"
    assert imported["results"]["delivery"]["processed"] == 1
    assert settled["joint_checkpoint"]["reason"] == "fingerprint_match"


def test_main_bridge_checkpoints_imported_receipts_and_refuses_existing_drift():
    from aletheia.agent_runtime import scheduler
    from aletheia.observation_lab.canonical import append_delivery_receipt
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    save_joint_checkpoint(compute_joint_checkpoint())
    first_receipt = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-main-bridge",
        candidate_key="candidate-main-bridge",
        resolution_reason="adapter_unavailable",
    )

    scheduler._run_observation_lab_bridge()

    assert verify_joint_checkpoint().reason == "fingerprint_match"
    assert get_db().execute(
        "SELECT acknowledged_at FROM observation_delivery_outbox WHERE receipt_id=?",
        (first_receipt,),
    ).fetchone()[0] is not None

    get_lab_db().execute(
        "INSERT INTO ufc_shadow_equivalence "
        "(id, observation_id, experiment_id, cohort_key, checked_at, matched) "
        "VALUES ('unexpected-drift', 'obs', 'exp', 'cohort', "
        "'2026-09-03T00:00:00+00:00', 1)"
    )
    blocked_receipt = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-main-bridge-blocked",
        candidate_key="candidate-main-bridge-blocked",
        resolution_reason="adapter_unavailable",
    )

    scheduler._run_observation_lab_bridge()

    assert verify_joint_checkpoint().ok is False
    assert get_db().execute(
        "SELECT acknowledged_at FROM observation_delivery_outbox WHERE receipt_id=?",
        (blocked_receipt,),
    ).fetchone()[0] is None


def test_settlement_drains_batches_and_refuses_poison(monkeypatch):
    from aletheia.observation_lab import canonical, checkpoint, runner

    batches = iter((
        {"available": True, "processed": 2, "failed_sequence": None, "retry_sequences": []},
        {"available": True, "processed": 1, "failed_sequence": None, "retry_sequences": []},
    ))
    monkeypatch.setattr(runner, "_pass_checkpoint", lambda: {"ok": True})
    monkeypatch.setattr(runner, "_save_checkpoint", lambda summary: summary.update(saved=True))
    monkeypatch.setattr(canonical, "import_delivery_receipts", lambda **_: next(batches))
    monkeypatch.setattr(canonical, "mirror_lab_audit_outbox", lambda **_: {"available": True, "failed": [], "processed": 0})
    monkeypatch.setattr(canonical, "prune_observation_delivery_outbox", lambda: {"available": True})
    monkeypatch.setattr(canonical, "compact_finalized_generations", lambda **_: {"skipped": 0})
    monkeypatch.setattr(checkpoint, "verify_joint_checkpoint", lambda: type("V", (), {"ok": True, "reason": "fingerprint_match"})())
    settled = runner.settle_observation_continuity(batch_size=2)
    assert settled["ok"] is True
    assert settled["delivery_import"]["processed"] == 3

    monkeypatch.setattr(canonical, "import_delivery_receipts", lambda **_: {
        "available": True, "processed": 0, "failed_sequence": 6144,
        "failed_reason": "unclassified_receipt_identity", "retry_sequences": [6144],
    })
    poisoned = runner.settle_observation_continuity()
    assert poisoned["ok"] is False
    assert poisoned["reason"] == "unclassified_receipt_identity"


def test_checkpoint_save_refuses_unavailable_outbox(monkeypatch):
    from aletheia.observation_lab import checkpoint, runner

    current = checkpoint.JointCheckpoint(
        checkpoint_id="bad", recorded_at="2026-09-04T00:00:00+00:00",
        app_migration_head="head", app_migration_count=1,
        active_manifest_hashes=(), active_manifest_count=0,
        lab_schema_head="head", lab_schema_count=1,
        lab_state_fingerprint="lab", checkpoint_ledger_fingerprint="ledger",
        main_outbox_high_watermark=-1, main_outbox_pending_count=-1,
        main_outbox_fingerprint="<unavailable>", joint_fingerprint="bad",
    )
    saved = []
    monkeypatch.setattr(checkpoint, "compute_joint_checkpoint", lambda: current)
    monkeypatch.setattr(checkpoint, "save_joint_checkpoint", saved.append)
    summary = {"ok": True}
    runner._save_checkpoint(summary)
    assert summary == {"ok": False, "reason": "main_outbox_unavailable"}
    assert saved == []


def test_settlement_reports_cycle_lock_contention_without_mutation(monkeypatch):
    from contextlib import contextmanager

    from aletheia.observation_lab import canonical, runner, worker_lock

    @contextmanager
    def contended():
        raise worker_lock.WorkerCycleLockError("busy")
        yield

    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", contended)
    monkeypatch.setattr(
        canonical,
        "import_delivery_receipts",
        lambda **_: pytest.fail("contention must not enter the importer"),
    )

    assert runner.settle_observation_continuity() == {
        "ok": False,
        "reason": "cycle_lock_contended",
    }
