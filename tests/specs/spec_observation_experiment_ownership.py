"""Executable Phase-1 debt ceiling for Observation Experiment Ownership V2."""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "observation_experiment_ownership_v2"
_CHECKPOINT_CONTRACT = "observation_lab_backup_contract"
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "aletheia"


def _production_sources() -> dict[str, str]:
    return {path.relative_to(_ROOT).as_posix(): path.read_text(encoding="utf-8") for path in _SRC.rglob("*.py")}


@spec_claim(
    SpecClaim(
        "OEO-S06",
        _CHECKPOINT_CONTRACT,
        "Bounded transport progress",
        "L2a",
        "Only settlement and the updater diagnostic may recognize pending-only Main progress; deployment remains strict.",
    )
)
def test_only_settlement_and_update_diagnostic_accept_main_outbox_progress():
    callers = {path for path, source in _production_sources().items() if "allow_main_outbox_progress=True" in source}
    assert callers == {
        "aletheia/observation_lab/runner.py",
        "aletheia/update/continuity.py",
    }


@spec_claim(
    SpecClaim(
        "OEO-S07",
        _CHECKPOINT_CONTRACT,
        "Pending transport frontier",
        "L2a",
        "Checkpoint identity excludes acknowledged retention rows while preserving the logical Main watermark.",
    )
)
def test_checkpoint_outbox_identity_is_the_pending_frontier():
    from aletheia.observation_lab.canonical import (
        append_delivery_receipt,
        prune_observation_delivery_outbox,
    )
    from aletheia.observation_lab.checkpoint import compute_joint_checkpoint
    from aletheia.storage.sqlite import get_db

    receipt_id = append_delivery_receipt(
        receipt_kind="capture",
        receipt_id="spec-checkpoint-frontier",
        resolution_reason="adapter_unavailable",
    )
    pending = compute_joint_checkpoint()
    get_db().execute(
        "UPDATE observation_delivery_outbox SET acknowledged_at=? WHERE receipt_id=?",
        ("2020-01-01T00:00:00+00:00", receipt_id),
    )
    acknowledged = compute_joint_checkpoint()
    prune_observation_delivery_outbox(keep_acknowledged=0, safety_grace_seconds=0)
    pruned = compute_joint_checkpoint()

    assert pending.main_outbox_pending_count == 1
    assert acknowledged.main_outbox_pending_count == 0
    assert acknowledged.main_outbox_high_watermark == pending.main_outbox_high_watermark
    assert pruned.main_outbox_high_watermark == acknowledged.main_outbox_high_watermark
    assert pruned.main_outbox_fingerprint == acknowledged.main_outbox_fingerprint


@spec_claim(
    SpecClaim(
        "OEO-S05",
        _CONTRACT,
        "Executable V2 boundary",
        "L2a",
        "Mechanism-aware Entry Learning is one Lab adapter with empty control and an explicit interference key.",
    )
)
def test_entry_learning_uses_the_canonical_lab_adapter_boundary():
    from aletheia.learning.entry_context import (
        ENTRY_LEARNING_ADAPTER_ID,
        ENTRY_LEARNING_ADAPTER_VERSION,
        ENTRY_LEARNING_INTERFERENCE_KEY,
        EntryLearningTreatmentAdapter,
    )
    from aletheia.observation_lab.canonical import registered_treatment_adapters
    from aletheia.observation_lab.runner import register_builtin_handlers

    register_builtin_handlers()
    assert (ENTRY_LEARNING_ADAPTER_ID, ENTRY_LEARNING_ADAPTER_VERSION) in (registered_treatment_adapters())
    adapter = EntryLearningTreatmentAdapter()
    assert adapter.deterministic_baseline({}) == {"include_learning_context": False}
    assert ENTRY_LEARNING_INTERFERENCE_KEY == "mechanism-aware-grounded-entry"
