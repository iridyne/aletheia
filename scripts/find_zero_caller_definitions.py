#!/usr/bin/env python
"""List module-level definitions in `aletheia/` that nothing references.

Objective dead-code detection: parse every `.py` under `aletheia/`, `tests/` and
`scripts/`, collect every referenced identifier (Name, Attribute, `from … import`
target, and string literals so registry/`getattr` lookups count), then report the
definitions whose name never appears.

Two exclusions matter, and both were learned by getting them wrong:

* **Decorator-registered callables are NOT dead.** FastAPI routes, Typer commands,
  `@register_agent` agents, pytest fixtures and pydantic validators are invoked by
  their framework, so their names are legitimately never called. Without this filter
  the report was 427 entries / 9013 lines, dominated by every HTTP endpoint in the
  codebase; with it, 129 entries / 2073 lines.
* **String literals count as references.** Anything reached through a registry, a
  `getattr`, or a name in a config table would otherwise look unused.

When you delete a candidate, **delete its decorators with it.** Slicing from the
`def` line leaves a decorator dangling onto the NEXT definition. Removing
`@contextmanager def atomic_opportunity_evidence()` from its `def` line silently
turned the following `record_filter_decision` into a context manager, and 19 tests
across four unrelated modules failed with `ProgrammingError`. Cut from
`node.decorator_list[0].lineno` when decorators exist, and check afterwards that no
`@contextmanager` function lacks a `yield`.

Two standing false-positive classes this detector cannot see:

* **Same-module callers.** Definitions and references are counted separately, so a
  helper whose only callers live in its own file reads as unused.
  `build_operator_learning_manifest` and `operator_learning_candidate_key` both
  looked dead for exactly this reason.
* **Names that live only in a contract or a test's prose.** `knowledge_hash_exists`
  has no code caller but `docs/design/contracts/knowledge_contract.md:49` names it as
  the hash-deduplication mechanism — retiring it is a contract decision, not a sweep.

The output is a CANDIDATE list, not a delete list. Verify each one by hand before
removing it — grep `docs/design/contracts/` too, because a contract naming a symbol
is a reason to keep it (or to retire the contract clause deliberately). Names
referenced only from prose in `docs/` are usually still dead: this repo has audit
reports that describe functions as having "no live callers".

Usage:
    uv run python scripts/find_zero_caller_definitions.py [--min-lines N] [--limit N]
"""

from __future__ import annotations

import argparse
import ast
import collections
import pathlib

_FRAMEWORK_MARKERS = (
    "router.", "_app.command", "app.command", "app.callback",
    "app.exception_handler", "app.middleware",
    "register_agent", "fixture", "hookimpl", "validator", "scheduler",
    "property", "cached", "lru_cache", "staticmethod", "classmethod",
    "overload", "spec_claim", "parametrize",
)


def _framework_registered(node: ast.AST) -> bool:
    for decorator in getattr(node, "decorator_list", []):
        rendered = ast.unparse(decorator)
        if any(marker in rendered for marker in _FRAMEWORK_MARKERS):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-lines", type=int, default=1)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]
    scanned = [
        *root.glob("aletheia/**/*.py"),
        *root.glob("tests/**/*.py"),
        *root.glob("scripts/**/*.py"),
    ]

    used: collections.Counter[str] = collections.Counter()
    definitions: list[tuple[str, str, str, int, int]] = []

    for path in scanned:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith("aletheia/"):
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if node.name.startswith("__") or _framework_registered(node):
                    continue
                span = (node.end_lineno or node.lineno) - node.lineno + 1
                definitions.append(
                    (relative, node.name, type(node).__name__, node.lineno, span)
                )
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used[node.id] += 1
            elif isinstance(node, ast.Attribute):
                used[node.attr] += 1
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    used[alias.name] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used[node.value.strip()] += 1

    dead = [d for d in definitions if used[d[1]] == 0 and d[4] >= args.min_lines]
    dead.sort(key=lambda item: -item[4])

    print(f"zero-reference module-level definitions: {len(dead)} ({sum(d[4] for d in dead)} lines)")
    print("CANDIDATES — verify each by hand before deleting.\n")
    for relative, name, kind, line, span in dead[: args.limit]:
        print(f"  {span:4} lines  {kind:16} {name:44} {relative}:{line}")
    if len(dead) > args.limit:
        print(f"\n  … {len(dead) - args.limit} more (raise --limit)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
