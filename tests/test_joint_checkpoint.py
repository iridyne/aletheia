"""Joint app.db + lab.db + manifest-set checkpoint tests (#1072 Cluster 5).

Contract lives in ``docs/design/contracts/observation_lab_backup_contract.md``.
These tests pin the "fail closed on inconsistent restore" invariant.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_first_pass_records_and_verifies_ok():
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        latest_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    # Fresh ledger — nothing stored.
    assert latest_joint_checkpoint() is None

    verdict = verify_joint_checkpoint()
    assert verdict.ok is True
    assert verdict.reason == "first_checkpoint"
    assert verdict.last is None

    checkpoint = compute_joint_checkpoint()
    save_joint_checkpoint(checkpoint)

    stored = latest_joint_checkpoint()
    assert stored is not None
    assert stored.joint_fingerprint == checkpoint.joint_fingerprint


def test_second_pass_with_no_state_change_verifies_match():
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())
    verdict = verify_joint_checkpoint()
    assert verdict.ok is True
    assert verdict.reason == "fingerprint_match"


def test_appended_main_receipt_requires_explicit_progress_mode():
    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())
    append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-new-receipt",
        candidate_key="candidate-checkpoint",
        resolution_reason="adapter_unavailable",
    )

    strict = verify_joint_checkpoint()
    verdict = verify_joint_checkpoint(allow_main_outbox_progress=True)

    assert strict.ok is False
    assert "fingerprint_mismatch" in strict.reason
    assert verdict.ok is True
    assert verdict.reason == "main_outbox_append_only"


def test_appended_main_receipt_does_not_hide_checkpointed_prefix_change():
    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-existing-receipt",
        candidate_key="candidate-existing",
        resolution_reason="adapter_unavailable",
    )
    save_joint_checkpoint(compute_joint_checkpoint())
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2026-09-03T00:00:00+00:00", receipt_id),
    )
    append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-later-receipt",
        candidate_key="candidate-later",
        resolution_reason="adapter_unavailable",
    )

    verdict = verify_joint_checkpoint(allow_main_outbox_progress=True)

    assert verdict.ok is False
    assert "fingerprint_mismatch" in verdict.reason


def test_pruning_acknowledged_receipts_keeps_exact_checkpoint_match():
    from extensions.observation_lab.canonical import (
        append_delivery_receipt,
        prune_observation_delivery_outbox,
    )
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-prunable-receipt",
        candidate_key="candidate-prunable",
        resolution_reason="adapter_unavailable",
    )
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2020-01-01T00:00:00+00:00", receipt_id),
    )
    save_joint_checkpoint(compute_joint_checkpoint())

    pruned = prune_observation_delivery_outbox(
        keep_acknowledged=0,
        safety_grace_seconds=0,
    )
    verdict = verify_joint_checkpoint()

    assert pruned["deleted"] == 1
    assert verdict.ok is True
    assert verdict.reason == "fingerprint_match"


def test_pending_receipt_identity_change_is_structured_outbox_drift():
    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-pending-identity",
        candidate_key="candidate-pending-identity",
        resolution_reason="adapter_unavailable",
    )
    save_joint_checkpoint(compute_joint_checkpoint())
    db = get_db()
    db.execute("DROP TRIGGER trg_observation_delivery_outbox_identity_immutable")
    db.execute(
        "UPDATE observation_delivery_outbox SET manifest_hash='changed' WHERE receipt_id=?",
        (receipt_id,),
    )

    verdict = verify_joint_checkpoint()

    assert verdict.ok is False
    assert verdict.mismatch_components == ("main_outbox",)


def test_outbox_frontier_uses_one_sqlite_snapshot(monkeypatch, tmp_path):
    import sqlite3

    from extensions.observation_lab.checkpoint import _read_app_outbox_state
    from aletheia.storage import sqlite as sqlite_store

    path = tmp_path / "snapshot.db"
    reader = sqlite3.connect(path, isolation_level=None)
    reader.row_factory = sqlite3.Row
    reader.execute("PRAGMA journal_mode=WAL")
    reader.execute(
        "CREATE TABLE observation_delivery_outbox ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT, "
        "manifest_hash TEXT, acknowledged_at TEXT)"
    )
    writer = sqlite3.connect(path, isolation_level=None)
    inserted = False

    def insert_between_frontier_reads(sql):
        nonlocal inserted
        if not inserted and sql.startswith("SELECT seq FROM sqlite_sequence"):
            inserted = True
            writer.execute(
                "INSERT INTO observation_delivery_outbox "
                "(receipt_id, manifest_hash) VALUES ('late', 'm')"
            )

    reader.set_trace_callback(insert_between_frontier_reads)
    monkeypatch.setattr(sqlite_store, "get_db", lambda: reader)

    first = _read_app_outbox_state()
    second = _read_app_outbox_state()

    assert first[:2] == (0, 0)
    assert second[:2] == (1, 1)
    writer.close()
    reader.close()


def test_outbox_frontier_rejects_incomplete_identity_schema(monkeypatch, tmp_path):
    import sqlite3

    from extensions.observation_lab.checkpoint import _read_app_outbox_state
    from aletheia.storage import sqlite as sqlite_store

    db = sqlite3.connect(tmp_path / "incomplete.db", isolation_level=None)
    db.execute(
        "CREATE TABLE observation_delivery_outbox ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT, "
        "acknowledged_at TEXT)"
    )
    monkeypatch.setattr(sqlite_store, "get_db", lambda: db)

    assert _read_app_outbox_state() == (-1, -1, "<unavailable>")
    db.close()


def test_runtime_verification_accepts_exact_legacy_outbox_fingerprint():
    from dataclasses import replace

    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        _fingerprint,
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-legacy-runtime",
        candidate_key="candidate-legacy-runtime",
        resolution_reason="adapter_unavailable",
    )
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2020-01-01T00:00:00+00:00", receipt_id),
    )
    current = compute_joint_checkpoint()
    assert current.legacy_main_outbox_state is not None
    watermark, pending, fingerprint = current.legacy_main_outbox_state
    legacy_joint_fingerprint = _fingerprint(
        app_migration_head=current.app_migration_head,
        app_migration_count=current.app_migration_count,
        active_manifest_hashes=current.active_manifest_hashes,
        lab_schema_head=current.lab_schema_head,
        lab_schema_count=current.lab_schema_count,
        lab_state_fingerprint=current.lab_state_fingerprint,
        checkpoint_ledger_fingerprint=current.checkpoint_ledger_fingerprint,
        main_outbox_high_watermark=watermark,
        main_outbox_pending_count=pending,
        main_outbox_fingerprint=fingerprint,
    )
    save_joint_checkpoint(replace(
        current,
        main_outbox_high_watermark=watermark,
        main_outbox_pending_count=pending,
        main_outbox_fingerprint=fingerprint,
        joint_fingerprint=legacy_joint_fingerprint,
    ))

    verdict = verify_joint_checkpoint()

    assert verdict.ok is True
    assert verdict.reason == "legacy_main_outbox_fingerprint_match"


def test_acknowledged_main_receipt_suffix_is_not_append_only_progress():
    from extensions.observation_lab.canonical import append_delivery_receipt
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.storage.sqlite import get_db

    save_joint_checkpoint(compute_joint_checkpoint())
    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="checkpoint-acknowledged-suffix",
        candidate_key="candidate-acknowledged-suffix",
        resolution_reason="adapter_unavailable",
    )
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2026-09-03T00:00:00+00:00", receipt_id),
    )

    verdict = verify_joint_checkpoint(allow_main_outbox_progress=True)

    assert verdict.ok is False
    assert "fingerprint_mismatch" in verdict.reason


def test_manifest_set_change_causes_mismatch_verdict():
    """Publishing a new active manifest after a checkpoint = failed proof."""
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from extensions.observation_lab.manifest import build_manifest, publish_manifest

    # Record baseline (empty manifest set).
    save_joint_checkpoint(compute_joint_checkpoint())

    # Publish a new active manifest — changes the joint state.
    manifest = build_manifest(
        observation_id="drift-obs",
        generation=1,
        spec_hash="drift-spec",
        treatment_adapter_id="test",
        treatment_adapter_version="v1",
        assignment_policy="stable",
        assignment_salt="drift",
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

    verdict = verify_joint_checkpoint()
    assert verdict.ok is False
    assert "fingerprint_mismatch" in verdict.reason
    assert verdict.last is not None
    assert verdict.current_fingerprint != verdict.last.joint_fingerprint


def test_lab_evidence_change_causes_mismatch_verdict():
    """Lab-only evidence restore/divergence cannot keep the same checkpoint."""
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from extensions.observation_lab.db import get_lab_db

    save_joint_checkpoint(compute_joint_checkpoint())
    get_lab_db().execute(
        "INSERT INTO ufc_shadow_equivalence ("
        "id, observation_id, experiment_id, cohort_key, checked_at, matched, "
        "mismatch_field, learning_verdict, recomputed_verdict, notes"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "lab-drift-1", "obs", "exp", "cohort", "2026-09-02T00:00:00+00:00",
            0, "verdict", "positive", "negative", "restore-divergence",
        ),
    )

    verdict = verify_joint_checkpoint()

    assert verdict.ok is False
    assert "lab_state" in verdict.reason
    assert verdict.current_fingerprint != verdict.last.joint_fingerprint


def test_lab_checkpoint_state_is_stable_after_saving_quiet_pass():
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )

    first = compute_joint_checkpoint()
    save_joint_checkpoint(first)
    second = compute_joint_checkpoint()
    save_joint_checkpoint(second)
    third = compute_joint_checkpoint()

    assert second.joint_fingerprint == third.joint_fingerprint
    assert second.lab_state_fingerprint == third.lab_state_fingerprint


def test_app_migration_head_change_causes_mismatch_verdict():
    """A restore that rolls app.db to a different migration head must be seen."""
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())

    # Simulate an app.db restore by monkeypatching the migration reader.
    with patch(
        "extensions.observation_lab.checkpoint._read_app_migration_state",
        return_value=("<restored-different-head>", 999),
    ):
        verdict = verify_joint_checkpoint()

    assert verdict.ok is False
    assert "fingerprint_mismatch" in verdict.reason
    # The reason must name what drifted so the operator can act on it.
    assert "app_head" in verdict.reason
    assert "count" in verdict.reason


def test_save_is_idempotent_on_unchanged_fingerprint():
    from extensions.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )
    from extensions.observation_lab.db import get_lab_db

    save_joint_checkpoint(compute_joint_checkpoint())
    save_joint_checkpoint(compute_joint_checkpoint())
    save_joint_checkpoint(compute_joint_checkpoint())

    row = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM joint_checkpoints"
    ).fetchone()
    # Three save calls, one fingerprint → one stored row.
    assert row["n"] == 1


def test_execution_manifests_are_immutable():
    import sqlite3

    from extensions.observation_lab.manifest import build_manifest, publish_manifest
    from extensions.observation_lab.db import get_lab_db

    manifest = build_manifest(
        observation_id="immutable-manifest",
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
    db = get_lab_db()
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(
            "UPDATE execution_manifests SET treatment_payload_json=? WHERE manifest_id=?",
            ("{}", manifest.manifest_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute(
            "DELETE FROM execution_manifests WHERE manifest_id=?",
            (manifest.manifest_id,),
        )


def test_joint_checkpoint_ledger_is_append_only():
    import sqlite3

    from extensions.observation_lab.checkpoint import compute_joint_checkpoint, save_joint_checkpoint
    from extensions.observation_lab.db import get_lab_db

    save_joint_checkpoint(compute_joint_checkpoint())
    checkpoint_id = get_lab_db().execute(
        "SELECT checkpoint_id FROM joint_checkpoints LIMIT 1"
    ).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        get_lab_db().execute(
            "UPDATE joint_checkpoints SET notes=? WHERE checkpoint_id=?",
            ("tampered", checkpoint_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        get_lab_db().execute(
            "DELETE FROM joint_checkpoints WHERE checkpoint_id=?",
            (checkpoint_id,),
        )


def test_existing_manifest_table_is_migrated_before_schema_head_is_recorded():
    import sqlite3

    from extensions.observation_lab import db as lab_db

    connection = sqlite3.connect(":memory:")
    legacy_sql = lab_db._EXECUTION_MANIFEST_TABLE_SQL.replace(
        " CHECK (julianday(effective_from) IS NOT NULL)", ""
    ).replace(
        " CHECK (effective_until IS NULL OR julianday(effective_until) IS NOT NULL)", ""
    )
    connection.executescript(legacy_sql)
    # Opening a real pre-constraint Lab first materializes the current script,
    # including indexes/triggers attached to the legacy table.
    connection.executescript(lab_db._LAB_SCHEMA_SQL)
    lab_db._migrate_execution_manifest_constraints(connection)
    table_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='execution_manifests'"
    ).fetchone()[0]
    assert "julianday(effective_from)" in table_sql
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_execution_manifests_no_update'"
    ).fetchone()


def test_fingerprint_is_deterministic_across_recomputes():
    from extensions.observation_lab.checkpoint import compute_joint_checkpoint

    a = compute_joint_checkpoint()
    b = compute_joint_checkpoint()
    # Same state, different call — checkpoint_id and recorded_at differ but
    # the fingerprint must not.
    assert a.joint_fingerprint == b.joint_fingerprint
    assert a.checkpoint_id != b.checkpoint_id
