"""Silent healthy-fallback gate (P3, 2026-10-09).

A broad ``except Exception:`` (or bare ``except:``) whose handler neither logs nor
raises and directly ``return``s a constant (``1.0``, ``0``, ``[]``, ``{}``,
``True``/``False``, ``None``, a string, or a tuple of those) turns "could not
read" into an answer that looks like real data. MP-06 is the example:
``risk/engine.py::_portfolio_drawdown_assessment`` returns ``(1.0, False)`` and
Risk check #5 reads the ``1.0`` as "no drawdown".

Scope: ``core/aletheia/{risk,execution,portfolio,diagnostics}`` and
``core/aletheia/agent_runtime/health.py``, where a fake-healthy value misleads a
safety or health decision.

Baseline (``silent_healthy_fallback_baseline.json``) entries are keyed by
``(file, enclosing function, returned constant)`` so unrelated edits that move
lines do not turn the gate red. A site not in the baseline fails; a baseline entry
whose site is gone prints a "remove from baseline" hint. Fix a new site with a
``logger.warning(..., exc_info=True)``, a ``raise``, or a fail-closed return the
caller can tell apart; only add a baseline entry with a reason.

Blind spots: a handler that logs at ``debug`` passes; a constant built by a call
(``float("nan")``) or returned from a nested branch is not matched.

Refs: docs/research/audits/FIVE_PATTERN_DISCONNECTION_SWEEP_2026-10-09.md §5.1
"""

from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = Path(__file__).with_name("silent_healthy_fallback_baseline.json")
_SCOPE_DIRS = ("risk", "execution", "portfolio", "diagnostics")
_SCOPE_FILES = ("core/aletheia/agent_runtime/health.py",)
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}

Site = tuple[str, str, str]


def _is_constant(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        return True
    if isinstance(node, ast.Tuple):
        return all(_is_constant(e) for e in node.elts)
    if isinstance(node, (ast.List, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return False


def _is_broad(handler: ast.ExceptHandler) -> bool:
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in {"Exception", "BaseException"})


def _silent_constant_returns(handler: ast.ExceptHandler) -> list[str]:
    nodes = [n for stmt in handler.body for n in ast.walk(stmt)]
    if any(isinstance(n, ast.Raise) for n in nodes):
        return []
    if any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _LOG_METHODS
        for n in nodes
    ):
        return []
    return [
        ast.unparse(stmt.value)
        for stmt in handler.body
        if isinstance(stmt, ast.Return) and stmt.value is not None and _is_constant(stmt.value)
    ]


def scan_source(source: str, rel: str) -> list[Site]:
    sites: list[Site] = []

    def visit(node: ast.AST, qual: str) -> None:
        for child in ast.iter_child_nodes(node):
            q = qual
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                q = f"{qual}.{child.name}" if qual else child.name
            if isinstance(child, ast.ExceptHandler) and _is_broad(child):
                sites.extend((rel, q, value) for value in _silent_constant_returns(child))
            visit(child, q)

    visit(ast.parse(source), "")
    return sites


def _current_sites() -> Counter[Site]:
    paths = [p for d in _SCOPE_DIRS for p in (ROOT / "core/aletheia" / d).rglob("*.py")]
    paths += [ROOT / f for f in _SCOPE_FILES]
    sites: Counter[Site] = Counter()
    for p in sorted(paths):
        sites.update(scan_source(p.read_text(encoding="utf-8"), str(p.relative_to(ROOT))))
    return sites


def _baseline() -> Counter[Site]:
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert all(s.get("reason") for s in data["sites"]), "every baseline entry needs a reason"
    return Counter((s["file"], s["function"], s["returns"]) for s in data["sites"])


def test_scanner_catches_unlogged_constant_fallback() -> None:
    src = (
        "def f():\n"
        "    try:\n        return g()\n    except Exception:\n        return 1.0, False\n"
        "def ok():\n"
        "    try:\n        return g()\n    except Exception:\n"
        "        logger.warning('x', exc_info=True)\n        return None\n"
    )
    assert scan_source(src, "m.py") == [("m.py", "f", "(1.0, False)")]


def test_no_new_silent_healthy_fallback() -> None:
    current, baseline = _current_sites(), _baseline()
    for site in sorted(baseline - current):
        print(f"  STALE baseline entry, remove from {BASELINE_FILE.name}: {site}")
    new = sorted(current - baseline)
    assert not new, (
        f"{len(new)} NEW unlogged `except Exception: return <constant>` site(s); log/raise/"
        f"fail closed, or add a reasoned entry to {BASELINE_FILE.name}:\n"
        + "\n".join(f"  {f} :: {fn} -> return {v}" for f, fn, v in new)
    )
