"""Historical fixed-look evidence remains readable, never writable at runtime."""

import hashlib
import json
import sqlite3

import pytest


def test_historical_cutoff_is_readable_and_rejects_tampering():
    from aletheia.learning.ledger import get_record
    from aletheia.storage.sqlite import get_db

    cutoff = json.dumps({"assignment_unit_keys": ["treatment", "control"]}, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(cutoff.encode()).hexdigest()
    db = get_db()
    db.execute(
        "INSERT INTO learning_inference_decisions "
        "(experiment_id,inference_family_id,inference_contract_version,plan_json,plan_hash,"
        "status,cutoff_json,cutoff_fingerprint,cutoff_frozen_at,authority_alpha,created_at,updated_at) "
        "VALUES ('historical','family','ufc-fixed-look/v2','{}',?,'cutoff_frozen',?,?,'2026-08-01',0.025,'2026-08-01','2026-08-01')",
        (hashlib.sha256(b"{}").hexdigest(), cutoff, fingerprint),
    )
    db.commit()
    decision = get_record("learning_inference_decisions", "historical")
    assert json.loads(decision["cutoff_json"])["assignment_unit_keys"] == ["treatment", "control"]
    assert decision["cutoff_fingerprint"] == fingerprint
    tampered = '{"assignment_unit_keys":["extra"]}'
    with pytest.raises(sqlite3.IntegrityError, match="cutoff is immutable"):
        db.execute("UPDATE learning_inference_decisions SET cutoff_json=?", (tampered,))
    assert get_record("learning_inference_decisions", "historical")["cutoff_json"] == cutoff




@pytest.mark.parametrize("table", [
    "learning_experiments", "learning_exposures", "learning_contribution_verdicts",
    "learning_inference_decisions", "learning_lifecycle_events",
])
def test_runtime_cannot_write_legacy_evidence_even_before_cutover(table):
    from aletheia.learning.models import LearningJobHealth
    from aletheia.learning.ledger import insert_record
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute("UPDATE observation_authority_state SET authority_source='legacy'")
    db.commit()
    before = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    with pytest.raises(RuntimeError, match="observation_lab_authority_required"):
        insert_record(table, LearningJobHealth(job_id="retired", schedule_key="retired"))
    assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == before
