"""Spec: Learning Pipeline (RED, written 2026-06-24, extended 2026-06-27 during freeze).

These claims describe how the Learning subsystem (knowledge_items / ps_lessons)
MUST be consumed by the PI Agent decision chain after Round 2 解冻. They are
recorded with status="missing" because the production code paths required to
satisfy them are inside the EXPERIMENT FREEZE clearance (trading_context.py /
trading_decision.py) and cannot be modified during Round 1.

LP-S01..S04 (2026-06-24): consumption-side dead filters (Layer3 lessons/guards,
ps_lessons no consumer). LP-S05..S06 (2026-06-27, from the full-lifecycle audit
in docs/issues/LEARNING_LOOP_AUDIT_2026-06-26.md): the attribution-chain root
cause (opportunity_id hard-coded None) and the guard-promotion dead-end (promoter
reads a different dir than where candidates are written).

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
              "_build_layer3_wisdom lessons must query a real-producer item_type "
              "(trade_case_study) so PI's Layer3 lessons is non-empty"),
)
def test_layer3_lessons_filter_aligned():
    """Layer3 lessons MUST query an item_type that has a real producer + DB rows.

    Fixed 2026-06-27 (L-Bug-A): the old filter ('lesson','accepted_lesson',
    'trading_guard') matched ZERO rows — none had a producer, so Layer3 lessons
    was permanently empty. The fix queries trade_case_study (attributed
    closed-trade cases) directly via list_knowledge_items(item_type=...). This
    test verifies that call exists AND that the item_type has live DB rows.
    """
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_CONTEXT).read_text(encoding="utf-8")
    tree = ast.parse(src)
    queried_types: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            tgt = node.func
            name = tgt.id if isinstance(tgt, ast.Name) else (tgt.attr if isinstance(tgt, ast.Attribute) else None)
            if name == "list_knowledge_items":
                for kw in node.keywords:
                    if kw.arg == "item_type" and isinstance(kw.value, ast.Constant) \
                            and isinstance(kw.value.value, str):
                        queried_types.add(kw.value.value)

    assert queried_types, (
        "no list_knowledge_items(item_type=...) call in trading_context — Layer3 "
        "would scan the whole table and be flooded by system_action"
    )
    assert "trade_case_study" in queried_types, (
        f"Layer3 lessons must query trade_case_study; found item_types={queried_types}. "
        f"See round2_week1_decision_chain_fixes.md Fix L-Bug-A"
    )

    import sqlite3
    conn = sqlite3.connect(f"{_project_root()}/data/app.db")
    n = conn.execute(
        "SELECT COUNT(*) FROM knowledge_items WHERE item_type='trade_case_study'"
    ).fetchone()[0]
    conn.close()
    assert n > 0, (
        "trade_case_study has 0 DB rows — Layer3 lessons would still be empty. "
        "See round2_week1_decision_chain_fixes.md Fix L-Bug-A"
    )


# ---------------------------------------------------------------------------
# LP-S02 — Layer3 guards query must filter by item_type=guard_candidate
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S02", "learning_pipeline", "§Layer3 guards", "L2a",
              "_build_layer3_wisdom guards must call list_knowledge_items(item_type='guard_candidate')"),
)
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
              "trading_decision.py must reference ps_lessons when building PI prompt"),
)
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


# ---------------------------------------------------------------------------
# LP-S05 — Attribution root: proposals MUST carry a real opportunity_id
# ---------------------------------------------------------------------------


_GUARDS_MODULE = "aletheia/knowledge/guards.py"


@spec_claim(
    SpecClaim("LP-S05", "learning_pipeline", "§Attribution root", "L2a",
              "create_paper_proposal() in trading_decision.py must pass a real "
              "opportunity_id (not the None literal) — the single root cause that "
              "starves every downstream learning artifact of entry-context"),
)
def test_proposal_carries_opportunity_id():
    """create_paper_proposal must NOT hard-code opportunity_id=None.

    That one line severs the attribution chain at the source: market_observer
    already writes scanner_family/universe/confidence into the opportunity, but
    the PI Agent drops opportunity_id at order time and nothing backfills it.
    """
    import pathlib
    src = (pathlib.Path(_project_root()) / _TRADING_DECISION).read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    hardcoded_none = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            tgt = node.func
            name = tgt.id if isinstance(tgt, ast.Name) else (tgt.attr if isinstance(tgt, ast.Attribute) else None)
            if name == "create_paper_proposal":
                found = True
                for kw in node.keywords:
                    if kw.arg == "opportunity_id" and isinstance(kw.value, ast.Constant) \
                            and kw.value.value is None:
                        hardcoded_none = True

    assert found, "create_paper_proposal call not found — refactor likely happened"
    assert not hardcoded_none, (
        "create_paper_proposal(opportunity_id=None) hard-codes None — attribution "
        "chain severed at the source. See round2_week1_decision_chain_fixes.md Fix L-Bug-C"
    )


# ---------------------------------------------------------------------------
# LP-S06 — Guard promotion: candidates MUST be reachable by the promoter
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S06", "learning_pipeline", "§Guard promotion", "L2a",
              "load_trading_guards must reach guard_candidate files so "
              "run_promotion_check can promote them (today it reads a different dir)",
              status="missing"),
)
@pytest.mark.xfail(reason="升级器 load_trading_guards(status='candidate') 读 data/knowledge/guards/ "
                          "但候选写 data/knowledge/guard_candidates/ 且前者不存在 → 0 candidates → 0 升级. "
                          "guards.py 不在冻结列表,但 load_trading_guards 被冻结的 paper_trader_ranker 调用, "
                          "故归 Round 2 统一处理 (LEARNING_LOOP_AUDIT_2026-06-26.md 断点 B2/B3). "
                          "Fix L-Bug-D in Round 2 解冻当天.", strict=True)
def test_guard_candidates_reachable_by_promoter():
    """guards.py must reference the guard_candidates directory so the promoter
    can see the堆积 candidates instead of an empty, non-existent guards/ dir.
    """
    import pathlib
    src = (pathlib.Path(_project_root()) / _GUARDS_MODULE).read_text(encoding="utf-8")
    assert "guard_candidates" in src, (
        "aletheia/knowledge/guards.py never references 'guard_candidates' — the "
        "promoter reads knowledge/guards/ while candidates are written to "
        "knowledge/guard_candidates/ → 0 promotions ever. "
        "See round2_week1_decision_chain_fixes.md Fix L-Bug-D"
    )
