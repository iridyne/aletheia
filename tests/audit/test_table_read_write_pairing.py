"""Table read/write pairing gate (P2 for tables, 2026-10-09).

A table that production code reads (``FROM``/``JOIN`` in a SQL string) but that no
production code writes is a tombstone: readers report "no data" forever and look
like an empty-but-healthy result. ``shadow_trades`` (last row 2026-08-27, still
read by family scorecard and calibration) is the example.

Scanned: string constants in ``core``, ``extensions`` and ``ops``. Tables are the
names in ``CREATE TABLE`` statements. A table is written when some string has
``INSERT [OR x] INTO`` / ``REPLACE INTO`` / ``UPDATE`` on it, or when its name is a
literal argument of a write-shaped helper call (``_insert("t", ...)``,
``insert_record("t", ...)``, ``update_...``, ``upsert_...``, ``write_...``,
``record_...``, ``save_...``, ``persist_...``, ``append_...``). Rebuild
temporaries ``*_vNN`` are ignored. Migration backfills count as writes: they are
production code that put the rows there, and migrations are frozen.

Every read-but-never-written table must be in the retired-tables baseline
(``table_read_write_pairing_baseline.json``) with a reason. Tables are never
dropped to satisfy this gate (data_model.md §6); the fix for a new entry is a
writer, deleting the reader, or a reasoned baseline entry. A baseline table that
is now written or no longer read prints a "remove from baseline" hint.

Blind spots: a write via an f-string table name is matched only through the
literal helper-call path; table names built at runtime are invisible; a writer
that exists but is unreachable is out of scope (see
``test_alpha_cohort_reachability.py`` for reachability).

Refs: docs/research/audits/FIVE_PATTERN_DISCONNECTION_SWEEP_2026-10-09.md §5.3
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = Path(__file__).with_name("table_read_write_pairing_baseline.json")
_ROOTS = ("core", "extensions", "ops")
_CREATE = re.compile(r"CREATE\s+(?:VIRTUAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_]\w*)", re.I)
_READ = re.compile(r"\b(?:FROM|JOIN)\s+[\"`]?([A-Za-z_]\w*)")
_WRITE = re.compile(
    r"\b(?:INSERT(?:\s+OR\s+[A-Z]+)?\s+INTO|REPLACE\s+INTO|UPDATE)\s+[\"`]?([A-Za-z_]\w*)"
)
_WRITE_HELPER = re.compile(r"insert|upsert|update|write|replace|record|save|persist|append", re.I)
_REBUILD_TEMP = re.compile(r"_v\d+$")


def scan_source(source: str) -> tuple[set[str], set[str], set[str]]:
    """Return (created, read, written) table names in one module."""
    created: set[str] = set()
    read: set[str] = set()
    written: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            created.update(_CREATE.findall(node.value))
            read.update(_READ.findall(node.value))
            written.update(_WRITE.findall(node.value))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if _WRITE_HELPER.search(name):
                written.update(
                    a.value for a in [*node.args, *(k.value for k in node.keywords)]
                    if isinstance(a, ast.Constant) and isinstance(a.value, str)
                )
    return created, read, written


def unpaired(created: set[str], read: set[str], written: set[str]) -> set[str]:
    return {t for t in read & created if t not in written and not _REBUILD_TEMP.search(t)}


def _current_unpaired() -> set[str]:
    created: set[str] = set()
    read: set[str] = set()
    written: set[str] = set()
    for root in _ROOTS:
        for p in sorted((ROOT / root).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            c, r, w = scan_source(p.read_text(encoding="utf-8"))
            created |= c
            read |= r
            written |= w
    return unpaired(created, read, written)


def _baseline() -> set[str]:
    data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    assert all(t.get("reason") for t in data["tables"]), "every baseline entry needs a reason"
    return {t["table"] for t in data["tables"]}


def test_scanner_pairs_reads_with_writes() -> None:
    src = (
        "DDL = 'CREATE TABLE IF NOT EXISTS a (x); CREATE TABLE b (x); CREATE TABLE c (x)'\n"
        "def r(db):\n    db.execute('SELECT * FROM a JOIN b ON 1 JOIN c ON 1')\n"
        "def w(db):\n    db.execute('INSERT OR IGNORE INTO a (x) VALUES (1)')\n    _insert('c', {})\n"
    )
    assert unpaired(*scan_source(src)) == {"b"}


def test_every_read_table_has_a_writer_or_is_retired() -> None:
    current, baseline = _current_unpaired(), _baseline()
    for t in sorted(baseline - current):
        print(f"  STALE baseline entry, remove from {BASELINE_FILE.name}: {t}")
    new = sorted(current - baseline)
    assert not new, (
        f"{len(new)} table(s) read in production SQL with no production writer; add the writer, "
        f"delete the reader, or add a reasoned entry to {BASELINE_FILE.name}:\n"
        + "\n".join(f"  {t}" for t in new)
    )
