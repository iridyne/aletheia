"""Diagnostics are read-only — ``doctor`` never writes trading state.

Authority: ``docs/design/contracts/filter_evidence_contract.md`` ("``doctor`` 必须只读报告…")
and the segment-isolation rule in ``docs/design/architecture/ARCHITECTURE.md``: the
diagnostics tree observes state and reports; it owns no write path. ``doctor`` is
invoked from the Operator loop, the CLI and the API route surface, and it runs
against whatever database the process opened — including production.

The gate rejects three shapes inside ``core/aletheia/diagnostics/``:

1. ``from aletheia.storage.repositories import insert_* | update_* | transition_*``
2. a call ``<repo|repositories>.insert_* | update_* | transition_*(...)``
3. ``<db|conn|cursor>.execute(<"INSERT|UPDATE|DELETE|REPLACE|ALTER|DROP|CREATE …">)``

Reads are unrestricted: ``db.execute("SELECT …")``, ``list_*``, ``get_*`` and
``count_*`` repository functions are exactly what a diagnostic is for. Purely
static; the gate does not import or execute the diagnostics tree.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.audit._segment_guard import ROOT, parse

_DIAGNOSTICS = ROOT / "core" / "aletheia" / "diagnostics"

_WRITE_PREFIXES = ("insert_", "update_", "transition_")
_WRITE_RECEIVERS = {"repo", "repositories"}
_SQL_WRITE_KEYWORDS = ("INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "DROP", "CREATE")
_SQL_RECEIVERS = {"db", "conn", "connection", "cursor"}


def _is_write_name(name: str) -> bool:
    return name.startswith(_WRITE_PREFIXES)


def _sql_writes(text: str) -> bool:
    return text.lstrip().upper().startswith(_SQL_WRITE_KEYWORDS)


def _violations_in(path: Path) -> list[str]:
    tree = parse(path)
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") != "aletheia.storage.repositories":
                continue
            for alias in node.names:
                if _is_write_name(alias.name):
                    found.append(f"{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            receiver = node.func.value
            if not isinstance(receiver, ast.Name):
                continue
            if receiver.id in _WRITE_RECEIVERS and _is_write_name(attr):
                found.append(f"{node.lineno}: {receiver.id}.{attr}(...)")
            elif (
                receiver.id in _SQL_RECEIVERS
                and attr in {"execute", "executemany", "executescript"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and _sql_writes(node.args[0].value)
            ):
                keyword = node.args[0].value.lstrip().split(None, 1)[0].upper()
                found.append(f"{node.lineno}: {receiver.id}.{attr}({keyword} ...)")
    return found


@pytest.mark.fast
def test_diagnostics_never_write_trading_state() -> None:
    violations = [
        f"{path.relative_to(ROOT)}:{detail}"
        for path in sorted(_DIAGNOSTICS.rglob("*.py"))
        for detail in _violations_in(path)
    ]
    assert not violations, (
        "the diagnostics tree is read-only: it observes state and reports, it "
        "owns no write path. Route the write through the owning subsystem "
        "instead.\n  " + "\n  ".join(violations)
    )
