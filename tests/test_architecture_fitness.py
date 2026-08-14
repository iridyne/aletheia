"""Architecture fitness functions — L2a structural invariants.

Checks dependency direction, symbol existence, and safety defaults
without executing business code. Based on Building Evolutionary Architectures.
"""

import ast
import inspect
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC = PROJECT_ROOT / "aletheia"


def _get_imports(path: Path) -> set[str]:
    """Extract all import module names from a Python file via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


# ---------------------------------------------------------------------------
# Dependency direction rules
# ---------------------------------------------------------------------------


def test_risk_engine_does_not_import_agents():
    """Risk Engine must not depend on agents/ (unidirectional data flow)."""
    imports = _get_imports(SRC / "risk" / "engine.py")
    violations = [i for i in imports if i.startswith("aletheia.agents")]
    assert violations == [], f"ARCH VIOLATION: risk/engine.py imports agents/: {violations}"


def test_risk_engine_does_not_import_agent_shell():
    """Risk Engine must not depend on agent_shell/."""
    imports = _get_imports(SRC / "risk" / "engine.py")
    violations = [i for i in imports if "agent_shell" in i]
    assert violations == [], f"ARCH VIOLATION: risk/engine.py imports agent_shell/: {violations}"


def test_execution_gateway_does_not_import_agent_shell():
    """Execution Gateway must not depend on agent_shell/."""
    imports = _get_imports(SRC / "execution" / "gateway.py")
    violations = [i for i in imports if "agent_shell" in i]
    assert violations == [], f"ARCH VIOLATION: gateway.py imports agent_shell/: {violations}"


def test_agent_shell_does_not_import_exchange_auth():
    """agent_shell cannot directly import exchange auth or secrets."""
    forbidden = {"aletheia.exchanges.bitget.auth", "aletheia.config.secrets"}
    for name in ("permissions.py", "registry.py", "runtime.py"):
        path = SRC / "agent_shell" / name
        if not path.exists():
            continue
        imports = _get_imports(path)
        violations = [i for i in imports if any(i.startswith(f) for f in forbidden)]
        assert violations == [], (
            f"ARCH VIOLATION: agent_shell/{name} imports trust-boundary: {violations}"
        )


# ---------------------------------------------------------------------------
# Symbol existence & completeness
# ---------------------------------------------------------------------------


def test_forbidden_intents_complete():
    """FORBIDDEN_INTENTS contains the 7 core contractually required items.

    (Subset check — the set also includes the 2 OL-C03 double-lock additions
    deactivate_kill_switch/modify_permissions since 2026-06-23; this test only
    asserts the 7 core items are present, so additions never break it.)
    """
    from aletheia.agent_shell.registry import FORBIDDEN_INTENTS

    required = frozenset([
        "enable_live", "modify_credentials", "bypass_risk",
        "place_live_order", "modify_execution_gateway",
        "delete_knowledge", "change_risk_limits",
    ])
    missing = required - FORBIDDEN_INTENTS
    assert not missing, f"FORBIDDEN_INTENTS missing: {missing}"


def test_restricted_tools_complete():
    """RESTRICTED_TOOLS contains the 5 contractually required tools."""
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS

    required = {
        "unlock_live", "place_live_order", "change_hard_risk_limits",
        "modify_credentials", "modify_execution_gateway",
    }
    missing = required - set(RESTRICTED_TOOLS)
    assert not missing, f"RESTRICTED_TOOLS missing: {missing}"


def test_risk_engine_eval_lock_exists():
    """_eval_lock threading.Lock exists in risk/engine (TOCTOU prevention)."""
    import threading
    import aletheia.risk.engine as eng

    assert hasattr(eng, "_eval_lock")
    assert isinstance(eng._eval_lock, type(threading.Lock()))


# ---------------------------------------------------------------------------
# Data retention invariants
# ---------------------------------------------------------------------------


def test_causal_tables_not_in_prunable_whitelist():
    """Trust-bearing tables must NEVER appear in _PRUNABLE whitelist."""
    from aletheia.storage.repositories import _PRUNABLE

    causal = {
        "trade_proposals", "risk_decisions", "orders", "fills",
        "positions", "audit_events", "knowledge_items",
    }
    violations = causal & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: causal tables in _PRUNABLE: {violations}"
    )


def test_audit_chain_12_tables_not_in_prunable_whitelist():
    """AGENTS.md hard rule: the full audit-chain 12 must NEVER be prunable.

    Phase 3 retention pass added many new prune functions; this assertion
    guards the whitelist against accidentally including a trust-bearing table
    in a follow-up refactor.
    """
    from aletheia.storage.repositories import _PRUNABLE

    audit_chain_12 = {
        "audit_events",
        "fills",
        "capital_events",
        "orders",
        "positions",
        "proposals",
        "risk_decisions",
        "approvals",
        "knowledge_items",
        "trade_proposals",
        "ps_orders",
        "ps_fills",
        "ps_positions",
    }
    violations = audit_chain_12 & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: audit-chain-12 tables in _PRUNABLE: {violations}"
    )


def test_learning_8_tables_not_in_prunable_whitelist():
    """AGENTS.md hard rule: the 8 learning tables must NEVER be prunable.

    They are the persistent record of every hypothesis, experiment, exposure,
    contribution verdict and policy epoch. Losing rows breaks paired-cohort
    read reproducibility and epoch replay, so retention/prune paths must
    never touch them.
    """
    from aletheia.storage.repositories import _PRUNABLE

    learning_tables = {
        "learning_experiments",
        "learning_exposures",
        "learning_hypotheses",
        "learning_policy_epochs",
        "learning_contribution_verdicts",
        "learning_inference_decisions",
        "learning_lifecycle_events",
        "learning_job_health",
    }
    violations = learning_tables & set(_PRUNABLE.keys())
    assert violations == set(), (
        f"RETENTION VIOLATION: learning tables in _PRUNABLE: {violations}"
    )


def test_migration_schema_is_additive_only():
    """SCHEMA_SQL must not contain DROP TABLE / DROP COLUMN / RENAME TABLE."""
    from aletheia.storage.migrations import SCHEMA_SQL

    sql_upper = SCHEMA_SQL.upper()
    assert "DROP TABLE" not in sql_upper, "MIGRATION VIOLATION: DROP TABLE"
    assert "DROP COLUMN" not in sql_upper, "MIGRATION VIOLATION: DROP COLUMN"
    assert "RENAME TABLE" not in sql_upper, "MIGRATION VIOLATION: RENAME TABLE"


# ---------------------------------------------------------------------------
# Safety defaults
# ---------------------------------------------------------------------------


def test_live_config_default_is_disabled():
    """LiveConfig.state default must be 'live_disabled'."""
    from aletheia.config.schema import LiveConfig

    source = inspect.getsource(LiveConfig)
    assert "live_disabled" in source, (
        "CONTRACT VIOLATION: LiveConfig default state is not live_disabled"
    )


def test_kill_switch_module_has_required_interface():
    """kill_switch.py exposes is_active/activate/deactivate."""
    from aletheia.risk import kill_switch

    assert callable(getattr(kill_switch, "is_active", None))
    assert callable(getattr(kill_switch, "activate", None))
    assert callable(getattr(kill_switch, "deactivate", None))


# ---------------------------------------------------------------------------
# Trust-boundary coverage registry
#
# See docs/operations/development/TRUST_BOUNDARY_COVERAGE.md and
# scripts/coverage_floors.py. If a new module is created on one of the
# canonical trust-boundary paths (Profit Gradient, epoch/cutoff, artifact
# compiler, replay, cost reconciliation) it MUST be registered in
# .coveragerc's include= list, otherwise a PR could grow silently
# unmeasured code on a path whose wrong answer misleads a decision,
# corrupts state, or bypasses a check.
#
# Registration is the contract; the floor in coverage_floors.py can be
# added or raised in a follow-up.
# ---------------------------------------------------------------------------


def test_trust_boundary_files_are_registered_in_coveragerc():
    """Every file on a canonical trust-boundary path must be listed in
    ``.coveragerc``'s ``include=`` block. Missing the registration means
    a silent hole in the coverage gate.
    """
    import fnmatch
    import re

    repo_root = Path(__file__).resolve().parent.parent
    coveragerc = (repo_root / ".coveragerc").read_text()

    # Parse the ``include =`` block. coverage.py accepts either
    # ``aletheia/...`` or ``*/aletheia/...``; we accept both by stripping
    # a leading ``*/``.
    run_section = re.search(r"\[run\](.*?)(?:\n\[|\Z)", coveragerc, re.S)
    assert run_section is not None, ".coveragerc is missing a [run] section"
    include_match = re.search(
        r"^include\s*=\s*\n((?:[ \t]+\S.*\n?)+)",
        run_section.group(1),
        re.M,
    )
    assert include_match is not None, ".coveragerc [run] has no include= block"
    registered = {
        line.strip().removeprefix("*/")
        for line in include_match.group(1).splitlines()
        if line.strip()
    }

    # Canonical trust-boundary path patterns. Any *.py file inside
    # aletheia/ whose repo-relative path matches one of these globs is a
    # trust boundary and must be registered. Patterns are anchored on
    # ``aletheia/...``.
    patterns = (
        "aletheia/analytics/profit_gradient*.py",
        "aletheia/execution/reconciliation.py",
        "aletheia/economics/context_artifacts.py",
        "aletheia/learning/*epoch*.py",
        "aletheia/learning/policy_epochs.py",
        # Replay lives under two subpackages today; both count.
        "aletheia/analytics/*replay*.py",
        "aletheia/counterfactual/*replay*.py",
    )

    aletheia_root = repo_root / "aletheia"
    unregistered: list[str] = []
    for path in aletheia_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(repo_root).as_posix()
        if any(fnmatch.fnmatchcase(rel, pat) for pat in patterns):
            if rel not in registered:
                unregistered.append(rel)

    assert not unregistered, (
        "TRUST-BOUNDARY REGISTRATION MISSING:\n  "
        + "\n  ".join(unregistered)
        + "\n\nAdd each path to .coveragerc's include= block and set a floor "
        "in scripts/coverage_floors.py. See "
        "docs/operations/development/TRUST_BOUNDARY_COVERAGE.md."
    )


def test_coverage_floors_match_coveragerc_registry():
    """Every include= entry in .coveragerc must have a floor, and every
    floor must correspond to a registered path. Prevents drift between
    what's measured and what's gated.
    """
    import re
    import sys

    repo_root = Path(__file__).resolve().parent.parent
    coveragerc = (repo_root / ".coveragerc").read_text()
    run_section = re.search(r"\[run\](.*?)(?:\n\[|\Z)", coveragerc, re.S)
    include_match = re.search(
        r"^include\s*=\s*\n((?:[ \t]+\S.*\n?)+)",
        run_section.group(1),
        re.M,
    )
    registered = {
        line.strip().removeprefix("*/")
        for line in include_match.group(1).splitlines()
        if line.strip()
    }

    sys.path.insert(0, str(repo_root / "scripts"))
    try:
        import coverage_floors  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    floors = set(coverage_floors.FLOORS.keys())
    missing_floor = registered - floors
    orphan_floor = floors - registered
    assert not missing_floor and not orphan_floor, (
        f"coverage registry drift: "
        f"registered-but-no-floor={sorted(missing_floor)}, "
        f"floor-but-not-registered={sorted(orphan_floor)}"
    )
