"""Bounded static checks shared by declared storage-writer inventories."""

import ast
from functools import lru_cache
from importlib.util import resolve_name
from pathlib import Path
import re


@lru_cache(maxsize=None)
def _parse_cached(path: str, mtime_ns: int, size: int) -> ast.Module:
    """Parse a source file once per (path, mtime, size).

    Every ``assert_writer_boundary`` call re-walks the whole tree, and the
    writer-boundary tests call it once per owned table across four test modules.
    Without this cache each call re-parses ~1.5k files (~25s of the tier_unit
    wall clock in 2026-09). The stamp keeps a rewritten file from being served
    stale.
    """
    return ast.parse(Path(path).read_text(encoding="utf-8"))


_SQL_COMMENTS = re.compile(r'''"(?:""|[^"])*"|'(?:''|[^'])*'|`(?:``|[^`])*`|\[[^\]]*\]|(?P<comment>/\*.*?\*/|--[^\r\n]*)''', re.S)
_GENERIC_WRITERS = {"_insert", "_insert_many", "_update", "_delete_by_rowid_batches"}


def _without_sql_comments(sql):
    return _SQL_COMMENTS.sub(lambda match: " " if match.group("comment") else match.group(), sql)


def _alias_names(value):
    # Calls produce values, not statically resolved aliases; dynamic results are out of scope.
    if value is None or isinstance(value, ast.Call):
        return set()
    names = {value.id} if isinstance(value, ast.Name) else {value.attr} if isinstance(value, ast.Attribute) else set()
    for child in ast.iter_child_nodes(value):
        names.update(_alias_names(child))
    return names


