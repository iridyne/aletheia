"""Zero-reference top-level definition gate (P5, 2026-10-09).

A top-level ``def``/``class`` in ``core/`` or ``extensions/`` whose name appears
nowhere except its own definition is dead: nothing in production calls, imports,
registers or routes to it. Tests do not count as a reference, so a name used only
by tests is dead too (delete both, or wire the live path).

References are word tokens over ``core``, ``extensions``, ``ops``, ``scripts``,
``frontend/src``, ``operator-shell`` and ``pyproject.toml`` (entry points), so
string registries, ``getattr`` names, job ids and route paths that spell the name
count. A definition with any decorator other than a plain wrapper
(``dataclass``, ``property``, ``lru_cache``...) is treated as registered
(typer/FastAPI/scheduler/tool registries) and skipped; dunder names are skipped.

Baseline (``zero_reference_definitions_baseline.json``) entries are
``(file, name)``. A new dead definition fails; a baseline entry that is gone
(deleted or now referenced) prints a "remove from baseline" hint.

Blind spots: a name that collides with any other token anywhere (``run``,
``reset_state``) is never flagged; a re-export in ``__init__.py`` counts as a
reference; methods and pydantic config fields are out of scope.

Refs: docs/research/audits/FIVE_PATTERN_DISCONNECTION_SWEEP_2026-10-09.md §5.2
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = Path(__file__).with_name("zero_reference_definitions_baseline.json")
_DEFINITION_ROOTS = ("core", "extensions")
_REFERENCE_ROOTS = ("core", "extensions", "ops", "scripts", "frontend/src", "operator-shell")
_REFERENCE_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".toml", ".json", ".yaml", ".yml", ".sh", ".html",
}
_SKIP_PARTS = {"node_modules", "__pycache__", ".venv", "dist", "build"}
_PLAIN_DECORATORS = {
    "dataclass", "staticmethod", "classmethod", "property", "cached_property", "lru_cache",
    "cache", "contextmanager", "asynccontextmanager", "overload", "final", "total_ordering",
    "wraps", "runtime_checkable", "unique",
}
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")


def candidate_definitions(source: str) -> list[str]:
    return [
        node.name
        for node in ast.parse(source).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("__")
        and all(_decorator_name(d) in _PLAIN_DECORATORS for d in node.decorator_list)
    ]


def _reference_counts() -> Counter[str]:
    counts: Counter[str] = Counter()
    files = [ROOT / "pyproject.toml"]
    for root in _REFERENCE_ROOTS:
        files += [
            p for p in (ROOT / root).rglob("*")
            if p.suffix in _REFERENCE_SUFFIXES
            and not _SKIP_PARTS.intersection(p.relative_to(ROOT).parts)
            and p.is_file()
        ]
    for p in files:
        counts.update(_TOKEN.findall(p.read_text(encoding="utf-8", errors="ignore")))
    return counts


def _current_dead() -> set[tuple[str, str]]:
    counts = _reference_counts()
    dead: set[tuple[str, str]] = set()
    for root in _DEFINITION_ROOTS:
        for p in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            rel = str(p.relative_to(ROOT))
            # The definition itself is the one occurrence a dead name has.
            dead.update((rel, n) for n in candidate_definitions(p.read_text(encoding="utf-8")) if counts[n] <= 1)
    return dead


def _baseline() -> set[tuple[str, str]]:
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert all(s.get("reason") for s in data["definitions"]), "every baseline entry needs a reason"
    return {(s["file"], s["name"]) for s in data["definitions"]}


def test_candidate_filter_skips_registered_and_dunder() -> None:
    src = (
        "@app.command()\ndef cli(): ...\n"
        "@dataclass\nclass Row: ...\n"
        "def __getattr__(n): ...\n"
        "def helper(): ...\n"
    )
    assert candidate_definitions(src) == ["Row", "helper"]


def test_no_new_zero_reference_definitions() -> None:
    current, baseline = _current_dead(), _baseline()
    for f, n in sorted(baseline - current):
        print(f"  STALE baseline entry, remove from {BASELINE_FILE.name}: {f} {n}")
    new = sorted(current - baseline)
    assert not new, (
        f"{len(new)} NEW top-level definition(s) with no reference outside their own definition "
        f"(tests do not count); delete, wire, or add a reasoned entry to {BASELINE_FILE.name}:\n"
        + "\n".join(f"  {f} :: {n}" for f, n in new)
    )
