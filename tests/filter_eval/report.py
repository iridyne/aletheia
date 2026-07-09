"""
Run filter evaluation scenarios and print a Markdown report.

Usage:  uv run python tests/filter_eval/report.py
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

# Make sure the project root is importable
sys.path.insert(0, str(Path(__file__).parents[2]))

from aletheia.agent_shell.trading_decision import _proposal_direction_overheated
from tests.filter_eval.scenarios.direction_brake import ALL_SCENARIOS


def _make_fake_db(history):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY, symbol TEXT, direction TEXT,
            created_at TEXT, source TEXT DEFAULT 'native'
        )
    """)
    for i, p in enumerate(history):
        conn.execute(
            "INSERT INTO trade_proposals VALUES (?,?,?,?,?)",
            (f"h{i}", p.symbol, p.direction, p.created_at, p.source),
        )
    conn.commit()
    return conn


def run_scenarios():
    rows = []
    for scenario in ALL_SCENARIOS:
        conn = _make_fake_db(scenario.history)

        class _FakeDB:
            def execute(self, sql, params=()):
                return conn.execute(sql, params)

        with patch("aletheia.storage.sqlite.get_db", return_value=_FakeDB()):
            for record, expected in zip(scenario.incoming, scenario.expected_blocked):
                result = _proposal_direction_overheated(record.direction, record.conviction)
                actual = result is not None
                status = "PASS" if actual == expected else "FAIL"
                rows.append({
                    "scenario": scenario.name,
                    "direction": record.direction,
                    "conviction": record.conviction,
                    "history": len(scenario.history),
                    "expected_blocked": expected,
                    "actual_blocked": actual,
                    "status": status,
                    "reason": result or "",
                })
    return rows


def print_report(rows):
    print("# Direction Brake Filter Evaluation Report")
    print()
    print("| Scenario | Dir | Conv | History | Expected | Actual | Status |")
    print("|----------|-----|------|---------|----------|--------|--------|")
    for r in rows:
        exp = "BLOCK" if r["expected_blocked"] else "PASS"
        act = "BLOCK" if r["actual_blocked"] else "PASS"
        print(f"| {r['scenario']} | {r['direction']} | {r['conviction']} | "
              f"{r['history']} | {exp} | {act} | {r['status']} |")
    print()
    fails = [r for r in rows if r["status"] == "FAIL"]
    passes = len(rows) - len(fails)
    print(f"**Result: {passes}/{len(rows)} passed, {len(fails)} failed**")
    if fails:
        print()
        print("## Failures")
        for f in fails:
            print(f"- **{f['scenario']}**: expected {'BLOCK' if f['expected_blocked'] else 'PASS'}, "
                  f"got {'BLOCK' if f['actual_blocked'] else 'PASS'} — reason: {f['reason']!r}")


if __name__ == "__main__":
    rows = run_scenarios()
    print_report(rows)
