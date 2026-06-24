"""Spec: Learning Pipeline (RED, written 2026-06-24 during freeze).

These claims describe how the Learning subsystem (knowledge_items / ps_lessons)
MUST be consumed by the PI Agent decision chain after Round 2 解冻. They are
recorded with status="missing" because the production code paths required to
satisfy them are inside the EXPERIMENT FREEZE clearance (trading_context.py /
trading_decision.py) and cannot be modified during Round 1.

Each test below intentionally fails today (xfail) and is expected to flip to
GREEN as soon as Round 2 解冻当天 applies the fixes documented in
docs/plans/round2_week1_decision_chain_fixes.md.

DO NOT modify these tests to make them pass during the freeze — the whole
point is that they are a tripwire for solving the right root cause once.
"""

from __future__ import annotations

import ast
import sqlite3

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


_TRADING_CONTEXT = "aletheia/agent_shell/trading_context.py"
_TRADING_DECISION = "aletheia/agent_shell/trading_decision.py"


def _project_root() -> str:
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[2])


def _find_call_keywords(source: str, func_name: str) -> list[set[str]]:
    """Return the set of keyword-arg names supplied to each call of func_name."""
    tree = ast.parse(source)
    out: list[set[str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = node.func
            name = None
            if isinstance(target, ast.Name):
                name = target.id
            elif isinstance(target, ast.Attribute):
                name = target.attr
            if name == func_name:
                out.append({kw.arg for kw in node.keywords if kw.arg})
    return out


# ---------------------------------------------------------------------------
# LP-S01 — Layer3 lessons filter must align with real DB item_type values
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S01", "learning_pipeline", "§Layer3 lessons", "L2a",
              "_build_layer3_wisdom lessons filter must hit existing DB item_type rows",
              status="missing"),
)
@pytest.mark.xfail(reason="冻结期不能改 trading_context.py:379 ('lesson'/'accepted_lesson'/'trading_guard') → 0 hits in DB. "
                          "Fix L-Bug-A in Round 2 解冻当天 to align filter values with real item_type "
                          "(post_trade_review / trade_case_study).", strict=True)
def test_layer3_lessons_filter_aligned():
    """The 3 hard-coded item_type tokens MUST match >= 1 row in knowledge_items.

    Today the filter is ('lesson','accepted_lesson','trading_guard'), all 3 of
    which have ZERO producers, so PI's Layer3 lessons is permanently empty.
    Real lesson content lives in 'post_trade_review' (1679 rows) and
    'trade_case_study' (496 rows) — Round 2 fix updates the filter set.
    """
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_CONTEXT).read_text(encoding="utf-8")

    # Find the lessons filter set: extract the literal tuple in the line
    # `if item.get("item_type") in (...)`
    tree = ast.parse(src)
    filter_values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.In) and isinstance(comp, ast.Tuple):
                    for elt in comp.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            # Only collect filter tuples that mention knowledge taxonomies
                            v = elt.value
                            if v in ("lesson", "accepted_lesson", "trading_guard",
                                     "post_trade_review", "trade_case_study",
                                     "research_note", "daily_review", "operator_assessment"):
                                filter_values.append(v)

    assert filter_values, "could not find the in-filter tuple — refactor likely happened"

    # Verify against the live DB: at least one of those filter values must exist
    import sqlite3
    conn = sqlite3.connect(f"{_project_root()}/data/app.db")
    placeholders = ",".join("?" * len(filter_values))
    n = conn.execute(
        f"SELECT COUNT(*) FROM knowledge_items WHERE item_type IN ({placeholders})",
        filter_values,
    ).fetchone()[0]
    conn.close()

    assert n > 0, (
        f"Layer3 lessons filter {filter_values} matches 0 DB rows — PI永远看不到 lessons. "
        f"See round2_week1_decision_chain_fixes.md Fix L-Bug-A"
    )


# ---------------------------------------------------------------------------
# LP-S02 — Layer3 guards query must filter by item_type=guard_candidate
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S02", "learning_pipeline", "§Layer3 guards", "L2a",
              "_build_layer3_wisdom guards must call list_knowledge_items(item_type='guard_candidate')",
              status="missing"),
)
@pytest.mark.xfail(reason="冻结期不能改 trading_context.py:398. list_knowledge_items(limit=30) "
                          "无 item_type → 被高频 system_action 占满 → guard_candidate 永不入窗口. "
                          "Fix L-Bug-A' in Round 2 解冻当天.", strict=True)
def test_layer3_guards_filter_via_query():
    """Layer3 _build_layer3_wisdom must call list_knowledge_items with item_type='guard_candidate'."""
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_CONTEXT).read_text(encoding="utf-8")

    # Find all list_knowledge_items calls; at least one must specify item_type=guard_candidate
    tree = ast.parse(src)
    has_guard_filtered_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            tgt = node.func
            name = tgt.id if isinstance(tgt, ast.Name) else (tgt.attr if isinstance(tgt, ast.Attribute) else None)
            if name == "list_knowledge_items":
                for kw in node.keywords:
                    if kw.arg == "item_type" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value == "guard_candidate":
                        has_guard_filtered_call = True

    assert has_guard_filtered_call, (
        "no list_knowledge_items(item_type='guard_candidate', ...) call found in "
        "trading_context.py — Layer3 guards permanently empty. "
        "See round2_week1_decision_chain_fixes.md Fix L-Bug-A'"
    )


# ---------------------------------------------------------------------------
# LP-S03 — ps_lessons must be consumed by trading_decision.py prompt
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S03", "learning_pipeline", "§ps_lessons consumption", "L2a",
              "trading_decision.py must reference ps_lessons when building PI prompt",
              status="missing"),
)
@pytest.mark.xfail(reason="冻结期不能改 trading_decision.py. 当前整个 aletheia/ 零 SELECT FROM ps_lessons. "
                          "184 条 ps_lessons 写入完全无消费. Fix L-Bug-B in Round 2 解冻当天.", strict=True)
def test_ps_lessons_consumed_in_prompt():
    """trading_decision.py source must mention ps_lessons (either via key access or function call)."""
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_DECISION).read_text(encoding="utf-8")
    assert "ps_lessons" in src, (
        "trading_decision.py never references ps_lessons — 184 行 ps_lessons "
        "完全无消费. See round2_week1_decision_chain_fixes.md Fix L-Bug-B"
    )


# ---------------------------------------------------------------------------
# LP-S04 (informational) — Layer3 wisdom function must exist
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S04", "learning_pipeline", "§Layer3 entry", "L2a",
              "_build_layer3_wisdom() callable exists in trading_context.py"),
)
def test_layer3_function_exists():
    """The Layer 3 wisdom builder MUST exist as an importable callable.

    This is a structural anchor: the Round 2 fixes above target this specific
    function — if it gets renamed/removed, the RED tests above would all xpass
    for the wrong reason. This guard catches that drift.
    """
    from aletheia.agent_shell.trading_context import _build_layer3_wisdom
    assert callable(_build_layer3_wisdom)
