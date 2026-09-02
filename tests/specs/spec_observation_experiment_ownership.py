"""Executable Phase-1 debt ceiling for Observation Experiment Ownership V2."""

from __future__ import annotations

import ast
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
    "aletheia/cli/learning_cmd.py",
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
    "aletheia/storage/repositories.py",
    "aletheia/universe/service.py",
    "aletheia/update/continuity.py",
})

# Existing physical writers and mutation call sites only. A path being listed
# is temporary migration debt, not general permission to introduce new legacy
# operations or tables.
_WRITER_ALLOWLIST = frozenset({
    "aletheia/agent_runtime/scheduler.py",
    "aletheia/cli/observe_cmd.py",
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
    "aletheia/storage/repositories.py",
    "aletheia/universe/service.py",
})

_TREATMENT_CALLER_ALLOWLIST = frozenset({
    "aletheia/agents/paper_trader_ranker.py",
    "aletheia/analytics/factor_feedback.py",
    "aletheia/knowledge/guards.py",
    "aletheia/learning/economic_knowledge.py",
    "aletheia/learning/experiments.py",
    "aletheia/learning/prompt_bundles.py",
    "aletheia/risk/engine.py",
    "aletheia/trading/entry_plan_compiler.py",
    "aletheia/trading/sizing_policy.py",
    "aletheia/universe/allocator.py",
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
    "OEO-S02", _CONTRACT, "Frozen legacy treatment-call inventory", "L2a",
    "Phase 1 enumerates every legacy apply_numeric_treatment/apply_boolean_treatment reference and rejects new callers.",
))
def test_legacy_treatment_caller_inventory_cannot_grow():
    discovered = {
        path
        for path, source in _production_sources().items()
        if any(api in source for api in _TREATMENT_APIS)
    }
    _assert_exact_inventory(
        label="LEGACY TREATMENT CALLER",
        discovered=discovered,
        allowed=_TREATMENT_CALLER_ALLOWLIST,
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
