"""Executable Phase-1 debt ceiling for Observation Experiment Ownership V2."""

from __future__ import annotations

import re
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "observation_experiment_ownership_v2"
_ROOT = Path(__file__).resolve().parents[2]
_SRC = _ROOT / "aletheia"

_LEGACY_TABLES = frozenset({
    "learning_experiments",
    "learning_exposures",
    "learning_contribution_verdicts",
    "filter_shadow_experiments",
})

# Exact Phase-1 inventory of production modules that mention one of the four
# legacy Experiment tables. This is a debt ceiling: later migration phases may
# shrink it, but new entries require explicit contract review.
_REFERENCE_ALLOWLIST = frozenset({
    "aletheia/agent_runtime/scheduler.py",
    "aletheia/analytics/alpha_attribution.py",
    "aletheia/analytics/profit_gradient_evidence.py",
    "aletheia/cli/data_cmd.py",
    "aletheia/cli/observe_cmd.py",
    "aletheia/config/schema.py",
    "aletheia/counterfactual/__init__.py",
    "aletheia/counterfactual/filter_experiments.py",
    "aletheia/learning/authority.py",
    "aletheia/learning/cohorts.py",
    "aletheia/learning/contribution_runner.py",
    "aletheia/learning/economic_knowledge.py",
    "aletheia/learning/experiments.py",
    "aletheia/learning/fixed_look.py",
    "aletheia/learning/hypothesis_compiler.py",
    "aletheia/learning/knowledge_yield.py",
    "aletheia/learning/ledger.py",
    "aletheia/learning/lifecycle.py",
    "aletheia/learning/maintenance.py",
    "aletheia/learning/policy_epochs.py",
    "aletheia/learning/read_models.py",
    "aletheia/maintenance/data_retention_backfill.py",
    "aletheia/observation_lab/checkpoint.py",
    "aletheia/observation_lab/db.py",
    "aletheia/observation_lab/exposure.py",
    "aletheia/observation_lab/runner.py",
    "aletheia/server/routes/analytics.py",
    "aletheia/server/routes/data.py",
    "aletheia/server/routes/settings.py",
    "aletheia/storage/migrations.py",
    "aletheia/universe/service.py",
    "aletheia/update/continuity.py",
})

# Existing physical writers and mutation call sites only. A path being listed
# is temporary migration debt, not general permission to introduce new legacy
# operations or tables.
_WRITER_ALLOWLIST = frozenset({
    "aletheia/agent_runtime/scheduler.py",
    "aletheia/counterfactual/filter_experiments.py",
    "aletheia/learning/contribution_runner.py",
    "aletheia/learning/experiments.py",
    "aletheia/learning/fixed_look.py",
    "aletheia/learning/hypothesis_compiler.py",
    "aletheia/learning/ledger.py",
    "aletheia/learning/lifecycle.py",
    "aletheia/learning/maintenance.py",
    "aletheia/maintenance/data_retention_backfill.py",
    "aletheia/storage/migrations.py",
    "aletheia/universe/service.py",
})

_SQL_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    r"\s+[\"'`\[]?(?P<table>"
    + "|".join(sorted(_LEGACY_TABLES))
    + r")\b",
    re.IGNORECASE,
)

_CALL_WRITE = re.compile(
    r"\b(?:insert_record|insert_experiment|insert_exposure|insert_contribution_verdict|"
    r"insert_filter_shadow_experiment|update_filter_shadow_experiment|"
    r"prune_filter_shadow_experiments)\s*\(",
)


def _production_sources() -> dict[str, str]:
    return {
        path.relative_to(_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in _SRC.rglob("*.py")
    }


@spec_claim(SpecClaim(
    "OEO-S01", _CONTRACT, "§3 current legacy ownership inventory and freeze", "L2a",
    "Phase 1 freezes all current production references to the legacy Experiment tables and rejects new coupling.",
))
def test_legacy_experiment_reference_inventory_cannot_grow():
    discovered = {
        path
        for path, source in _production_sources().items()
        if any(table in source for table in _LEGACY_TABLES)
    }
    unexpected = discovered - _REFERENCE_ALLOWLIST
    assert not unexpected, (
        "NEW LEGACY EXPERIMENT REFERENCE(S): "
        f"{sorted(unexpected)}. Use ObservationHandler/TreatmentAdapter and Lab V2, "
        "or update #2737's migration contract before changing the debt ceiling."
    )


@spec_claim(SpecClaim(
    "OEO-S02", _CONTRACT, "§3 current legacy ownership inventory and freeze", "L2a",
    "No production module outside the temporary writer allowlist may add a legacy Experiment-table write.",
))
def test_no_new_legacy_experiment_writer():
    writers: set[str] = set()
    for path, source in _production_sources().items():
        mentions_legacy_table = any(table in source for table in _LEGACY_TABLES)
        if _SQL_WRITE.search(source) or (mentions_legacy_table and _CALL_WRITE.search(source)):
            writers.add(path)

    unexpected = writers - _WRITER_ALLOWLIST
    assert not unexpected, (
        "NEW LEGACY EXPERIMENT WRITER(S): "
        f"{sorted(unexpected)}. New Experiment state belongs to Observation Lab; "
        "the Phase-1 allowlist may shrink but must not grow without contract review."
    )


def test_legacy_experiment_allowlists_only_name_existing_production_modules():
    missing = {
        path
        for path in _REFERENCE_ALLOWLIST | _WRITER_ALLOWLIST
        if not (_ROOT / path).is_file()
    }
    assert not missing, f"STALE LEGACY EXPERIMENT INVENTORY: {sorted(missing)}"
