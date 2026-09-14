"""Architecture fitness functions — L2a structural invariants.

Checks dependency direction, symbol existence, and safety defaults
without executing business code. Based on Building Evolutionary Architectures.
"""

import ast
import inspect
from pathlib import Path

import pytest

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


def _assert_risk_writer_boundary(root: Path):
    from tests.authority_writer_checks import assert_writer_boundary

    assert_writer_boundary(
        root, table="risk_decisions",
        owners={
            "insert_risk_decision": {"risk/engine.py"},
            "insert_authoritative_risk_decision": {"risk/engine.py"},
        },
        raw_owners={"insert_risk_decision"},
        delegations={("insert_authoritative_risk_decision", "insert_risk_decision")},
    )


def test_risk_decisions_have_one_application_writer():
    _assert_risk_writer_boundary(PROJECT_ROOT)


@pytest.mark.parametrize("directory", ["aletheia", "extensions", "legacy", "ops", "scripts"])
@pytest.mark.parametrize("source", [
    "from aletheia.storage.repositories import insert_risk_decision as write",
    "repo.insert_authoritative_risk_decision(decision)",
    "repo._insert('risk_decisions', row)",
    "repo._insert(table='risk_decisions', data=row)",
    "persist = repo.insert_risk_decision\npersist(row)",
    "db.execute('INSERT INTO main.risk_decisions VALUES (?)')",
    "db.execute('UPDATE main.\"risk_decisions\" SET approved=1')",
    "db.execute('DELETE FROM \"main\".[risk_decisions]')",
    "db.execute('INSERT INTO \"risk_decisions\" VALUES (?)')",
    "db.execute('UPDATE [risk_decisions] SET approved=1')",
    "db.execute('DELETE FROM `risk_decisions`')",
    "db.execute('INSERT OR IGNORE INTO risk_decisions VALUES (?)')",
    "db.execute('update risk_decisions SET approved=1')",
    "db.execute('DELETE FROM risk_decisions')",
])
def test_risk_writer_guard_rejects_non_owner(tmp_path, directory, source):
    path = tmp_path / directory / "other.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_risk_writer_boundary(tmp_path)


@pytest.mark.parametrize("path,source", [
    ("aletheia/other/module.py", "from ..storage.repositories import insert_risk_decision as persist\npersist(row)"),
    ("aletheia/storage/repositories.py", "def rewrite(row):\n    _update('risk_decisions', row['id'], row)"),
    ("aletheia/storage/repositories.py", "def rewrite(row):\n    db.execute('DELETE FROM risk_decisions')"),
    ("aletheia/storage/repositories.py", "def rewrite(row):\n    insert_risk_decision(row)"),
    ("extensions/other.py", "db.execute('INSERT /* hidden */ INTO risk_decisions VALUES (?)')"),
    ("extensions/other.py", "db.execute('UPDATE OR IGNORE risk_decisions SET approved=1')"),
    ("aletheia/risk/engine.py", "db.execute('DROP TABLE risk_decisions')"),
    ("extensions/other.py", "from aletheia.storage.repositories import _insert as save\nsave('risk_decisions', row)"),
    ("aletheia/storage/repositories.py", "def outer():\n    def insert_risk_decision(row):\n        _insert('risk_decisions', row)"),
    ("aletheia/storage/repositories.py", "def insert_risk_decision(row):\n    pass\n@decorate(_insert('risk_decisions', row))\ndef unrelated():\n    pass"),
    ("extensions/other.py", "from aletheia.storage.repositories import *\ninsert_risk_decision(row)"),
    ("aletheia/risk/engine.py", "from aletheia.storage.repositories import insert_risk_decision as exported_writer"),
    ("aletheia/storage/repositories.py", "@decorate(_insert('risk_decisions', row))\ndef insert_risk_decision(row):\n    pass"),
])
def test_risk_writer_guard_rejects_relative_alias_and_new_storage_owner(tmp_path, path, source):
    target = tmp_path / path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_risk_writer_boundary(tmp_path)


def test_risk_writer_guard_accepts_canonical_storage_and_risk_import(tmp_path):
    sources = {
        "aletheia/risk/engine.py": "def evaluate(row):\n    from ..storage.repositories import insert_risk_decision as persist\n    persist(row)",
        "aletheia/storage/repositories.py": "def insert_risk_decision(row):\n    _insert(table='risk_decisions', data=row)\ndef insert_authoritative_risk_decision(row):\n    insert_risk_decision(row)",
        "aletheia/storage/migrations.py": "db.execute('UPDATE [risk_decisions] SET approved=0')",
    }
    for path, source in sources.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    _assert_risk_writer_boundary(tmp_path)


