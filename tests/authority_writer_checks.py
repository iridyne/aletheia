"""Bounded static checks shared by declared storage-writer inventories."""

import ast
from importlib.util import resolve_name
import re


_SQL_COMMENTS = re.compile(r'''"(?:""|[^"])*"|'(?:''|[^'])*'|`(?:``|[^`])*`|\[[^\]]*\]|(?P<comment>/\*.*?\*/|--[^\r\n]*)''', re.S)


def _without_sql_comments(sql):
    return _SQL_COMMENTS.sub(lambda match: " " if match.group("comment") else match.group(), sql)


def assert_writer_boundary(root, *, table, owners, raw_owners, delegations=()):
    """Bounded named-reference/literal-write proof, not arbitrary dynamic SQL."""
    sql_pattern = re.compile(rf'''\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+(?:(?:"[^"]+"|'[^']+'|`[^`]+`|\[[^\]]+\]|\w+)\s*\.\s*)?(?:"{re.escape(table)}"|'{re.escape(table)}'|`{re.escape(table)}`|\[{re.escape(table)}\]|{re.escape(table)}\b)''', re.I)
    repository = root / "aletheia/storage/repositories.py"
    migrations = root / "aletheia/storage/migrations.py"
    for directory in ("aletheia", "extensions", "legacy", "ops", "scripts"):
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            canonical = {}
            if path == repository:
                for name in raw_owners | {caller for caller, _ in delegations}:
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
                if isinstance(reference, (ast.Assign, ast.AnnAssign)):
                    value = reference.value
                    name = value.attr if isinstance(value, ast.Attribute) else generic_aliases.get(value.id) if isinstance(value, ast.Name) else None
                    if name in {"_insert", "_insert_many", "_update"}:
                        for target in reference.targets if isinstance(reference, ast.Assign) else [reference.target]:
                            if isinstance(target, ast.Name):
                                generic_aliases[target.id] = name
            for node in ast.walk(tree):
                child = node
                parent = parents.get(node)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    child = parent
                    parent = parents.get(parent)
                function = getattr(parent, "name", None)
                canonical_function = parent is not None and canonical.get(function) is parent and child in parent.body

                def check_writer(name):
                    if name not in owners:
                        return
                    permitted = {root / "aletheia" / owner for owner in owners[name]}
                    delegation = canonical_function and (function, name) in delegations
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
                        table_arg = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "table"), None)
                        if isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str) and sql_pattern.search(_without_sql_comments("UPDATE " + table_arg.value)):
                            assert path == migrations or (canonical_function and function in raw_owners), (path, node.lineno)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and sql_pattern.search(_without_sql_comments(node.value)):
                    assert path == migrations or (canonical_function and function in raw_owners), (path, node.lineno)