def _sql_text(node):
    """Join static SQL fragments; unknown expressions remain explicit gaps."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sql_text(node.left) + _sql_text(node.right)
    if isinstance(node, ast.JoinedStr):
        return "".join(_sql_text(value) for value in node.values)
    return "?"


def assert_writer_boundary(root, *, table, owners, raw_owners, delegations=(), literal_writes=None, expression_writes=None, allow_migration_dml=True, raw_operations=None, additive_migration_ddl=False):
    """Bounded named-reference/literal-write proof, not arbitrary dynamic SQL."""
    sql_pattern = re.compile(rf'''\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE(?:\s+OR\s+\w+)?|DELETE\s+FROM)\s+(?:(?:"[^"]+"|'[^']+'|`[^`]+`|\[[^\]]+\]|\w+)\s*\.\s*)?(?:"{re.escape(table)}"|'{re.escape(table)}'|`{re.escape(table)}`|\[{re.escape(table)}\]|{re.escape(table)}\b)''', re.I)
    repository = root / "core/aletheia/storage/repositories.py"
    migrations = root / "core/aletheia/storage/migrations.py"
    ddl_pattern = re.compile(rf'''\b(?:CREATE\s+(?:(?:TEMP|TEMPORARY|VIRTUAL)\s+)?TABLE(?:\s+IF\s+NOT\s+EXISTS)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?|ALTER\s+TABLE)\s+(?:(?:"[^"]+"|'[^']+'|`[^`]+`|\[[^\]]+\]|\w+)\s*\.\s*)?(?:"{re.escape(table)}"|'{re.escape(table)}'|`{re.escape(table)}`|\[{re.escape(table)}\]|{re.escape(table)}\b)''', re.I)
    scanned = 0
    for directory in ("core/aletheia", "extensions", "legacy", "ops", "scripts"):
        for path in (root / directory).rglob("*.py"):
            scanned += 1
            stat = path.stat()
            tree = _parse_cached(str(path), stat.st_mtime_ns, stat.st_size)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            canonical = {}
            allowed_literals = {
                function: statements
                for (owner_path, function), statements in (literal_writes or {}).items()
                if path == root / "core/aletheia" / owner_path
            }
            allowed_expressions = {
                function: {ast.dump(ast.parse(expression, mode="eval").body) for expression in expressions}
                for (owner_path, function), expressions in (expression_writes or {}).items()
                if path == root / "core/aletheia" / owner_path
            }
            canonical_names = set(allowed_literals) | set(allowed_expressions)
            if path == repository:
                canonical_names |= raw_owners | {caller for caller, _ in delegations}
            for name in canonical_names:
                definitions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
                if definitions:
                    assert len(definitions) == 1 and parents[definitions[0]] is tree, (path, name)
                    canonical[name] = definitions[0]
            for reference in ast.walk(tree):
                if isinstance(reference, ast.ImportFrom) and (reference.module or "").endswith("repositories"):
                    for alias in reference.names:
                        if alias.name in _GENERIC_WRITERS:
                            assert path == repository, (path, reference.lineno, "generic writer re-export")
                if isinstance(reference, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                    value = reference.value
                    aliases = _alias_names(value)
                    assert not aliases.intersection(owners), (path, reference.lineno, "named writer alias", aliases)
                    assert not aliases.intersection(_GENERIC_WRITERS), (path, reference.lineno, "generic writer alias")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Attribute, ast.ImportFrom, ast.Call, ast.Constant, ast.BinOp, ast.JoinedStr)):
                    continue
                child = node
                parent = parents.get(node)
                while parent is not None and not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    child = parent
                    parent = parents.get(parent)
                function = getattr(parent, "name", None)
                canonical_function = parent is not None and canonical.get(function) is parent and child in parent.body
                canonical_storage_writer = path == repository and canonical_function and function in raw_owners

                def check_writer(name):
                    if name not in owners:
                        return
                    permitted = {root / "core/aletheia" / owner for owner in owners[name]}
                    delegation = canonical_function and (function, name) in delegations
                    assert path in permitted or delegation, (path, node.lineno, name)

                if isinstance(node, ast.Attribute):
                    check_writer(node.attr)
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        import_root = root / "core" if directory == "core/aletheia" else root
                        module = resolve_name("." * node.level + module, ".".join(path.relative_to(import_root).parent.parts))
                    if module == "aletheia.storage.repositories":
                        assert all(alias.name != "*" for alias in node.names), (path, node.lineno)
                    for alias in node.names:
                        check_writer(alias.name)
                        if alias.name in owners and parent is None:
                            assert alias.asname in {None, alias.name}, (path, node.lineno, "writer re-export")
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
                    check_writer(name)
                    if name in _GENERIC_WRITERS:
                        table_arg = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "table"), None)
                        if isinstance(table_arg, ast.Constant) and isinstance(table_arg.value, str) and sql_pattern.search(_without_sql_comments("UPDATE " + table_arg.value)):
                            assert (path == migrations and allow_migration_dml) or canonical_storage_writer, (path, node.lineno)
                            if canonical_storage_writer and function in (raw_operations or {}):
                                assert name in raw_operations[function], (path, node.lineno, "non-publication mutation")
                if isinstance(node, (ast.Constant, ast.BinOp, ast.JoinedStr)):
                    expression = node
                    while isinstance(parents.get(expression), ast.JoinedStr) or (
                        isinstance(parents.get(expression), ast.BinOp) and isinstance(parents[expression].op, ast.Add)
                    ):
                        expression = parents[expression]
                    if expression is not node:
                        continue
                    sql = _without_sql_comments(_sql_text(expression))
                    exact_literal = canonical_function and isinstance(node, ast.Constant) and node.value in allowed_literals.get(function, ())
                    exact_expression = canonical_function and ast.dump(expression) in allowed_expressions.get(function, ())
                    if sql_pattern.search(sql):
                        assert (path == migrations and allow_migration_dml) or canonical_storage_writer or exact_literal or exact_expression, (path, node.lineno)
                        if canonical_storage_writer and function in (raw_operations or {}):
                            assert "sql" in raw_operations[function], (path, node.lineno, "raw SQL mutation")
                    for ddl in ddl_pattern.finditer(sql):
                        assert path == migrations, (path, node.lineno, "authority table DDL")
                        if additive_migration_ddl:
                            assert ddl.group().upper().startswith("CREATE") or (
                                ddl.group().upper().startswith("ALTER")
                                and re.match(r"\s+ADD\s+(?:COLUMN\s+)?(?!CONSTRAINT\b)", sql[ddl.end():], re.I)
                            ), (path, node.lineno, "non-additive authority DDL")
    return scanned
