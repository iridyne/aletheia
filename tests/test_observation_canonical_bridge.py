"""Focused mechanics for the Observation Experiment ownership bridge."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

import pytest


@dataclass
class _Adapter:
    adapter_id: str = "test-adapter"
    adapter_version: str = "v1"
    behavior_dimension: str = "test_dimension"
    requires_outcome_seed: bool = False

    def assignment_unit_key(self, context):
        return str(context["unit"])

    def eligible(self, manifest, context):
        return True

    def deterministic_baseline(self, context):
        return {"value": 1.0}

    def apply_payload(self, baseline, payload, context):
        return dict(payload)

    def build_outcome_seed(self, context):
        return None


def _manifest(*, effect_mode="paper_treatment", **overrides):
    from aletheia.observation_lab.manifest import build_manifest

    values = dict(
        observation_id="obs-canonical",
        generation=1,
        spec_hash="spec-hash",
        treatment_adapter_id="test-adapter",
        treatment_adapter_version="v1",
        assignment_policy="stable_assignment_by_key",
        assignment_salt="salt",
        assignment_namespace_key="legacy-experiment-id",
        assignment_unit_key_template="{unit}",
        allocation_pct=1.0,
        control_payload={"value": 1.0},
        treatment_payload={"value": 2.0},
        eligible_population={"population_key": "test"},
        behavior_contract_hash="behavior-hash",
        interference_key="test-interference",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"value": 1.0},
        unavailable_fallback="baseline",
        effect_mode=effect_mode,
        owner_admission_ref="owner:admission-1" if effect_mode == "paper_treatment" else None,
        behavior_policy_epoch_ref="policy:v1",
    )
    values.update(overrides)
    return build_manifest(**values)


def test_lab_v2_schema_and_authority_start_legacy():
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.canonical import get_authority_source

    tables = {
        str(row[0])
        for row in get_lab_db().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "observation_generation_contracts",
        "observation_nominations",
        "observation_exposures",
        "observation_evaluations",
        "observation_inference_plans",
        "observation_lifecycle_events",
        "manifest_revocations",
        "observation_import_cursors",
        "observation_job_health",
        "observation_retention_claims",
        "observation_archives",
        "lab_audit_outbox",
    } <= tables
    assert get_authority_source().source == "legacy"


def test_bridge_health_reports_only_current_unrevoked_manifests():
    from aletheia.observation_lab.canonical import (
        observation_bridge_health,
        set_authority_source,
    )
    from aletheia.observation_lab.manifest import publish_manifest
    from aletheia.observation_lab.db import get_lab_db

    set_authority_source("lab", actor="owner", reason="bridge-health-test")
    lab = get_lab_db()
    for observation_id in ("obs-health-active", "obs-health-expired", "obs-health-revoked"):
        lab.execute(
            "INSERT INTO observation_windows "
            "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
            "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
            "VALUES (?, 'filter_shadow', 'canonical-v1', 1, ?, 'observe_only', 'active', '{}', ?, "
            "'2020-01-01T00:00:00+00:00', '9999-12-31T00:00:00+00:00', "
            "'9999-12-31T00:00:00+00:00')",
            (observation_id, f"slot:{observation_id}", f"spec:{observation_id}"),
        )
    publish_manifest(_manifest(observation_id="obs-health-active", spec_hash="spec:obs-health-active"))
    publish_manifest(_manifest(
        observation_id="obs-health-expired",
        spec_hash="spec:obs-health-expired",
        effective_from="2020-01-01T00:00:00+00:00",
        effective_until="2021-01-01T00:00:00+00:00",
    ))
    revoked = _manifest(
        observation_id="obs-health-revoked",
        spec_hash="spec:obs-health-revoked",
    )
    publish_manifest(revoked)
    lab.execute(
        "INSERT INTO manifest_revocations "
        "(revocation_id, manifest_id, manifest_hash, observation_id, generation, revoked_at, reason, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "health-revocation", revoked.manifest_id, revoked.manifest_hash,
            revoked.observation_id, revoked.generation,
            "2020-01-02T00:00:00+00:00", "test", "2020-01-02T00:00:00+00:00",
        ),
    )

    health = observation_bridge_health()

    assert health["available"] is True
    assert health["active_manifest_count"] == 1
    assert health["active_revocation_count"] == 1
    assert health["manifest_contracts"] == ["execution-manifest/v2"]


def test_missing_authority_row_fails_closed():
    from aletheia.observation_lab.canonical import get_authority_source
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "DELETE FROM observation_authority_state WHERE state_key='experiment_authority'"
    )
    with pytest.raises(RuntimeError, match="authority_state_unavailable"):
        get_authority_source()


def test_lab_authority_resolves_and_imports_one_receipt():
    from aletheia.observation_lab.canonical import (
        import_delivery_receipts,
        prune_observation_delivery_outbox,
        register_treatment_adapter,
        register_observation_spec,
        resolve_observation_treatment,
        set_authority_source,
    )
    from aletheia.observation_lab.manifest import publish_manifest
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    register_treatment_adapter(_Adapter(), replace=True)
    set_authority_source("lab", actor="owner", reason="cutover-test")
    get_lab_db().execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('obs-canonical', 'filter_shadow', 'canonical-v1', 1, 'slot-canonical', "
        "'external_authority', 'active', '{\"production_mutation_allowed\":true,\"authorizing\":true}', 'spec-hash', "
        "'2020-01-01T00:00:00+00:00', '9999-12-31T00:00:00+00:00', "
        "'9999-12-31T00:00:00+00:00')"
    )
    manifest = _manifest()
    publish_manifest(manifest)

    decision = resolve_observation_treatment(
        adapter_id="test-adapter",
        candidate_key="candidate-1",
        context={"unit": "unit-1", "interference_key": "test-interference"},
        baseline={"value": 1.0},
        now="2026-01-01T00:00:00+00:00",
        consumer="test",
        consumer_stage="score",
        outcome_candidate_key="candidate-1",
    )
    assert decision.receipt_id
    assert decision.assignment == "treatment"
    assert decision.payload == {"value": 2.0}
    assert decision.behavior_changed is True

    outbox = get_db().execute(
        "SELECT sequence, receipt_id FROM observation_delivery_outbox"
    ).fetchone()
    assert outbox["receipt_id"] == decision.receipt_id
    imported = import_delivery_receipts()
    assert imported["processed"] == 1
    row = get_lab_db().execute(
        "SELECT source_receipt_id, applied, behavior_changed FROM observation_exposures"
    ).fetchone()
    assert tuple(row) == (decision.receipt_id, 1, 1)
    claim = get_lab_db().execute(
        "SELECT source_ref, claim_domain, status FROM observation_retention_claims"
    ).fetchone()
    assert tuple(claim) == ("candidate-1", "filter_candidate_outcomes", "active")
    # At-least-once retry is exactly-once at the Lab ledger.
    assert import_delivery_receipts()["processed"] == 0
    assert get_lab_db().execute("SELECT COUNT(*) FROM observation_exposures").fetchone()[0] == 1
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2020-01-01T00:00:00+00:00", decision.receipt_id),
    )
    assert prune_observation_delivery_outbox(keep_acknowledged=0)["deleted"] == 1


def test_paper_manifest_without_explicit_lab_authorization_falls_back_to_baseline():
    from aletheia.observation_lab.canonical import (
        register_treatment_adapter,
        resolve_observation_treatment,
        set_authority_source,
    )
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.manifest import publish_manifest

    register_treatment_adapter(_Adapter(), replace=True)
    set_authority_source("lab", actor="owner", reason="missing-paper-authority-test")
    lab = get_lab_db()
    lab.execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('obs-missing-paper-authority', 'filter_shadow', 'canonical-v1', 1, "
        "'slot-missing-paper-authority', 'external_authority', 'active', '{}', "
        "'spec:missing-paper-authority', '2020-01-01T00:00:00+00:00', "
        "'9999-12-31T00:00:00+00:00', '9999-12-31T00:00:00+00:00')"
    )
    manifest = _manifest(
        observation_id="obs-missing-paper-authority",
        spec_hash="spec:missing-paper-authority",
    )
    publish_manifest(manifest)

    decision = resolve_observation_treatment(
        adapter_id="test-adapter",
        candidate_key="candidate-missing-paper-authority",
        context={
            "unit": "unit-missing-paper-authority",
            "interference_key": "test-interference",
        },
        baseline={"value": 1.0},
        now="2026-01-01T00:00:00+00:00",
    )

    assert decision.reason == "observe_only_generation"
    assert decision.payload == {"value": 1.0}
    assert decision.behavior_changed is False


def test_lab_outbox_prune_stays_unavailable_without_import_proof():
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        prune_observation_delivery_outbox,
        set_authority_source,
    )
    from aletheia.storage.sqlite import get_db

    set_authority_source("lab", actor="owner", reason="prune-proof-test")
    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="capture-prune-proof",
        candidate_key="candidate-prune-proof",
        assignment="unassigned",
        resolution_reason="adapter_unavailable",
        authority_source="lab",
    )
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2020-01-01T00:00:00+00:00", receipt_id),
    )

    result = prune_observation_delivery_outbox(keep_acknowledged=0)

    assert result == {
        "available": False,
        "reason": "replay_proof_unavailable",
        "deleted": 0,
        "authority_source": "lab",
    }


def test_identityless_capture_receipt_is_imported_as_non_authorizing_lab_audit():
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        import_delivery_receipts,
        set_authority_source,
    )
    from aletheia.observation_lab.db import get_lab_db

    set_authority_source("lab", actor="owner", reason="capture-import-test")
    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="capture-without-generation",
        candidate_key="candidate-capture",
        assignment="unassigned",
        resolution_reason="adapter_unavailable",
        authority_source="lab",
    )
    assert import_delivery_receipts()["processed"] == 1
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_exposures WHERE source_receipt_id=?",
        (receipt_id,),
    ).fetchone()[0] == 0
    audit = get_lab_db().execute(
        "SELECT payload_json FROM lab_audit_outbox WHERE dedup_key=?",
        (f"observation-receipt-capture:{receipt_id}",),
    ).fetchone()
    assert audit is not None
    assert receipt_id in audit[0]


def test_legacy_assignment_namespace_and_denominator_are_bitwise_compatible():
    from aletheia.learning.experiments import stable_assignment
    from aletheia.learning.models import LearningExperiment
    from aletheia.observation_lab.manifest import resolve_treatment

    manifest = _manifest(
        assignment_namespace_key="legacy-experiment-id",
        assignment_hash_version="sha256-prefix16/0xffffffffffffffff",
    )
    legacy = LearningExperiment(
        experiment_id="legacy-experiment-id",
        hypothesis_id="h",
        assignment_salt=manifest.assignment_salt,
        allocation_pct=manifest.allocation_pct,
    )
    for index in range(1000):
        key = f"candidate-{index}"
        expected = stable_assignment(legacy, key)
        actual = resolve_treatment(
            manifest=manifest,
            candidate_key=key,
            assignment_key=key,
            now="2026-01-01T00:00:00+00:00",
        ).assignment
        assert actual == expected


def test_legacy_numeric_caller_uses_lab_seam_after_cutover(monkeypatch):
    from aletheia.learning import experiments
    from aletheia.observation_lab.canonical import set_authority_source
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.manifest import build_manifest, publish_manifest
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    set_authority_source("lab", actor="owner", reason="numeric-seam-test")
    get_lab_db().execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('obs-numeric', 'filter_shadow', 'canonical-v1', 1, 'slot-numeric', "
        "'external_authority', 'active', '{\"production_mutation_allowed\":true,\"authorizing\":true}', "
        "'spec-numeric', '2020-01-01T00:00:00+00:00', '9999-12-31T00:00:00+00:00', "
        "'9999-12-31T00:00:00+00:00')"
    )
    publish_manifest(build_manifest(
        observation_id="obs-numeric",
        generation=1,
        spec_hash="spec-numeric",
        treatment_adapter_id="learning.numeric:ranker_case_score",
        treatment_adapter_version="v1",
        assignment_policy="stable_assignment_by_key",
        assignment_salt="salt",
        assignment_namespace_key="legacy-numeric-experiment",
        assignment_unit_key_template="{candidate_key}",
        allocation_pct=1.0,
        control_payload={"value": 1.0},
        treatment_payload={"value": 2.0},
        eligible_population={"path_id": "ranker_case_score"},
        behavior_contract_hash="behavior",
        interference_key="ranker_case_score",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"value": 1.0},
        unavailable_fallback="baseline",
        effect_mode="paper_treatment",
        owner_admission_ref="owner:numeric",
    ))
    decision = experiments.apply_numeric_treatment(
        path_id="ranker_case_score",
        scope={"feature": "score"},
        action={"delta": 1},
        source_cohort="test",
        target_horizon="24h",
        candidate_key="candidate-1",
        baseline=1.0,
        treatment=2.0,
        consumer="test",
        consumer_stage="score",
        assignment_key="candidate-1",
    )
    assert decision.value == 2.0
    assert decision.applied is True
    assert decision.exposure_id
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 0
    row = get_db().execute(
        "SELECT authority_source, assignment_unit_key FROM observation_delivery_outbox"
    ).fetchone()
    assert tuple(row) == ("lab", "candidate-1")


def test_deferred_lab_treatment_backpressure_keeps_publication_on_baseline(monkeypatch):
    from aletheia.learning import experiments
    from aletheia.observation_lab import canonical
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        set_authority_source,
    )
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(canonical, "_OUTBOX_CAPACITY", 1)
    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    set_authority_source("lab", actor="owner", reason="deferred-backpressure-test")
    append_delivery_receipt(
        receipt_id="backpressure-existing",
        assignment="unassigned",
        resolution_reason="test",
        authority_source="lab",
    )
    deferred: list[dict] = []
    decision = experiments.apply_numeric_treatment(
        path_id="ranker_case_score",
        scope={"feature": "score"},
        action={"delta": 1},
        source_cohort="test",
        target_horizon="24h",
        candidate_key="backpressure-candidate",
        baseline=1.0,
        treatment=2.0,
        consumer="test",
        consumer_stage="score",
        deferred_exposure_writes=deferred,
    )

    assert decision.value == 1.0
    assert decision.reason == "outbox_backpressure"
    assert deferred == []
    assert get_db().execute(
        "SELECT COUNT(*) FROM observation_delivery_outbox"
    ).fetchone()[0] == 1


def test_observe_only_and_deny_tombstone_never_change_baseline():
    from aletheia.observation_lab.canonical import (
        register_treatment_adapter,
        resolve_observation_treatment,
        set_authority_source,
    )
    from aletheia.observation_lab.db import get_lab_db, lab_transaction
    from aletheia.observation_lab.manifest import publish_manifest

    register_treatment_adapter(_Adapter(), replace=True)
    set_authority_source("lab", actor="owner", reason="observe-only-test")
    observe = _manifest(effect_mode="observe_only")
    publish_manifest(observe)
    decision = resolve_observation_treatment(
        adapter_id="test-adapter",
        candidate_key="candidate-2",
        context={"unit": "unit-2", "interference_key": "test-interference"},
        baseline={"value": 1.0},
        now="2026-01-01T00:00:00+00:00",
    )
    assert decision.assignment == "treatment"
    assert decision.payload == {"value": 1.0}
    assert decision.behavior_changed is False

    with lab_transaction(immediate=True) as db:
        db.execute(
            "INSERT INTO manifest_revocations "
            "(revocation_id, manifest_id, manifest_hash, observation_id, generation, revoked_at, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "rev-1", observe.manifest_id, observe.manifest_hash, observe.observation_id,
                observe.generation, "2025-01-01T00:00:00+00:00", "harm", "2025-01-01T00:00:00+00:00",
            ),
        )
    revoked = resolve_observation_treatment(
        adapter_id="test-adapter",
        candidate_key="candidate-3",
        context={"unit": "unit-3", "interference_key": "test-interference"},
        baseline={"value": 1.0},
        now="2026-01-01T00:00:00+00:00",
    )
    assert revoked.reason == "manifest_revoked"
    assert revoked.payload == {"value": 1.0}


def test_receipt_is_atomic_with_caller_transaction():
    from aletheia.observation_lab.canonical import append_delivery_receipt
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    receipt_id = "atomic-receipt"
    with pytest.raises(RuntimeError):
        with transaction(immediate=True):
            append_delivery_receipt(
                receipt_id=receipt_id,
                assignment="unassigned",
                resolution_reason="test",
                db=get_db(),
            )
            raise RuntimeError("rollback")
    assert get_db().execute(
        "SELECT 1 FROM observation_delivery_outbox WHERE receipt_id=?", (receipt_id,)
    ).fetchone() is None


def test_checkpoint_and_manifest_identity_triggers_remain_append_only():
    from aletheia.observation_lab.db import get_lab_db

    get_lab_db().execute(
        "INSERT INTO observation_archives "
        "(archive_id, observation_id, generation, terminal_status, root_identity, archive_schema, archive_path, archive_sha256, replay_fingerprint, terminal_at, created_at) "
        "VALUES ('a', 'o', 1, 'finalized', 'root', 'v1', '/tmp/a', 'hash', 'replay', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        get_lab_db().execute("DELETE FROM observation_archives WHERE archive_id='a'")
    # The append-only trigger is part of the physical schema.
    assert get_lab_db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='trg_observation_archives_no_delete'"
    ).fetchone()


def test_generation_contract_fixed_look_lifecycle_claim_and_archive(tmp_path):
    from aletheia.observation_lab.canonical import (
        append_lifecycle_event,
        compact_finalized_generations,
        consume_inference_plan,
        enqueue_lab_audit,
        freeze_inference_plan,
        record_generation_contract,
        record_observation_evaluation,
        record_observation_nomination,
        register_inference_plan,
        release_retention_claim,
        upsert_retention_claim,
        verify_generation_archive,
        write_generation_archive,
    )
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    db.execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before, terminal_at) "
        "VALUES ('obs-v2', 'test', 'v1', 1, 'slot-v2', 'observe_only', 'finalized', '{}', 'spec-v2', "
        "'2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', '2026-01-03T00:00:00+00:00', '2026-01-03T00:00:00+00:00')"
    )
    nomination = record_observation_nomination(
        source_nomination_id="nom-1", source_hash="source-hash", snapshot={"hypothesis": "h"},
        disposition="admitted", observation_id="obs-v2", generation=1,
    )
    assert record_observation_nomination(
        source_nomination_id="nom-1", source_hash="source-hash", snapshot={"hypothesis": "h"},
        disposition="admitted", observation_id="obs-v2", generation=1,
    ) == nomination
    contract = record_generation_contract(
        observation_id="obs-v2", generation=1, stage="shadow",
        assignment_policy="stable_assignment_by_key", assignment_namespace_key="obs-v2:g1",
        assignment_salt="salt", assignment_unit_key_template="{unit}", allocation_pct=0.5,
        target_horizon="24h", outcome_contract_version="outcome/v1", metric_version="metric/v1",
        behavior_contract_hash="behavior-v2", started_at="2026-01-01T00:00:00+00:00",
    )
    assert contract
    plan_id = register_inference_plan(
        observation_id="obs-v2", generation=1, inference_contract_version="look/v1",
        plan={"look": 1},
    )
    freeze_inference_plan(plan_id, cutoff={"sequence": 4})
    evaluation_id = record_observation_evaluation(
        observation_id="obs-v2", generation=1, evaluation_kind="confirmatory",
        source_cutoff="main:sequence=4", input_fingerprint="input-v1",
        exposure_fingerprint="exposure-v1", outcome_fingerprint="outcome-v1",
        result={"verdict": "hold"}, verdict="hold", disposition="refrozen",
    )
    consume_inference_plan(plan_id, evaluation_id=evaluation_id)
    db.execute(
        "INSERT INTO observation_units "
        "(unit_id, observation_id, generation, spec_hash, unit_key, source_kind, source_ref, source_hash, "
        "known_at, captured_at, admission) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "unit-v2", "obs-v2", 1, "spec-v2", "unit-1", "test", "source-1", "hash-1",
            "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "admitted",
        ),
    )
    event_id = append_lifecycle_event(
        observation_id="obs-v2", generation=1, to_status="finalized", action="finalize",
        reason="evidence-complete", actor="worker", dedup_key="obs-v2-finalize",
    )
    assert event_id
    assert enqueue_lab_audit(
        event_type="test_event", payload={"ok": True}, dedup_key="audit-test",
    )
    claim = upsert_retention_claim(
        observation_id="obs-v2", generation=1, source_kind="test",
        source_ref="source-1", source_hash="hash-1", claim_domain="outcomes",
        derived_from_fingerprint="exposure-v1",
    )
    release_retention_claim(claim, proof_fingerprint="archive-v1", reason="archived")
    archive = write_generation_archive("obs-v2", 1, archive_root=str(tmp_path))
    assert verify_generation_archive(archive["archive_id"])["available"] is True
    assert db.execute(
        "SELECT status, consumed_evaluation_id FROM observation_inference_plans WHERE plan_id=?",
        (plan_id,),
    ).fetchone()["status"] == "consumed"
    compacted = compact_finalized_generations(retention_days=0)
    assert compacted["evicted_rows"] >= 1
    assert db.execute(
        "SELECT 1 FROM observation_units WHERE unit_id='unit-v2'"
    ).fetchone() is None


def test_retention_claim_cannot_invent_unbound_source_identity():
    from aletheia.observation_lab.canonical import upsert_retention_claim
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    db.execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('claim-proof', 'test', 'v1', 1, 'slot-claim-proof', 'observe_only', 'active', '{}', "
        "'claim-spec', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', "
        "'2026-01-03T00:00:00+00:00')"
    )
    with pytest.raises(ValueError, match="retention_claim_source_unproven"):
        upsert_retention_claim(
            observation_id="claim-proof", generation=1,
            source_kind="outcome_candidate_key", source_ref="missing-outcome",
            source_hash="a" * 64, claim_domain="outcomes",
            derived_from_fingerprint="derived",
        )


def test_lab_cutover_rejects_fabricated_active_retention_claim():
    from aletheia.observation_lab.canonical import set_authority_source
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    db.execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('claim-cutover-proof', 'test', 'v1', 1, 'slot-claim-cutover-proof', 'observe_only', 'active', '{}', "
        "'claim-cutover-spec', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', "
        "'2026-01-03T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO observation_retention_claims "
        "(claim_id, observation_id, generation, source_kind, source_ref, source_hash, claim_domain, "
        "derived_from_fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
        (
            "fabricated-claim", "claim-cutover-proof", 1, "test", "unbound-source", "a" * 64,
            "outcomes", "derived", "2026-01-01T00:00:00+00:00",
        ),
    )

    with pytest.raises(RuntimeError, match="authority_cutover_retention_claim_unproven"):
        set_authority_source("lab", actor="owner", reason="fabricated claim")


def test_evaluation_source_cutoff_rejects_unstructured_text():
    from aletheia.observation_lab.canonical import record_observation_evaluation
    from aletheia.observation_lab.db import get_lab_db

    get_lab_db().execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('cutoff-shape', 'test', 'v1', 1, 'slot-cutoff-shape', 'observe_only', 'active', '{}', "
        "'cutoff-shape-spec', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', "
        "'2026-01-03T00:00:00+00:00')"
    )
    with pytest.raises(ValueError, match="source_cutoff_invalid"):
        record_observation_evaluation(
            observation_id="cutoff-shape", generation=1,
            evaluation_kind="monitoring", source_cutoff="moving-main-rows",
            input_fingerprint="input", exposure_fingerprint="exposure",
            outcome_fingerprint="outcome", result={"verdict": "hold"},
            verdict="hold", disposition="monitoring",
        )


def test_evaluation_source_cutoff_cannot_run_ahead_of_import_cursor():
    from aletheia.observation_lab.canonical import record_observation_evaluation
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    db.execute(
        "INSERT INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
        "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
        "VALUES ('cutoff-ahead', 'test', 'v1', 1, 'slot-cutoff-ahead', 'observe_only', 'active', '{}', "
        "'cutoff-ahead-spec', '2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', "
        "'2026-01-03T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO observation_import_cursors "
        "(source_name, contiguous_sequence, retry_sequences_json, updated_at) "
        "VALUES ('main', 2, '[]', '2026-01-01T00:00:00+00:00')"
    )
    with pytest.raises(ValueError, match="source_cutoff_ahead_of_import"):
        record_observation_evaluation(
            observation_id="cutoff-ahead", generation=1,
            evaluation_kind="monitoring",
            source_cutoff='{"main_import_contiguous_sequence":3}',
            input_fingerprint="input", exposure_fingerprint="exposure",
            outcome_fingerprint="outcome", result={"verdict": "hold"},
            verdict="hold", disposition="monitoring",
        )


def test_lab_audit_outbox_mirrors_idempotently(monkeypatch, tmp_path):
    from aletheia.files import jsonl
    from aletheia.observation_lab.canonical import (
        enqueue_lab_audit,
        mirror_lab_audit_outbox,
    )
    from aletheia.observation_lab.db import get_lab_db

    log_dir = tmp_path / "logs"
    monkeypatch.setattr(jsonl, "_log_dir", lambda: log_dir)
    audit_id = enqueue_lab_audit(
        event_type="owner_admission",
        payload={"admission_ref": "owner:1"},
        dedup_key="mirror-owner-1",
        observation_id="obs-mirror",
        generation=1,
    )
    first = mirror_lab_audit_outbox()
    assert audit_id in first["mirrored"]
    assert get_lab_db().execute(
        "SELECT status, attempts FROM lab_audit_outbox WHERE audit_id=?", (audit_id,)
    ).fetchone()["status"] == "mirrored"
    second = mirror_lab_audit_outbox()
    assert second["processed"] == 0
    lines = (log_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if audit_id in line]) == 1


def test_inference_plan_rejects_evaluation_from_another_generation():
    from aletheia.observation_lab.canonical import (
        consume_inference_plan,
        freeze_inference_plan,
        record_observation_evaluation,
        register_inference_plan,
    )
    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    for observation_id in ("obs-plan-a", "obs-plan-b"):
        db.execute(
            "INSERT INTO observation_windows "
            "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
            "spec_json, spec_hash, started_at, collect_until, finalize_not_before) "
            "VALUES (?, 'test', 'v1', 1, ?, 'observe_only', 'active', '{}', ?, "
            "'2026-01-01T00:00:00+00:00', '2026-01-02T00:00:00+00:00', "
            "'2026-01-03T00:00:00+00:00')",
            (observation_id, f"slot:{observation_id}", f"spec:{observation_id}"),
        )
    plan_id = register_inference_plan(
        observation_id="obs-plan-a", generation=1,
        inference_contract_version="look/v1", plan={"look": 1},
    )
    freeze_inference_plan(plan_id, cutoff={"sequence": 1})
    evaluation_id = record_observation_evaluation(
        observation_id="obs-plan-b", generation=1, evaluation_kind="monitoring",
        source_cutoff="main:sequence=1", input_fingerprint="input-b",
        exposure_fingerprint="exposure-b", outcome_fingerprint="outcome-b",
        result={"verdict": "hold"}, verdict="hold", disposition="monitoring",
    )

    with pytest.raises(sqlite3.IntegrityError, match="inference_evaluation_identity_mismatch"):
        consume_inference_plan(plan_id, evaluation_id=evaluation_id)
    assert db.execute(
        "SELECT status, consumed_evaluation_id FROM observation_inference_plans WHERE plan_id=?",
        (plan_id,),
    ).fetchone()["status"] == "frozen"


def test_importer_does_not_treat_a_numeric_outbox_gap_as_missing_evidence():
    from aletheia.observation_lab.canonical import import_delivery_receipts
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    get_lab_db()
    main = get_db()
    main.execute(
        "INSERT INTO observation_delivery_outbox "
        "(sequence, receipt_id, receipt_kind, observation_id, generation, assignment, applied, behavior_changed, "
        "baseline_payload_hash, applied_payload_hash, baseline_payload_json, applied_payload_json, resolution_reason, applied_at) "
        "VALUES (2, 'gap-receipt', 'treatment', 'gap-observation', 1, 'unassigned', 0, 0, ?, ?, '{}', '{}', 'gap', ?)",
        ("a" * 64, "b" * 64, "2026-01-01T00:00:00+00:00"),
    )
    result = import_delivery_receipts()
    assert result["processed"] == 1
    assert result["failed_sequence"] is None
    assert get_lab_db().execute(
        "SELECT contiguous_sequence FROM observation_import_cursors WHERE source_name='main'"
    ).fetchone()[0] == 2


def test_canonical_registration_does_not_create_a_main_experiment():
    from aletheia.observation_lab.canonical import register_observation_spec
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    row = register_observation_spec({
        "filter_name": "ranker_confidence_gate",
        "stage": "shadow",
        "scanner_family": "family",
        "direction": "long",
        "market_type": "spot",
        "horizon": "24h",
        "control": {"minimum": 0.8},
        "treatment": {"minimum": 0.9},
    })
    assert row["authority_mode"] == "observe_only"
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_windows WHERE observation_id=?",
        (row["observation_id"],),
    ).fetchone()[0] == 1
    assert get_db().execute(
        "SELECT COUNT(*) FROM learning_experiments"
    ).fetchone()[0] == 0


def test_lab_authority_blocks_legacy_experiment_behavior_paths():
    from aletheia.learning import ledger
    from aletheia.learning.contribution_runner import run_verdict_cycle
    from aletheia.learning.lifecycle import run_lifecycle_controller
    from aletheia.observation_lab.canonical import set_authority_source
    from aletheia.analytics.filter_controller import run_controller_observe

    set_authority_source("lab", actor="owner", reason="legacy-path-guard-test")
    with pytest.raises(RuntimeError, match="observation_lab_authority_required"):
        ledger.insert_experiment(object())
    with pytest.raises(RuntimeError, match="observation_lab_authority_required"):
        ledger.insert_exposure(object())
    assert run_verdict_cycle()["status"] == "canonical_lab_authority"
    assert run_lifecycle_controller()["status"] == "canonical_lab_authority"
    assert run_controller_observe()["pass_reason"] == "canonical_lab_authority"
    assert ledger.list_open_experiments() == []


def test_owner_close_is_imported_as_terminal_lab_lifecycle():
    from aletheia.observation_lab.canonical import (
        import_delivery_receipts,
        record_owner_action,
        register_observation_spec,
        register_treatment_adapter,
        resolve_observation_treatment,
        set_authority_source,
    )
    from aletheia.observation_lab.manifest import (
        build_manifest,
        publish_manifest,
    )
    from aletheia.observation_lab.db import get_lab_db

    register_treatment_adapter(_Adapter(), replace=True)
    set_authority_source("lab", actor="owner", reason="owner-close-test")
    root = register_observation_spec({
        "filter_name": "owner-close",
        "stage": "paper_10",
        "scanner_family": "test",
        "direction": "long",
        "market_type": "spot",
        "horizon": "24h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
    })
    manifest = build_manifest(
        observation_id=root["observation_id"],
        generation=1,
        spec_hash=root["spec_hash"],
        treatment_adapter_id="test-adapter",
        treatment_adapter_version="v1",
        assignment_policy="stable_assignment_by_key",
        assignment_salt="owner-close",
        assignment_namespace_key=root["observation_id"],
        assignment_unit_key_template="{unit}",
        allocation_pct=1.0,
        control_payload={"value": 1.0},
        treatment_payload={"value": 2.0},
        eligible_population={"path_id": "owner-close"},
        behavior_contract_hash="owner-close-contract",
        interference_key="owner-close",
        composability_contract="exclusive",
        effective_from="2020-01-01T00:00:00+00:00",
        deterministic_baseline={"value": 1.0},
        unavailable_fallback="baseline",
        effect_mode="paper_treatment",
        owner_admission_ref="owner:close-test",
    )
    publish_manifest(manifest)
    receipt_id = record_owner_action(
        action="close",
        actor="owner",
        reason="owner closed generation",
        observation_id=root["observation_id"],
        generation=1,
        manifest_id=manifest.manifest_id,
        manifest_hash=manifest.manifest_hash,
        dedup_key="owner-close:1",
    )
    assert import_delivery_receipts()["processed"] == 1
    state = get_lab_db().execute(
        "SELECT status FROM observation_windows WHERE observation_id=? AND generation=1",
        (root["observation_id"],),
    ).fetchone()
    assert state[0] == "aborted"
    assert get_lab_db().execute(
        "SELECT 1 FROM manifest_revocations WHERE source_action_id=?",
        (receipt_id,),
    ).fetchone()
    decision = resolve_observation_treatment(
        adapter_id="test-adapter",
        candidate_key="candidate-owner-close",
        context={"unit": "unit-owner-close", "interference_key": "owner-close"},
        baseline={"value": 1.0},
        now="2026-01-01T00:00:00+00:00",
    )
    assert decision.reason == "observation_generation_not_active"
    assert decision.payload == {"value": 1.0}


def test_owner_admission_creates_lab_successor_and_applied_receipt():
    from aletheia.observation_lab.canonical import (
        import_delivery_receipts,
        record_owner_action,
        register_observation_spec,
        set_authority_source,
    )
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    set_authority_source("lab", actor="owner", reason="admission-successor-test")
    root = register_observation_spec({
        "filter_name": "admission-successor",
        "stage": "shadow",
        "scanner_family": "test",
        "direction": "long",
        "market_type": "spot",
        "horizon": "24h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
    })
    receipt_id = record_owner_action(
        action="admit",
        actor="owner",
        reason="owner approved paper admission",
        observation_id=root["observation_id"],
        generation=1,
        dedup_key="owner-admit-successor:1",
    )
    assert import_delivery_receipts()["processed"] == 1
    states = get_lab_db().execute(
        "SELECT generation, status FROM observation_windows WHERE observation_id=? ORDER BY generation",
        (root["observation_id"],),
    ).fetchall()
    assert [tuple(row) for row in states] == [(1, "invalidated"), (2, "active")]
    contract = get_lab_db().execute(
        "SELECT stage, parent_generation, owner_admission_ref FROM observation_generation_contracts "
        "WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    assert tuple(contract) == ("paper_10", 1, f"owner-action:{receipt_id}")
    successor = get_lab_db().execute(
        "SELECT spec_json FROM observation_windows WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    successor_spec = json.loads(successor["spec_json"])
    assert successor_spec["production_mutation_allowed"] is True
    assert successor_spec["authorizing"] is True
    plan = get_lab_db().execute(
        "SELECT status, plan_json FROM observation_inference_plans "
        "WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    assert plan["status"] == "collecting"
    assert json.loads(plan["plan_json"])["owner_action_receipt_id"] == receipt_id
    assert get_lab_db().execute(
        "SELECT 1 FROM observation_lifecycle_events "
        "WHERE observation_id=? AND generation=2 AND action='manifest_not_published' "
        "AND reason='owner_action_pending_manifest'",
        (root["observation_id"],),
    ).fetchone() is not None
    receipt = get_db().execute(
        "SELECT lab_application_status, lab_applied_generation, lab_applied_event_id "
        "FROM observation_delivery_outbox WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone()
    assert receipt["lab_application_status"] == "applied"
    assert receipt["lab_applied_generation"] == 2
    assert receipt["lab_applied_event_id"]


def test_paper_manifest_publication_commits_plan_manifest_and_audit_together():
    from aletheia.observation_lab.canonical import (
        import_delivery_receipts,
        publish_manifest_with_lifecycle,
        record_owner_action,
        register_observation_spec,
        register_treatment_adapter,
        set_authority_source,
    )
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.manifest import build_manifest

    register_treatment_adapter(_Adapter(), replace=True)
    set_authority_source("lab", actor="owner", reason="paper-publication-test")
    root = register_observation_spec({
        "filter_name": "paper-publication",
        "stage": "shadow",
        "scanner_family": "test",
        "direction": "long",
        "market_type": "spot",
        "horizon": "24h",
        "control": {"value": 1.0},
        "treatment": {"value": 2.0},
    })
    receipt_id = record_owner_action(
        action="admit",
        actor="owner",
        reason="owner approved paper publication",
        observation_id=root["observation_id"],
        generation=1,
        dedup_key="paper-publication:admit",
    )
    assert import_delivery_receipts()["processed"] == 1
    lab = get_lab_db()
    successor = lab.execute(
        "SELECT * FROM observation_windows WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    contract = lab.execute(
        "SELECT * FROM observation_generation_contracts WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    plan = lab.execute(
        "SELECT * FROM observation_inference_plans WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    assert plan["status"] == "collecting"
    manifest = build_manifest(
        observation_id=root["observation_id"],
        generation=2,
        spec_hash=successor["spec_hash"],
        treatment_adapter_id="test-adapter",
        treatment_adapter_version="v1",
        assignment_policy=contract["assignment_policy"],
        assignment_salt=contract["assignment_salt"],
        assignment_namespace_key=contract["assignment_namespace_key"],
        assignment_hash_version=contract["assignment_hash_version"],
        assignment_unit_key_template=contract["assignment_unit_key_template"],
        allocation_pct=contract["allocation_pct"],
        control_payload={"value": 1.0},
        treatment_payload={"value": 2.0},
        eligible_population={"path_id": "paper-publication"},
        behavior_contract_hash=contract["behavior_contract_hash"],
        interference_key="paper-publication",
        composability_contract="exclusive",
        effective_from=successor["started_at"],
        effective_until=contract["effective_until"],
        deterministic_baseline={"value": 1.0},
        unavailable_fallback="baseline",
        owner_admission_ref=f"owner-action:{receipt_id}",
        effect_mode="paper_treatment",
    )
    publication = publish_manifest_with_lifecycle(
        manifest,
        actor="owner",
        reason="paper manifest approved",
        inference_plan={
            "inference_contract_version": plan["inference_contract_version"],
            "plan": json.loads(plan["plan_json"]),
            "cutoff": {"sequence": 1},
        },
    )
    assert publication["published"] is True
    assert publication["inference_plan_id"] == plan["plan_id"]
    assert lab.execute(
        "SELECT status, cutoff_json FROM observation_inference_plans WHERE plan_id=?",
        (plan["plan_id"],),
    ).fetchone()["status"] == "frozen"
    assert lab.execute(
        "SELECT 1 FROM execution_manifests WHERE manifest_hash=?",
        (manifest.manifest_hash,),
    ).fetchone() is not None
    assert lab.execute(
        "SELECT 1 FROM observation_lifecycle_events WHERE dedup_key=?",
        (f"manifest-publish:{manifest.manifest_hash}",),
    ).fetchone() is not None
    assert lab.execute(
        "SELECT 1 FROM lab_audit_outbox WHERE dedup_key=?",
        (f"observation-lifecycle:manifest-publish:{manifest.manifest_hash}",),
    ).fetchone() is not None
    adoption_receipt = record_owner_action(
        action="adopt",
        actor="owner",
        reason="owner adopted paper treatment as baseline",
        observation_id=root["observation_id"],
        generation=2,
        manifest_id=manifest.manifest_id,
        manifest_hash=manifest.manifest_hash,
        expected_manifest_hash=manifest.manifest_hash,
        dedup_key="paper-publication:adopt",
    )
    assert import_delivery_receipts()["processed"] == 1
    adopted = lab.execute(
        "SELECT status FROM observation_windows WHERE observation_id=? AND generation=2",
        (root["observation_id"],),
    ).fetchone()
    assert adopted["status"] == "invalidated"
    lifecycle = lab.execute(
        "SELECT action, to_status FROM observation_lifecycle_events "
        "WHERE source_receipt_id=?",
        (adoption_receipt,),
    ).fetchone()
    assert tuple(lifecycle) == ("adopt", "superseded")
    assert lab.execute(
        "SELECT 1 FROM manifest_revocations WHERE source_action_id=?",
        (adoption_receipt,),
    ).fetchone() is not None


def test_main_delivery_outbox_is_append_only_until_acknowledged():
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        prune_observation_delivery_outbox,
    )
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_id="outbox-immutability",
        observation_id="obs-outbox",
        generation=1,
        candidate_key="candidate-outbox",
        assignment="unassigned",
        resolution_reason="test",
    )
    db = get_db()
    with pytest.raises(sqlite3.IntegrityError, match="identity is immutable"):
        db.execute(
            "UPDATE observation_delivery_outbox SET resolution_reason='tampered' "
            "WHERE receipt_id=?",
            (receipt_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="unacknowledged"):
        db.execute(
            "DELETE FROM observation_delivery_outbox WHERE receipt_id=?",
            (receipt_id,),
        )
    db.execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2026-01-01T00:00:00+00:00", receipt_id),
    )
    assert prune_observation_delivery_outbox(keep_acknowledged=0)["deleted"] == 1


def test_delivery_receipt_duplicate_id_cannot_change_identity():
    from aletheia.observation_lab.canonical import append_delivery_receipt

    append_delivery_receipt(
        receipt_id="receipt-identity",
        observation_id="obs-identity",
        generation=1,
        candidate_key="candidate-identity",
        assignment="unassigned",
        resolution_reason="first",
    )
    with pytest.raises(sqlite3.IntegrityError, match="identity_conflict"):
        append_delivery_receipt(
            receipt_id="receipt-identity",
            observation_id="obs-identity",
            generation=1,
            candidate_key="candidate-identity",
            assignment="unassigned",
            resolution_reason="tampered",
        )
