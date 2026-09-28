"""Profit Loop Observability Contract §1/§2: Position Story is a read-only projection.

Scan the current package, including its readers, rather than a retired .py path.
Repository access is limited to the currently required get_db connection reader,
including import aliases. SQL writes reuse the diagnostics checker. Actual
read-only behavior is also exercised by test_position_lifecycle_story.py.
"""

import ast

from tests.audit._segment_guard import ROOT, parse
from tests.audit.test_doctor_no_execution_writes import _violations_in


def _repository_access_violations(path):
    tree = parse(path)
    aliases = set()
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("aletheia.storage.repositories"):
                    aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if module == "aletheia.storage" and alias.name.startswith("repositories"):
                    aliases.add(alias.asname or alias.name)
                elif module.startswith("aletheia.storage.repositories") and alias.name != "get_db":
                    violations.append(f"{node.lineno}: unapproved repository import {alias.name}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and ast.unparse(node.value) in aliases and node.attr != "get_db":
            violations.append(f"{node.lineno}: unapproved repository access {node.attr}")
    return violations


def test_position_story_never_writes_trading_state():
    paths = sorted((ROOT / "core/aletheia/analytics/position_lifecycle_story").rglob("*.py"))
    assert paths, "Position Story source not found; read-only gate is vacuous"
    violations = [
        f"{path.relative_to(ROOT)}:{detail}"
        for path in paths
        for detail in [*_violations_in(path), *_repository_access_violations(path)]
    ]
    assert not violations, "Position Story must remain read-only:\n" + "\n".join(violations)
