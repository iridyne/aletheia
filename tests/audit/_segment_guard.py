"""Shared AST helpers for the segment-isolation audit gates.

Pure static analysis: never imports the scanned module, touches no DB and needs
no fixtures. Used by ``test_reconciliation_no_gateway_imports.py`` and
``test_scheduler_no_order_submission.py``, which both assert that a
non-executing segment cannot reach an order-placement entry point.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive: unparse on exotic nodes
        return ""


def _module_aliases(tree: ast.Module, targets: dict[str, frozenset[str]]) -> dict[str, str]:
    """Map every local name bound to a target module back to that module path."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in targets:
                    aliases[alias.asname or alias.name.rsplit(".", 1)[-1]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            source = node.module or ""
            for alias in node.names:
                candidate = f"{source}.{alias.name}" if source else alias.name
                if candidate in targets:
                    aliases[alias.asname or alias.name] = candidate
    return aliases


def placement_violations(
    path: Path, targets: dict[str, frozenset[str]]
) -> list[str]:
    """Return ``"<line>: <what>"`` for every reach into a forbidden placement name.

    ``targets`` maps a module path to its forbidden member names. Three shapes are
    caught, which is every way this codebase reaches such a name:

    * ``from <module> import <forbidden>`` — the direct import.
    * ``<alias>.<forbidden>(...)`` where ``<alias>`` is bound to the module by
      ``import <module> as <alias>`` or ``from <parent> import <leaf>``.
    * ``<module dotted path>.<forbidden>(...)`` written out in full.

    Importing the *module* without reaching a placement member is allowed: the
    invariant is about placement entry points, not about module coupling.
    """
    tree = parse(path)
    forbidden = frozenset().union(*targets.values())
    leaves = tuple(module.rsplit(".", 1)[-1] for module in targets)
    aliases = _module_aliases(tree, targets)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in targets:
                for alias in node.names:
                    if alias.name in targets[module]:
                        violations.append(
                            f"{node.lineno}: from {module} import {alias.name}"
                        )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            receiver = _dotted(node.func.value)
            if attr not in forbidden:
                continue
            bound = aliases.get(receiver.split(".")[0])
            if bound is not None and attr in targets[bound]:
                violations.append(f"{node.lineno}: {receiver}.{attr}(...)")
            elif attr in forbidden and any(leaf in receiver for leaf in leaves):
                violations.append(f"{node.lineno}: {receiver}.{attr}(...)")

    return sorted(set(violations))