def test_scheduler_does_not_own_order_or_close_claim_writes():
    """Scheduling observed evidence must delegate its durable execution claim."""
    tree = ast.parse((SRC / "agent_runtime/scheduler.py").read_text(encoding="utf-8"))
    writers = {"insert_order", "claim_position_close"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not writers.intersection(alias.name for alias in node.names), node.lineno
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
            assert name not in writers, node.lineno


def test_fill_writes_stay_with_exchange_materialization():
    """Keep observed-close and native Fill writes under the same Core owner."""
    import re

    owner = SRC / "execution" / "exchange_materialization.py"
    storage = {SRC / "storage" / "repositories.py", SRC / "storage" / "migrations.py"}
    fill_sql = re.compile(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+fills\b", re.I)
    for root in (SRC, PROJECT_ROOT / "extensions", PROJECT_ROOT / "legacy", PROJECT_ROOT / "ops", PROJECT_ROOT / "scripts"):
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "aletheia.storage.repositories":
                    if any(alias.name == "insert_fill" for alias in node.names):
                        assert path == owner, (path, node.lineno)
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
                    if name == "insert_fill":
                        assert path == owner, (path, node.lineno)
                    if name in {"_insert", "_insert_many", "_update"} and node.args:
                        table = node.args[0]
                        if isinstance(table, ast.Constant) and table.value == "fills":
                            assert path in storage, (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and fill_sql.search(node.value):
                    assert path in storage | {owner}, (path, node.lineno)


def test_candidate_snapshot_creation_has_one_core_owner():
    import re
    from aletheia.storage import repositories as repo

    retired = {"insert_universe_snapshot", "insert_universe_member", "insert_universe_members_batch",
               "insert_candidate", "publish_serenity_candidates"}
    assert not retired.intersection(vars(repo))
    writes = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not re.search(r"\b(?:INSERT|REPLACE)\s+(?:OR\s+\w+\s+)?INTO\s+(?:universe_members|universe_snapshots)\b", node.value, re.I), (path, node.lineno)
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else None
            table = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
            if name not in {"_insert", "_insert_many"} or table not in {"universe_members", "universe_snapshots"}:
                continue
            parent = parents.get(node)
            while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                parent = parents.get(parent)
            writes.add((str(path.relative_to(SRC)), parent.name if parent else None, table))
    assert writes == {
        ("storage/repositories.py", "publish_candidate_snapshot", "universe_snapshots"),
        ("storage/repositories.py", "publish_candidate_snapshot", "universe_members"),
    }
    assert "aletheia.trading.serenity_publication" not in _get_imports(SRC / "storage" / "repositories.py")


def test_gateway_startup_has_no_bulk_secret_migration():
    """Startup must not enumerate credentials or hide a historical repair."""
    app_tree = ast.parse((SRC / "server" / "app.py").read_text(encoding="utf-8"))
    lifespan = next(node for node in app_tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan")
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "aletheia.config.secrets"
        and any(alias.name in {"migrate_keyring_to_file", "get_secret", "set_secret", "_get_keyring"} for alias in node.names)
        for node in ast.walk(lifespan)
    )
    secrets_tree = ast.parse((SRC / "config" / "secrets.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.FunctionDef) and node.name == "migrate_keyring_to_file"
        or isinstance(node, ast.Attribute) and node.attr == "CredEnumerate"
        for node in ast.walk(secrets_tree)
    )


def test_legacy_hook_cleanup_is_explicit_doctor_work_only():
    callers = set()
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "remove_managed_git_hooks"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "remove_managed_git_hooks"
            ):
                callers.add(str(path.relative_to(SRC)))
    assert callers == {"diagnostics/doctor.py"}


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


def test_core_account_environment_uses_registered_execution_identity():
    """Core safety/economic consumers must not infer account class from suffixes."""
    from aletheia.config.exchanges import (
        DEMO_EXECUTION_MODES,
        EXECUTION_MODE_ENVIRONMENTS,
        LIVE_EXECUTION_MODE_SET,
    )
    from aletheia.risk.policy import (
        DEMO_EXECUTION_MODES as RISK_DEMO_EXECUTION_MODES,
        LIVE_EXECUTION_MODES as RISK_LIVE_EXECUTION_MODES,
    )

    assert RISK_DEMO_EXECUTION_MODES is DEMO_EXECUTION_MODES
    assert RISK_LIVE_EXECUTION_MODES is LIVE_EXECUTION_MODE_SET
    assert set(EXECUTION_MODE_ENVIRONMENTS) >= {
        "paper_internal",
        "binance_testnet",
        "okx_demo",
        "bitget_demo",
        "binance_live_limited",
        "okx_live_limited",
        "bitget_live_limited",
    }

    core_paths = (
        SRC / "server" / "routes" / "data.py",
        SRC / "portfolio" / "position_manager.py",
        SRC / "portfolio" / "pnl.py",
        SRC / "maintenance" / "repair_administrative_close.py",
        SRC / "agent_runtime" / "scheduler.py",
        SRC / "risk" / "engine.py",
    )
    for path in core_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        suffix_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "endswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"_demo", "_live_limited"}
        ]
        assert suffix_calls == [], f"{path.relative_to(PROJECT_ROOT)} infers account class from a suffix"


def test_entry_route_gates_use_registered_capabilities():
    """Generic Entry consumers must not hard-code the current venue route."""
    route = "bitget_demo"
    consumers = (
        SRC / "trading" / "entry_plan_compiler.py",
        SRC / "trading" / "entry_authority.py",
        SRC / "execution" / "entry_plan_execution.py",
        SRC / "execution" / "gateway.py",
        SRC / "risk" / "engine.py",
    )
    for path in consumers:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hardcoded = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(value, ast.Constant) and value.value == route
                for value in ast.walk(node)
            )
        ]
        assert hardcoded == [], f"{path.relative_to(PROJECT_ROOT)} hard-codes {route} in a route gate"


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


def test_learning_9_tables_not_in_prunable_whitelist():
    """AGENTS.md hard rule: the 9 learning tables must NEVER be prunable.

    They retain every nomination, hypothesis, experiment, exposure, contribution
    verdict, fixed-look decision, lifecycle event, job-health record, and policy
    epoch. Losing rows breaks causal replay and generation-cost accounting.
    """
    from aletheia.storage.repositories import _PRUNABLE

    learning_tables = {
        "learning_experiments",
        "learning_exposures",
        "learning_hypotheses",
        "learning_hypothesis_nominations",
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
