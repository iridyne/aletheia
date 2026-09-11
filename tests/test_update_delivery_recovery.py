"""Bounded applied-receipt interruption recovery uses real SQLite evidence."""
import json

import pytest


@pytest.mark.parametrize("defect", [None, "prior_lab", "suffix_receipt", "retry", "foreign_state"])
def test_only_exact_unclassified_delivery_suffix_can_settle(defect):
    from aletheia.observation_lab.canonical import append_delivery_receipt, import_delivery_receipts
    from aletheia.observation_lab.checkpoint import compute_joint_checkpoint, save_joint_checkpoint, verify_joint_checkpoint
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.runner import observation_settlement_barrier

    lab = get_lab_db()
    append_delivery_receipt(receipt_kind="capture", receipt_id="prior", resolution_reason="adapter_unavailable")
    assert import_delivery_receipts()["processed"] == 1
    save_joint_checkpoint(compute_joint_checkpoint())
    append_delivery_receipt(receipt_kind="capture", receipt_id="interrupted", resolution_reason="adapter_unavailable")
    assert import_delivery_receipts()["processed"] == 1
    if defect in {"prior_lab", "suffix_receipt"}:
        row = lab.execute("SELECT audit_id,payload_json FROM lab_audit_outbox ORDER BY rowid " + ("ASC" if defect == "prior_lab" else "DESC") + " LIMIT 1").fetchone()
        payload = json.loads(row["payload_json"])
        payload["receipt"]["resolution_reason"] = "altered"
        lab.execute("UPDATE lab_audit_outbox SET payload_json=? WHERE audit_id=?", (json.dumps(payload), row["audit_id"]))
    elif defect == "retry":
        lab.execute("UPDATE observation_import_cursors SET retry_sequences_json='[2]'")
    elif defect == "foreign_state":
        lab.execute("CREATE TABLE unexpected_evidence (value TEXT)")
        lab.execute("INSERT INTO unexpected_evidence VALUES ('changed')")
    lab.commit()
    assert not verify_joint_checkpoint().ok
    count = lab.execute("SELECT COUNT(*) FROM joint_checkpoints").fetchone()[0]
    append_delivery_receipt(receipt_kind="capture", receipt_id="pending", resolution_reason="adapter_unavailable")
    from aletheia.observation_lab.update_settlement import prove_applied_delivery_suffix
    from aletheia.update.continuity import _checkpoint_preflight_verdict
    proof = prove_applied_delivery_suffix()
    assert proof["ok"] is (defect is None)
    verdict = _checkpoint_preflight_verdict({"verified": {"ok": False, "mismatch_components": ["lab_state"]}, "settleable": proof})
    assert (verdict == ("warning", "observation_settlement_required")) is (defect is None)
    with observation_settlement_barrier() as result:
        assert result["ok"] is (defect is None)
    if defect is None:
        assert result["delivery_import"]["processed"] == 1
        assert verify_joint_checkpoint().reason == "fingerprint_match"
        assert lab.execute("SELECT COUNT(*) FROM lab_audit_outbox").fetchone()[0] == 3
    else:
        assert lab.execute("SELECT COUNT(*) FROM joint_checkpoints").fetchone()[0] == count
        assert not verify_joint_checkpoint().ok
