"""Audit Chain Contract §2: literal write_audit payloads identify actor and source.

Static scope: named write_audit calls with a dict literal in core/extensions/ops.
A preceding direct local dict assignment can supply keys through **unpacking;
dynamic payload builders and value/enum validation are outside this gate.
"""

from __future__ import annotations

import ast

import pytest

from tests.audit._segment_guard import ROOT

_SCOPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _scope_nodes(node):
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, _SCOPES):
            yield from _scope_nodes(child)


def literal_audit_failures(tree: ast.Module) -> tuple[int, list[str]]:
    checked = 0
    failures = []
    for scope in ast.walk(tree):
        if not isinstance(scope, _SCOPES):
            continue
        bindings = {}
        for statement in scope.body:
            if isinstance(statement, _SCOPES):
                continue
            for node in _scope_nodes(statement):
                if not (
                    isinstance(node, ast.Call)
                    and (
                        (isinstance(node.func, ast.Name) and node.func.id == "write_audit")
                        or (isinstance(node.func, ast.Attribute) and node.func.attr == "write_audit")
                    )
                ):
                    continue
                payload = node.args[0] if node.args else next(
                    (keyword.value for keyword in node.keywords if keyword.arg == "event"), None
                )
                if not isinstance(payload, ast.Dict):
                    continue
                checked += 1
                keys = {key.value for key in payload.keys if isinstance(key, ast.Constant)}
                for key, value in zip(payload.keys, payload.values, strict=True):
                    if key is None and isinstance(value, ast.Name):
                        inherited = bindings.get(value.id)
                        if isinstance(inherited, ast.Dict):
                            keys.update(key.value for key in inherited.keys if isinstance(key, ast.Constant))
                missing = {"actor", "source"} - keys
                if missing:
                    failures.append(f"{node.lineno}: literal write_audit missing {', '.join(sorted(missing))}")
            if isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = statement.value
    return checked, failures


@pytest.mark.fast
@pytest.mark.tier_unit
def test_literal_audit_events_identify_actor_and_source():
    checked = 0
    violations = []
    for directory in ("core", "extensions", "ops"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "write_audit" not in source:
                continue
            count, failures = literal_audit_failures(ast.parse(source, filename=str(path)))
            checked += count
            violations.extend(f"{path.relative_to(ROOT)}:{failure}" for failure in failures)
    assert checked > 0, "No literal write_audit payloads found; audit gate is vacuous"
    assert not violations, "Audit Chain §2 actor/source invariant:\n" + "\n".join(violations)
