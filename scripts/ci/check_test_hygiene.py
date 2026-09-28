#!/usr/bin/env python3
"""Test hygiene gate: skip/xfail census and data-guarded silent-pass audit (#3471).

A green test run is only evidence when the tests actually ran and actually
asserted. This gate fails when a test suite can be green without either:

  H1  ``pytest.skip()`` with no reason
  H2  ``@pytest.mark.skip`` / ``skipif`` with no ``reason=``
  H3  ``@pytest.mark.xfail`` without ``strict=True`` (or without ``reason=``)
  H4  imperative ``pytest.xfail(...)`` — always xfails, so it can never turn red
  H5  module-level ``pytest.importorskip`` / ``pytest.skip(allow_module_level=True)``
      / unconditional ``pytestmark`` skip — the whole file disappears silently
  H6  a test whose every assertion sits behind a data/environment guard
      (``if path.exists():``, ``if os.environ.get(...):``) or that returns early
      on such a guard — zero assertions executed, still green

Deliberate non-rules: assertion-free smoke tests, asserts inside loops, and
explicit ``if/else`` disclosure branches are legitimate and are not flagged.

Usage: uv run python scripts/ci/check_test_hygiene.py [paths ...]
Exit codes: 0 clean, 1 findings.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import TypeGuard

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_PATHS = ("tests",)

# Conditions whose truth decides whether a test's assertions ever execute.
_GUARD_MARKERS = (
    ".exists(",
    ".is_file(",
    ".is_dir(",
    "environ",
    "getenv",
    "which(",
    "is None",
    "is not None",
)
_CHECK_NAMES = ("raises", "fail", "warns", "deprecated_call")


def _is_test_function(node: ast.AST) -> TypeGuard[ast.FunctionDef | ast.AsyncFunctionDef]:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return getattr(func, "id", "")


def _own_body_nodes(body: list[ast.stmt]) -> list[ast.AST]:
    """Nodes of a body — nested helper functions stay opaque.

    A guard inside a nested helper (``def _mismatched(...): if row is None: return``)
    decides nothing about the test's own assertions, so it is not a silent-pass
    shape.
    """
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        nodes.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _checks(nodes: list[ast.AST]) -> list[ast.AST]:
    """Assertions and assertion-like context managers inside these nodes."""
    found: list[ast.AST] = []
    for node in nodes:
        if isinstance(node, ast.Assert):
            found.append(node)
        elif isinstance(node, ast.Call) and _call_name(node) in _CHECK_NAMES:
            found.append(node)
    return found


def _is_data_guard(test: ast.expr) -> bool:
    source = ast.unparse(test)
    return any(marker in source for marker in _GUARD_MARKERS)


def _guard_findings(function: ast.FunctionDef | ast.AsyncFunctionDef, location: str) -> list[str]:
    """H6: assertions that can silently never run."""
    own_body = _own_body_nodes(function.body)
    checks = _checks(own_body)
    if not checks:
        return []
    findings: list[str] = []
    guarded: list[ast.AST] = []
    for node in own_body:
        if not isinstance(node, ast.If) or not _is_data_guard(node.test):
            continue
        guarded.extend(_checks(_own_body_nodes(node.body)))
        body = [
            statement
            for statement in node.body
            if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ]
        if len(body) == 1 and isinstance(body[0], ast.Return):
            findings.append(
                f"{location} H6 data-guarded early return at line {node.lineno}: "
                "assertions can be skipped while the test still reports green"
            )
    if guarded and len(guarded) >= len(checks):
        findings.append(
            f"{location} H6 every assertion is behind a data/environment guard: "
            "the test passes with zero assertions when the data is absent"
        )
    return findings


def audit_source(source: str, path: str) -> list[str]:
    """Return hygiene findings for one Python source file."""
    tree = ast.parse(source, filename=path)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for decorator in node.decorator_list:
                text = ast.unparse(decorator)
                location = f"{path}:{node.lineno} {node.name}"
                if "mark.skip" in text and "reason" not in text:
                    findings.append(f"{location} H2 skip/skipif without reason: {text[:120]}")
                if "mark.xfail" in text:
                    if "strict" not in text:
                        findings.append(f"{location} H3 xfail without strict=True: {text[:120]}")
                    if "reason" not in text:
                        findings.append(f"{location} H3 xfail without reason: {text[:120]}")
        if isinstance(node, ast.Call):
            text = ast.unparse(node)
            if text.startswith("pytest.skip("):
                if not node.args and not node.keywords:
                    findings.append(f"{path}:{node.lineno} H1 pytest.skip() without reason")
                if "allow_module_level" in text and node.col_offset == 0:
                    findings.append(
                        f"{path}:{node.lineno} H5 module-level pytest.skip(allow_module_level=True) "
                        "silently removes the whole file"
                    )
            elif text.startswith("pytest.importorskip(") and node.col_offset == 0:
                findings.append(
                    f"{path}:{node.lineno} H5 module-level pytest.importorskip silently removes the whole file"
                )
            elif text.startswith("pytest.xfail("):
                findings.append(
                    f"{path}:{node.lineno} H4 imperative pytest.xfail can never turn red — "
                    "use @pytest.mark.xfail(strict=True, reason=...)"
                )
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            text = ast.unparse(node)
            if ".skip" in text:
                findings.append(f"{path}:{node.lineno} H5 unconditional module-level pytestmark skip")

    for node in ast.walk(tree):
        if _is_test_function(node):
            findings.extend(_guard_findings(node, f"{path}:{node.lineno} {node.name}"))

    return sorted(set(findings))


def audit_path(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:  # pragma: no cover - unreadable test file
        return [f"{path} unreadable: {error}"]
    try:
        return audit_source(source, str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path))
    except SyntaxError as error:
        return [f"{path} syntax error: {error}"]


def iter_python_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        target = (REPO_ROOT / raw).resolve() if not Path(raw).is_absolute() else Path(raw)
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
        else:
            files.append(target)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Test skip/xfail and silent-pass hygiene gate.")
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    args = parser.parse_args(argv)

    findings: list[str] = []
    for path in iter_python_files(args.paths):
        findings.extend(audit_path(path))

    if findings:
        print(f"test hygiene: FAIL ({len(findings)} finding(s))")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print("test hygiene: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
