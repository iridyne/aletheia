"""Joint app.db + lab.db + manifest-set checkpoint tests (#1072 Cluster 5).

Contract lives in ``docs/design/contracts/observation_lab_backup_contract.md``.
These tests pin the "fail closed on inconsistent restore" invariant.
"""

from __future__ import annotations

from unittest.mock import patch


def test_first_pass_records_and_verifies_ok():
    from aletheia.observation_lab.checkpoint import (
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
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())
    verdict = verify_joint_checkpoint()
    assert verdict.ok is True
    assert verdict.reason == "fingerprint_match"


def test_manifest_set_change_causes_mismatch_verdict():
    """Publishing a new active manifest after a checkpoint = failed proof."""
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.observation_lab.manifest import build_manifest, publish_manifest

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


def test_app_migration_head_change_causes_mismatch_verdict():
    """A restore that rolls app.db to a different migration head must be seen."""
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    save_joint_checkpoint(compute_joint_checkpoint())

    # Simulate an app.db restore by monkeypatching the migration reader.
    with patch(
        "aletheia.observation_lab.checkpoint._read_app_migration_state",
        return_value=("<restored-different-head>", 999),
    ):
        verdict = verify_joint_checkpoint()

    assert verdict.ok is False
    assert "fingerprint_mismatch" in verdict.reason
    # The reason must name what drifted so the operator can act on it.
    assert "app_head" in verdict.reason
    assert "count" in verdict.reason


def test_save_is_idempotent_on_unchanged_fingerprint():
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )
    from aletheia.observation_lab.db import get_lab_db

    save_joint_checkpoint(compute_joint_checkpoint())
    save_joint_checkpoint(compute_joint_checkpoint())
    save_joint_checkpoint(compute_joint_checkpoint())

    row = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM joint_checkpoints"
    ).fetchone()
    # Three save calls, one fingerprint → one stored row.
    assert row["n"] == 1


def test_fingerprint_is_deterministic_across_recomputes():
    from aletheia.observation_lab.checkpoint import compute_joint_checkpoint

    a = compute_joint_checkpoint()
    b = compute_joint_checkpoint()
    # Same state, different call — checkpoint_id and recorded_at differ but
    # the fingerprint must not.
    assert a.joint_fingerprint == b.joint_fingerprint
    assert a.checkpoint_id != b.checkpoint_id
