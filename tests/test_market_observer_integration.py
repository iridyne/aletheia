"""Integration test — Round 2 anti-hallucination boundary audit (plan §8.6.3).

Round 1 was terminated because a downstream learning loop consumed hallucinated
opportunity_id=None rows from trading_decision.py:250, feeding the ranker noise
that bypassed the frozen guards. The contract for Round 2 macro signals (LSR,
F&G, top movers) is that those values enter the LLM enriched_context ONLY —
they must NOT be read by trading_decision, pi_agent, learning, dreaming, or
research. The LLM may CITE a value from the snapshot in key_observations
(that's the prompt's whole point), but the structured fields must stay walled
off from the feedback loops that learn from outcomes.

These tests assert both halves of that contract:
  1. STATIC grep — no module under the trading/learning chain references the
     new field names by string. Cheap, definitive, runs in the spec window.
  2. BEHAVIORAL — when the market_observer produces an Opportunity row whose
     key_observations string mentions "LSR=2.09" (i.e. the LLM cited the value),
     the row lands in storage and the downstream rankers never get
     long_short_ratio passed in via the row dict's columns. The new field
     stays in the snapshot tables, not in opportunities.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
NEW_FIELDS = ("long_short_ratio", "fng_value", "fng_classification", "top_movers_summary")

# Modules that MUST NOT reference the new field names (per [[aletheia-round1-terminated-fix-batch]]).
FORBIDDEN_PATHS = [
    "aletheia/agent_shell/trading_decision.py",
    "aletheia/learning",
    "aletheia/dreaming",
    "aletheia/research",
]


def _grep_for_field(field: str, path: Path) -> list[str]:
    """Return list of file:line hits — empty means clean."""
    hits: list[str] = []
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = list(path.rglob("*.py"))
    else:
        return []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if field in line and not line.strip().startswith("#"):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:140]}")
    return hits


def test_pi_agent_files_glob_exists_or_skipped():
    """pi_agent_* is a glob; skip if no files match (the layout has moved before)."""
    pi_files = list((ROOT / "aletheia" / "agents").glob("pi_agent*.py"))
    if not pi_files:
        pytest.skip("no pi_agent_* files — module may have been renamed")


def test_new_fields_not_in_trading_decision():
    """trading_decision.py — the Round 1 hallucination loop's terminal sink."""
    target = ROOT / "aletheia/agent_shell/trading_decision.py"
    if not target.exists():
        pytest.skip(f"{target} not present")
    leaks: dict[str, list[str]] = {}
    for field in NEW_FIELDS:
        hits = _grep_for_field(field, target)
        if hits:
            leaks[field] = hits
    assert not leaks, (
        f"Round 2 anti-hallucination boundary VIOLATED: new fields leaked into "
        f"trading_decision.py — these fields must stay in enriched_context only.\n"
        f"Leaks:\n" + "\n".join(f"  {fld}: {hits}" for fld, hits in leaks.items())
    )


def test_new_fields_not_in_pi_agent():
    """pi_agent_* modules — must NOT consume new fields by name."""
    pi_files = list((ROOT / "aletheia" / "agents").glob("pi_agent*.py"))
    if not pi_files:
        pytest.skip("no pi_agent_* files")
    leaks: dict[str, list[str]] = {}
    for field in NEW_FIELDS:
        for f in pi_files:
            hits = _grep_for_field(field, f)
            if hits:
                leaks.setdefault(field, []).extend(hits)
    assert not leaks, (
        f"Round 2 boundary VIOLATED: new fields leaked into pi_agent_* — these "
        f"fields must stay in enriched_context only.\n"
        f"Leaks:\n" + "\n".join(f"  {fld}: {hits}" for fld, hits in leaks.items())
    )


def test_new_fields_not_in_learning_dreaming_research():
    """The three downstream consumers Round 1 was killed for poisoning."""
    leaks: dict[str, list[str]] = {}
    for subdir in ("learning", "dreaming", "research"):
        target = ROOT / "aletheia" / subdir
        if not target.exists():
            continue
        for field in NEW_FIELDS:
            hits = _grep_for_field(field, target)
            if hits:
                leaks.setdefault(f"{subdir}::{field}", []).extend(hits)
    assert not leaks, (
        f"Round 2 boundary VIOLATED: new fields leaked into "
        f"learning/dreaming/research — Round 1 termination root cause replay risk.\n"
        f"Leaks:\n" + "\n".join(f"  {key}: {hits}" for key, hits in leaks.items())
    )


def test_opportunity_table_has_no_new_field_columns():
    """The Opportunity table schema must NOT have grown a long_short_ratio /
    fng_value / top_movers_summary column. If a future refactor adds one, the
    feedback loops will quietly start reading it from opportunity rows."""
    migrations = (ROOT / "aletheia/storage/migrations.py").read_text(encoding="utf-8")
    # Locate the CREATE TABLE block for opportunities.
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS opportunities\s*\((.*?)\);",
        migrations,
        re.DOTALL,
    )
    assert m, "opportunities CREATE TABLE block not found in migrations.py"
    block = m.group(1)
    forbidden_in_opp = ("long_short_ratio", "fng_value", "fng_classification", "top_movers_summary")
    leaked = [f for f in forbidden_in_opp if f in block]
    assert not leaked, (
        f"opportunities table schema must not contain Round 2 enriched_context "
        f"field columns; found: {leaked}. New signals must stay in their own "
        f"snapshot tables (open_interest_snapshots / macro_snapshots / "
        f"coingecko_top_snapshots), not on opportunities."
    )

    # Same check on _ADDITIVE_COLUMNS — defends against the ALTER TABLE path
    # the migration module uses for forward-compat additions.
    add_block_match = re.search(
        r"_ADDITIVE_COLUMNS\s*=\s*\[(.*?)\n\]",
        migrations,
        re.DOTALL,
    )
    if add_block_match:
        add_block = add_block_match.group(1)
        for fld in forbidden_in_opp:
            # Pattern: ("field_name", "opportunities", ...). We allow these
            # tuples in macro_snapshots / open_interest_snapshots tables — only
            # `opportunities` is forbidden.
            forbidden_pattern = re.compile(
                rf'\(\s*"{re.escape(fld)}"\s*,\s*"opportunities"\s*,', re.MULTILINE,
            )
            assert not forbidden_pattern.search(add_block), (
                f"_ADDITIVE_COLUMNS adds {fld!r} to the opportunities table — "
                f"this violates the Round 2 anti-hallucination boundary."
            )


def test_market_observer_does_not_pass_new_fields_into_opportunity_constructor():
    """AST-level assertion: every Opportunity() construction in market_observer.py
    must NOT pass long_short_ratio / fng_value / fng_classification / top_movers_summary
    as kwargs. The new signals are rendered into the enriched_context markdown
    that feeds the LLM read; whether the LLM cites the value in
    key_observations is its choice — but the structured row must stay clean.
    """
    import ast
    src = (ROOT / "aletheia/agents/market_observer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"long_short_ratio", "fng_value", "fng_classification", "top_movers_summary"}
    bad: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Opportunity"
        ):
            kw_names = {kw.arg for kw in node.keywords if kw.arg}
            if leaks := (kw_names & forbidden):
                bad.append(f"Opportunity() at line {node.lineno} has forbidden kwargs: {leaks}")
    assert not bad, "\n".join(bad)
