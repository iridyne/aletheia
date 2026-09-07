"""Executable Phase-1 debt ceiling for Observation Experiment Ownership V2."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "observation_experiment_ownership_v2"
_CHECKPOINT_CONTRACT = "observation_lab_backup_contract"
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "aletheia"

_LEGACY_TABLES = frozenset({
    "learning_experiments",
    "learning_exposures",
    "learning_contribution_verdicts",
    "filter_shadow_experiments",
})

# Exact inventory of production modules that mention one of the four legacy
# Experiment tables. This is a debt ceiling: later migration phases may shrink
# it, but new entries require explicit contract review.
#
# Trimmed by 17 rows after #2945 retired the legacy Learning Experiment runtime.
# The assertion is exact in BOTH directions, so debt shrinking trips it as loudly
# as debt growing; `unexpected` (discovered - allowed) is the half that guards the
# invariant, and it was empty, so nothing lost protection here.
_REFERENCE_ALLOWLIST = frozenset({
    "aletheia/agent_runtime/scheduler.py",
    "aletheia/analytics/profit_gradient_evidence.py",
    # Backup verification must inspect legacy rows so an unproven cutover
    # cannot be presented as an authoritative recovery checkpoint.
    "aletheia/backup/verify.py",
    "aletheia/cli/observe_cmd.py",
    "aletheia/config/schema.py",
    "aletheia/counterfactual/__init__.py",
    "aletheia/learning/experiments.py",
    "aletheia/learning/ledger.py",
    "aletheia/learning/policy_epochs.py",
    "aletheia/maintenance/data_retention_backfill.py",
    "aletheia/observation_lab/checkpoint.py",
    "aletheia/observation_lab/db.py",
    "aletheia/observation_lab/exposure.py",
    "aletheia/observation_lab/migration.py",
    "aletheia/server/routes/data.py",
    "aletheia/server/routes/settings.py",
    "aletheia/storage/migrations.py",
    "aletheia/storage/repositories.py",
    "aletheia/update/continuity.py",
})

# Existing physical writers and mutation call sites only. A path being listed
# is temporary migration debt, not general permission to introduce new legacy
# operations or tables.
#
# Trimmed by 10 rows: after #2945 no aletheia/ module writes the legacy Experiment
# ledger at all. learning/ledger.py stays out of this list while remaining in the
# reference list above, because its two remaining touches are SELECTs.
_WRITER_ALLOWLIST = frozenset({
    "aletheia/agent_runtime/scheduler.py",
    "aletheia/maintenance/data_retention_backfill.py",
    "aletheia/storage/migrations.py",
    "aletheia/storage/repositories.py",
})


_TREATMENT_APIS = ("apply_numeric_treatment", "apply_boolean_treatment")


_SQL_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    r"\s+[\"'`\[]?(?P<table>"
    + "|".join(sorted(_LEGACY_TABLES))
    + r")\b",
    re.IGNORECASE,
)

_NAMED_LEGACY_WRITE_CALLS = frozenset({
    "insert_experiment",
    "insert_exposure",
    "insert_contribution_verdict",
    "insert_filter_shadow_experiment",
    "update_filter_shadow_experiment",
    "prune_filter_shadow_experiments",
    "_persist_attempt_learning_exposures",
})

def _production_sources() -> dict[str, str]:
    return {
        path.relative_to(_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in _SRC.rglob("*.py")
    }


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _constant_keyword(node: ast.Call, name: str) -> object | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _declares_legacy_dispatch(node: ast.Call) -> bool:
    """Recognise a table-specific dynamic retention dispatch from its plan row."""
    return (
        _call_name(node) == "RetentionTarget"
        and _constant_keyword(node, "table") in _LEGACY_TABLES
        and _constant_keyword(node, "prune_fn") in _NAMED_LEGACY_WRITE_CALLS
    )


def _discover_legacy_writers(sources: dict[str, str]) -> set[str]:
    writers: set[str] = set()
    for path, source in sources.items():
        if _SQL_WRITE.search(source):
            writers.add(path)
            continue
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            if name in _NAMED_LEGACY_WRITE_CALLS or _declares_legacy_dispatch(node):
                writers.add(path)
                break
            if (
                name == "insert_record"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in _LEGACY_TABLES
            ):
                writers.add(path)
                break
    return writers


def _assert_exact_inventory(
    *,
    label: str,
    discovered: set[str],
    allowed: frozenset[str],
) -> None:
    unexpected = discovered - allowed
    stale = allowed - discovered
    assert not unexpected and not stale, (
        f"{label} inventory drift: "
        f"unexpected={sorted(unexpected)}, stale={sorted(stale)}. "
        "Update #2737's exact migration debt ceiling with the code change."
    )


@spec_claim(SpecClaim(
    "OEO-S01", _CONTRACT, "Frozen legacy storage inventory", "L2a",
    "Phase 1 freezes all current production references to the legacy Experiment tables and rejects new coupling.",
))
def test_legacy_experiment_reference_inventory_cannot_grow():
    discovered = {
        path
        for path, source in _production_sources().items()
        if any(table in source for table in _LEGACY_TABLES)
    }
    _assert_exact_inventory(
        label="LEGACY EXPERIMENT REFERENCE",
        discovered=discovered,
        allowed=_REFERENCE_ALLOWLIST,
    )




@spec_claim(SpecClaim(
    "OEO-S03", _CONTRACT, "Verification and rollback", "L2a",
    "No production module outside the temporary writer allowlist may add a legacy Experiment-table write.",
))
def test_no_new_legacy_experiment_writer():
    sources = _production_sources()
    _assert_exact_inventory(
        label="LEGACY EXPERIMENT WRITER",
        discovered=_discover_legacy_writers(sources),
        allowed=_WRITER_ALLOWLIST,
    )


@spec_claim(SpecClaim(
    "OEO-S04", _CONTRACT, "Lab runtime independence", "L2a",
    "The canonical Lab runner must not import the legacy Learning Experiment runtime.",
))
def test_lab_runner_does_not_import_legacy_experiment_runtime():
    for module in ("runner.py", "evaluator.py"):
        source = (_SRC / "observation_lab" / module).read_text(encoding="utf-8")
        assert "aletheia.learning.experiments" not in source
        assert "aletheia.learning.ledger" not in source


@spec_claim(SpecClaim(
    "OEO-S06", _CHECKPOINT_CONTRACT, "Bounded transport progress", "L2a",
    "Only settlement and the updater diagnostic may recognize pending-only Main progress; deployment remains strict.",
))
def test_only_settlement_and_update_diagnostic_accept_main_outbox_progress():
    callers = {
        path
        for path, source in _production_sources().items()
        if "allow_main_outbox_progress=True" in source
    }
    assert callers == {
        "aletheia/observation_lab/runner.py",
        "aletheia/update/continuity.py",
    }


@spec_claim(SpecClaim(
    "OEO-S07", _CHECKPOINT_CONTRACT, "Pending transport frontier", "L2a",
    "Checkpoint identity excludes acknowledged retention rows while preserving the logical Main watermark.",
))
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


@spec_claim(SpecClaim(
    "OEO-S05", _CONTRACT, "Executable V2 boundary", "L2a",
    "Mechanism-aware Entry Learning is one Lab adapter with empty control and an explicit interference key.",
))
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
    assert (ENTRY_LEARNING_ADAPTER_ID, ENTRY_LEARNING_ADAPTER_VERSION) in (
        registered_treatment_adapters()
    )
    adapter = EntryLearningTreatmentAdapter()
    assert adapter.deterministic_baseline({}) == {"include_learning_context": False}
    assert ENTRY_LEARNING_INTERFERENCE_KEY == "mechanism-aware-grounded-entry"
