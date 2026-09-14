"""Named Order writers and literal SQL stay within declared Core owners."""

import ast
from importlib.util import resolve_name
from pathlib import Path
import re

import pytest


_OWNERS = {
    "insert_order": {"execution/coordinator.py", "execution/exchange_materialization.py", "portfolio/position_reconciler.py"},
    "transition_order": {"execution/coordinator.py", "execution/exchange_materialization.py", "execution/reconciliation.py", "execution/gateway.py", "execution/pending_close_recovery.py", "execution/stale_order_cleanup.py"},
    "update_order_metadata": {"execution/coordinator.py", "execution/exchange_materialization.py", "execution/reconciliation.py"},
    "replace_order_error_reason": {"execution/pending_close_recovery.py"},
    "finalize_exchange_acknowledgement": {"execution/coordinator.py"},
}
_RAW_OWNERS = {"insert_order", "transition_order", "update_order_metadata", "replace_order_error_reason"}
_ORDER_SQL = re.compile(r'''\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+(?:(?:"[^"]+"|'[^']+'|`[^`]+`|\[[^\]]+\]|\w+)\s*\.\s*)?(?:"orders"|'orders'|`orders`|\[orders\]|orders\b)''', re.I)


def _assert_order_writer_boundary(root):
    """Bounded named-reference/literal-write proof, not arbitrary dynamic SQL."""
    repository = root / "aletheia/storage/repositories.py"
    migrations = root / "aletheia/storage/migrations.py"
    for directory in ("aletheia", "extensions", "legacy", "ops", "scripts"):
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            canonical = {}
            if path == repository:
                for name in _RAW_OWNERS | {"finalize_exchange_acknowledgement"}:
                    definitions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
                    if definitions:
                        assert len(definitions) == 1 and parents[definitions[0]] is tree, (path, name)
                        canonical[name] = definitions[0]
            generic_aliases = {}
            for reference in ast.walk(tree):
                if isinstance(reference, ast.ImportFrom) and (reference.module or "").endswith("repositories"):
                    for alias in reference.names:
                        if alias.name in {"_insert", "_insert_many", "_update"}:
                            generic_aliases[alias.asname or alias.name] = alias.name
                if isinstance(reference, ast.Assign):
                    value = reference.value
                    name = value.attr if isinstance(value, ast.Attribute) else generic_aliases.get(value.id) if isinstance(value, ast.Name) else None
                    if name in {"_insert", "_insert_many", "_update"}:
                        for target in reference.targets:
                            if isinstance(target, ast.Name):
                                generic_aliases[target.id] = name
            for node in ast.walk(tree):
                parent = parents.get(node)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    parent = parents.get(parent)
                function = getattr(parent, "name", None)
                canonical_function = parent is not None and canonical.get(function) is parent

                def check_writer(name):
                    if name not in _OWNERS:
                        return
                    permitted = {root / "aletheia" / owner for owner in _OWNERS[name]}
                    delegation = canonical_function and function == "finalize_exchange_acknowledgement" and name == "transition_order"
                    assert path in permitted or delegation, (path, node.lineno, name)

                if isinstance(node, ast.Attribute):
                    check_writer(node.attr)
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        module = resolve_name("." * node.level + module, ".".join(path.relative_to(root).parent.parts))
                    if module == "aletheia.storage.repositories":
                        assert all(alias.name != "*" for alias in node.names), (path, node.lineno)
                    for alias in node.names:
                        check_writer(alias.name)
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
                    name = generic_aliases.get(name, name)
                    check_writer(name)
                    if name in {"_insert", "_insert_many", "_update"}:
                        table = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "table"), None)
                        if isinstance(table, ast.Constant) and isinstance(table.value, str) and _ORDER_SQL.search("UPDATE " + table.value):
                            assert path == migrations or (canonical_function and function in _RAW_OWNERS), (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and _ORDER_SQL.search(node.value):
                    assert path == migrations or (canonical_function and function in _RAW_OWNERS), (path, node.lineno)


def test_order_writer_ownership():
    _assert_order_writer_boundary(Path(__file__).resolve().parents[1])


@pytest.mark.parametrize("directory", ["aletheia", "extensions", "legacy", "ops", "scripts"])
@pytest.mark.parametrize("source", [
    "from aletheia.storage.repositories import insert_order as persist\npersist(row)",
    "persist = repo.transition_order\npersist(order_id, status='filled')",
    "repo._insert(table='orders', data=row)",
    "repo._update('orders', order_id, {'status': 'filled'})",
    "from aletheia.storage.repositories import _insert as persist\npersist('orders', row)",
    "persist = repo._update\npersist(table='orders', rid=order_id, data=row)",
    "repo._update('ORDERS', order_id, row)",
    "repo._insert('\"orders\"', row)",
    "from aletheia.execution.coordinator import insert_order as persist\npersist(row)",
    "db.execute('UPDATE main.\"orders\" SET status=?')",
    "db.execute('DELETE FROM [orders]')",
    "db.execute('INSERT OR REPLACE INTO `orders` VALUES (?)')",
    "db.execute('UPDATE OR IGNORE orders SET status=?')",
    'db.execute("UPDATE \'orders\' SET status=?")',
])
def test_order_writer_guard_rejects_other_owners(tmp_path, directory, source):
    target = tmp_path / directory / "other.py"
    target.parent.mkdir()
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_order_writer_boundary(tmp_path)


@pytest.mark.parametrize("source", [
    "def rewrite(row):\n    _insert('orders', row)",
    "def rewrite(row):\n    db.execute('UPDATE orders SET status=?')",
    "def rewrite(row):\n    transition_order(row['id'], status='filled')",
    "class Other:\n    def insert_order(self, row):\n        _insert('orders', row)",
    "def insert_order(row):\n    pass\ndef insert_order(row):\n    _insert('orders', row)",
    "def other():\n    def insert_order(row):\n        _insert('orders', row)",
])
def test_new_repository_wrapper_is_not_an_order_owner(tmp_path, source):
    target = tmp_path / "aletheia/storage/repositories.py"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_order_writer_boundary(tmp_path)


def test_order_guard_accepts_canonical_owner_and_delegation(tmp_path):
    sources = {
        "aletheia/execution/coordinator.py": "from ..storage.repositories import insert_order as persist\npersist(row)",
        "aletheia/storage/repositories.py": "def insert_order(row):\n    _insert('orders', row)\ndef finalize_exchange_acknowledgement(row):\n    transition_order(row['id'], status='submitted')",
        "aletheia/storage/migrations.py": "db.execute('UPDATE [orders] SET status=?')",
    }
    for path, source in sources.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    _assert_order_writer_boundary(tmp_path)
