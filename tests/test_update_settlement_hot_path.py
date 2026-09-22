"""Focused proof for Issue #2869's minimal update settlement path."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


def _install_lab_root(tmp_path: Path, monkeypatch) -> None:
    from aletheia.config import loader

    (tmp_path / "observation-lab").mkdir()
    (tmp_path / "observation-lab" / "lab.db").touch()
    monkeypatch.setattr(loader, "get_data_dir", lambda: tmp_path)


def _checkpoint(*, ok: bool, reason: str, fingerprint: str = "trusted"):
    return SimpleNamespace(
        ok=ok,
        reason=reason,
        current_fingerprint=fingerprint,
        mismatch_components=() if ok else ("main_outbox",),
        last=None,
    )


def test_caught_up_update_settlement_is_a_true_read_only_fast_path(
    tmp_path, monkeypatch,
):
    from extensions.observation_lab import canonical, runner, update_settlement

    _install_lab_root(tmp_path, monkeypatch)
    checkpoint = {
        "ok": True,
        "reason": "fingerprint_match",
        "current_fingerprint": "trusted",
    }
    monkeypatch.setattr(runner, "_pass_checkpoint", lambda: checkpoint)
    monkeypatch.setattr(
        update_settlement,
        "_delivery_transport_state",
        lambda: {
            "available": True,
            "reason": "fingerprint_frontier_settled",
            "settled": True,
            "outbox_pending_count": 0,
            "outbox_high_watermark": 42,
            "lab_imported_watermark": 42,
            "retry_sequences": [],
        },
    )
    monkeypatch.setattr(
        canonical,
        "import_delivery_receipts",
        lambda **_: pytest.fail("fast path must not run the importer"),
    )
    monkeypatch.setattr(
        runner,
        "_save_checkpoint",
        lambda *_: pytest.fail("fast path must not rewrite the checkpoint"),
    )

    result = update_settlement.settle_update_continuity_locked()

    assert result["ok"] is True
    assert result["fast_path"] is True
    assert result["delivery_import"]["processed"] == 0
    assert result["checkpoint"] is checkpoint
    assert result["maintenance_deferred"] is True
    assert result["timings_ms"]["total"] >= 0


def test_update_settlement_drains_only_delivery_then_strictly_verifies(
    tmp_path, monkeypatch,
):
    from extensions.observation_lab import canonical, checkpoint, runner, update_settlement

    _install_lab_root(tmp_path, monkeypatch)
    states = iter((
        {
            "available": True,
            "reason": "delivery_import_pending",
            "settled": False,
            "outbox_pending_count": 2,
            "outbox_high_watermark": 12,
            "lab_imported_watermark": 10,
            "retry_sequences": [],
        },
        {
            "available": True,
            "reason": "fingerprint_frontier_settled",
            "settled": True,
            "outbox_pending_count": 0,
            "outbox_high_watermark": 12,
            "lab_imported_watermark": 12,
            "retry_sequences": [],
        },
    ))
    pre = {
        "ok": True,
        "reason": "main_outbox_append_only",
        "current_fingerprint": "before",
    }
    monkeypatch.setattr(runner, "_pass_checkpoint", lambda: pre)
    monkeypatch.setattr(
        update_settlement, "_delivery_transport_state", lambda: next(states)
    )
    monkeypatch.setattr(
        canonical,
        "import_delivery_receipts",
        lambda **_: {
            "available": True,
            "processed": 2,
            "failed_sequence": None,
            "retry_sequences": [],
            "backlog_count": 0,
        },
    )
    monkeypatch.setattr(
        runner,
        "_save_checkpoint",
        lambda summary: summary.update(current_fingerprint="after"),
    )
    monkeypatch.setattr(
        checkpoint,
        "verify_joint_checkpoint",
        lambda: _checkpoint(ok=True, reason="fingerprint_match", fingerprint="after"),
    )
    for name in (
        "mirror_lab_audit_outbox",
        "prune_observation_delivery_outbox",
        "compact_finalized_generations",
    ):
        monkeypatch.setattr(
            canonical,
            name,
            lambda *_, _name=name, **__: pytest.fail(
                f"update settlement must defer {_name}"
            ),
        )

    result = update_settlement.settle_update_continuity_locked()

    assert result["ok"] is True
    assert result["fast_path"] is False
    assert result["delivery_import"]["processed"] == 2
    assert result["checkpoint"]["current_fingerprint"] == "after"
    assert result["maintenance_deferred"] is True
    assert "delivery_import" in result["timings_ms"]
    assert "checkpoint_postverify" in result["timings_ms"]


def test_update_settlement_refuses_retry_before_import(tmp_path, monkeypatch):
    from extensions.observation_lab import canonical, runner, update_settlement

    _install_lab_root(tmp_path, monkeypatch)
    monkeypatch.setattr(
        runner,
        "_pass_checkpoint",
        lambda: {
            "ok": True,
            "reason": "main_outbox_append_only",
            "current_fingerprint": "trusted",
        },
    )
    monkeypatch.setattr(
        update_settlement,
        "_delivery_transport_state",
        lambda: {
            "available": True,
            "reason": "delivery_import_retry_pending",
            "settled": False,
            "outbox_pending_count": 1,
            "outbox_high_watermark": 8,
            "lab_imported_watermark": 7,
            "retry_sequences": [8],
        },
    )
    monkeypatch.setattr(
        canonical,
        "import_delivery_receipts",
        lambda **_: pytest.fail("retry state must fail before mutation"),
    )

    result = update_settlement.settle_update_continuity_locked()

    assert result["ok"] is False
    assert result["reason"] == "delivery_import_retry_pending"


def test_update_barrier_keeps_cycle_lock_around_minimal_cut(monkeypatch):
    from extensions.observation_lab import runner, update_settlement, worker_lock

    events: list[str] = []

    @contextmanager
    def lock():
        events.append("lock:enter")
        try:
            yield
        finally:
            events.append("lock:exit")

    monkeypatch.setattr(worker_lock, "worker_cycle_exclusion", lock)
    monkeypatch.setattr(
        update_settlement,
        "settle_update_continuity_locked",
        lambda: events.append("settle") or {"ok": True},
    )

    with runner.observation_settlement_barrier() as result:
        events.append("activation")
        assert result["ok"] is True

    assert events == ["lock:enter", "settle", "activation", "lock:exit"]


def _stub_capture_dependencies(monkeypatch, verdicts) -> None:
    from aletheia.cli import control_cmd
    from aletheia.config import loader
    from extensions.observation_lab import checkpoint, db as lab_db, execution_owner, manifest
    from aletheia.storage import sqlite as storage_sqlite
    from aletheia.update import git_ops
    from ops.update import continuity, git_ops as git_mutations

    @contextmanager
    def read_only(*_args, **_kwargs):
        yield None

    monkeypatch.setattr(git_ops, "current_commit", lambda: "old-sha")
    monkeypatch.setattr(
        control_cmd,
        "_data_root_identity",
        lambda: {"available": True, "identity": "root"},
    )
    monkeypatch.setattr(loader, "read_only_config", read_only)
    monkeypatch.setattr(execution_owner, "get_execution_owner", lambda: "worker")
    monkeypatch.setattr(storage_sqlite, "readonly_db", read_only)
    monkeypatch.setattr(lab_db, "readonly_lab_db", read_only)
    monkeypatch.setattr(
        continuity,
        "_observation_ids",
        lambda: {"available": True, "ids": [], "total": 0},
    )
    monkeypatch.setattr(manifest, "load_active_manifests", lambda: ())
    monkeypatch.setattr(
        continuity,
        "_worker_signature",
        lambda: {"available": True, "source_commit": "worker-sha"},
    )
    monkeypatch.setattr(
        continuity,
        "_gateway_status",
        lambda: {
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
    )
    monkeypatch.setattr(checkpoint, "verify_joint_checkpoint", verdicts)


def test_preflight_skips_redundant_settleable_checkpoint_when_strict_matches(
    monkeypatch,
):
    from ops.update import continuity

    calls: list[bool] = []

    def verify(*, allow_main_outbox_progress=False):
        calls.append(bool(allow_main_outbox_progress))
        return _checkpoint(ok=True, reason="fingerprint_match")

    _stub_capture_dependencies(monkeypatch, verify)

    snapshot = continuity.capture_update_continuity()

    assert calls == [False]
    checkpoint = snapshot["observation"]["checkpoint"]
    assert checkpoint["verified"]["reason"] == "fingerprint_match"
    assert checkpoint["settleable"]["reason"] == "fingerprint_match"


def test_preflight_computes_settleability_only_after_strict_mismatch(monkeypatch):
    from ops.update import continuity

    calls: list[bool] = []

    def verify(*, allow_main_outbox_progress=False):
        calls.append(bool(allow_main_outbox_progress))
        return (
            _checkpoint(ok=True, reason="main_outbox_append_only")
            if allow_main_outbox_progress
            else _checkpoint(ok=False, reason="fingerprint_mismatch")
        )

    _stub_capture_dependencies(monkeypatch, verify)

    snapshot = continuity.capture_update_continuity()

    assert calls == [False, True]
    checkpoint = snapshot["observation"]["checkpoint"]
    assert checkpoint["verified"]["ok"] is False
    assert checkpoint["settleable"] == {
        "ok": True,
        "reason": "main_outbox_append_only",
    }


def test_update_progress_separates_stop_settlement_and_keeps_lock(
    tmp_path, monkeypatch,
):
    from ops.backup import service as backup_service
    from extensions.observation_lab import runner
    from ops.update import applier, continuity, target_fresh

    old = "old-sha"
    target = "target-sha"
    before = {
        "capture_available": True,
        "control": {
            "available": True,
            "disk_sha": old,
            "data_root": {"available": True, "identity": "root"},
        },
        "gateway": {
            "available": True,
            "port": 8787,
            "boot_commit": old,
            "current_commit": old,
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
            "worker": {"available": True, "source_commit": "worker-sha"},
            "identities": {"available": True, "ids": []},
            "manifest_generations": {},
            "checkpoint": {
                "verified": {"ok": True, "reason": "fingerprint_match"},
                "settleable": {"ok": True, "reason": "fingerprint_match"},
            },
        },
    }
    after = {
        **before,
        "control": {**before["control"], "disk_sha": target},
        "gateway": {
            **before["gateway"],
            "boot_commit": target,
            "current_commit": target,
        },
        "timings_ms": {"gateway_wait": 0, "target_capture": 0},
    }
    events: list[str] = []
    phases: list[str] = []

    @contextmanager
    def barrier():
        events.append("barrier:enter")
        try:
            yield {
                "ok": True,
                "checkpoint": {"current_fingerprint": "settled-cut"},
                "timings_ms": {"total": 1},
            }
        finally:
            events.append("barrier:exit")

    class Backup:
        def __init__(self, *_args, **_kwargs):
            pass

        def create_backup(self, *, label, profile):
            events.append("backup")
            return tmp_path / "backup.tar.gz"

    def apply(*, restart, progress=None, preactivation_backup=""):
        assert restart is True
        assert preactivation_backup == ""
        events.append("apply")
        if progress:
            progress("Applying source update")
        return {
            "applied": True,
            "mode": "development",
            "old_commit": old,
            "new_commit": target,
            "source_changed": True,
            "deps_reinstalled": False,
            "schema_changed": False,
            "frontend_built": False,
            "metadata_persisted": True,
        }

    monkeypatch.setattr(continuity, "capture_update_continuity", lambda: before)
    monkeypatch.setattr(
        continuity,
        "evaluate_preflight",
        lambda _snapshot: {"ok": True, "failures": [], "warnings": [], "proof_gaps": []},
    )
    monkeypatch.setattr(applier, "_stop_gateway", lambda: {"ok": True})
    monkeypatch.setattr(applier, "_restart_gateway", lambda: {"ok": True})
    monkeypatch.setattr(runner, "observation_settlement_barrier", barrier)
    monkeypatch.setattr(backup_service, "BackupService", Backup)
    monkeypatch.setattr(applier, "apply_update", apply)
    monkeypatch.setattr(
        target_fresh,
        "reanchor_joint_checkpoint",
        lambda **_kwargs: events.append("reanchor") or {"ok": True},
    )
    monkeypatch.setattr(
        continuity,
        "capture_update_continuity_until_settled",
        lambda *_args, **_kwargs: after,
    )
    monkeypatch.setattr(
        continuity,
        "compare_update_continuity",
        lambda *_args, **_kwargs: {
            "ok": True,
            "failures": [],
            "proof_gaps": [],
            "warnings": [],
            "owner": "worker",
            "worker_sha_after": "worker-sha",
            "data_root_unchanged": True,
            "main_health_status": "healthy",
        },
    )
    monkeypatch.setattr(continuity, "_record", lambda *_args, **_kwargs: None)

    result = continuity.apply_update_with_continuity(
        restart=True,
        progress=phases.append,
    )

    assert result["continuity_verified"] is True
    assert phases[:4] == [
        "Running preflight checks",
        "Stopping runtime",
        "Settling Observation data",
        "Applying source update",
    ]
    assert phases[-1] == "Verifying continuity"
    assert "Creating recovery backup" not in phases
    assert events == [
        "barrier:enter",
        "apply",
        "reanchor",
        "barrier:exit",
    ]
    assert "backup" not in events
