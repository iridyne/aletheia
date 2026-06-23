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
    """FORBIDDEN_INTENTS contains the 7 contractually required items."""
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
