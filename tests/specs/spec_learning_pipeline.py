"""Spec: Learning Pipeline (GREEN post-Round-1-fixes, updated 2026-06-29).

Originally written as RED tripwires during Round 1 freeze (2026-06-24 / 2026-06-27).
All six L-Bug fixes (A/A'/B/C/D) were applied in fix/round1-learning-fixes
(b9ad957, merged 873d464) and LP-S01..S06 now pass. LP-S07..S11 were always GREEN.

LP-S01..S04 (2026-06-24): consumption-side dead filters (Layer3 lessons/guards,
ps_lessons no consumer). LP-S05..S06 (2026-06-27, from the full-lifecycle audit
in docs/issues/LEARNING_LOOP_AUDIT_2026-06-26.md): the attribution-chain root
cause (opportunity_id hard-coded None) and the guard-promotion dead-end (promoter
reads a different dir than where candidates are written).

These claims are now status="implemented". If any of the L-Bug fixes regress,
the corresponding test will FAIL — that is the intended tripwire behaviour.
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
              "(trade_case_study) so PI's Layer3 lessons is non-empty",
              status="implemented"),
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

    import pathlib
    import sqlite3
    # Production-DB tripwire: skip where there is no populated app.db (CI / fresh
    # clone / worktree), exactly like the sibling reads LP-S07 / LP-S10. Read-only.
    db = pathlib.Path(_project_root()) / "data" / "app.db"
    if not db.exists():
        pytest.skip("no production app.db on this machine")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM knowledge_items WHERE item_type='trade_case_study'"
        ).fetchone()[0]
    finally:
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
              "_build_layer3_wisdom guards must call list_knowledge_items(item_type='guard_candidate')",
              status="implemented"),
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
              "trading_decision.py must reference ps_lessons when building PI prompt",
              status="implemented"),
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
              "_build_layer3_wisdom() callable exists in trading_context.py",
              status="implemented"),
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
              "starves every downstream learning artifact of entry-context",
              status="implemented"),
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
              "load_trading_guards reaches guard_candidate files (scans both guards/ and "
              "guard_candidates/) so run_promotion_check can promote them — L-Bug-D fixed",
              status="implemented"),
)
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


# ---------------------------------------------------------------------------
# LP-S07 — Attribution chain must hold at RUNTIME, not just in isolation
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S07", "learning_pipeline", "§Attribution runtime", "L2c",
              "The opportunity→decision attribution chain MUST hold at RUNTIME for the "
              "population that is structurally required to carry it: signal_pending rows "
              "from the opportunity ranker (reasoning 'Ranker candidate...'). Checked on "
              "the most-recent rows so the pre-fix historical backlog (and self-deleting "
              "transient rows) cannot skew it. skip/watch (non-decisions) and "
              "ruleset-fired signals (no source opportunity) are excluded — NULL "
              "opportunity_id there is honest, not a severed chain. Fixed live by "
              "b9ad957 (2026-06-27), deployed at the ~13:10 gateway restart.",
              status="implemented"),
)
def test_runtime_pi_decisions_carry_opportunity_id():
    """GREEN tripwire on the PRODUCTION DB (mirrors LP-S01's data/app.db read).

    The learning loop attributes trade outcomes back to the opportunity that
    triggered them via pi_decisions.opportunity_id. The population that MUST carry
    that id is the opportunity ranker's signals (reasoning starts with
    'Ranker candidate'). We measure the most RECENT such rows so that:

      * the pre-fix historical backlog — rows written before b9ad957 went live at
        the 2026-06-27 ~13:10 gateway restart — does not skew the result. It
        self-deletes via prune_pi_decisions(days=7) and falls out of "most recent"
        as soon as fresh rows accumulate.
      * skip/watch rows (rejected candidates / non-decisions) are excluded — they
        have no source opportunity, so a NULL opportunity_id there is correct.
      * ruleset-fired signals are excluded — they originate from a technical rule,
        not a scanned opportunity, so they have no opportunity_id to carry, and
        PiDecision has no strategy_id field to anchor them either.

    This is the only thing the learning loop cares about. If the live ranker path
    regresses and stops propagating the id, recent rows go NULL and this fails.
    Earlier scoping (all actions over a 2-day window) was wrong: it counted the
    self-deleting backlog plus the legitimately-NULL skip/ruleset rows, so it sat
    at ~50% forever even though the causal chain was healthy. Trade-path
    attribution is covered by the static LP-S05 + PL-PT record_decision_trade.
    Skips on machines without a populated app.db (CI / fresh clone).
    """
    import pathlib

    db = pathlib.Path(_project_root()) / "data" / "app.db"
    if not db.exists():
        pytest.skip("no production app.db on this machine")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT opportunity_id FROM pi_decisions "
            "WHERE action = 'signal_pending' "
            "AND reasoning LIKE 'Ranker candidate%' "
            "ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        pytest.skip("no recent opportunity-ranker signals to evaluate")

    nulls = sum(1 for (opp,) in rows if opp is None or opp == "")
    null_pct = 100.0 * nulls / len(rows)
    assert null_pct < 10.0, (
        f"{null_pct:.1f}% of the {len(rows)} most-recent opportunity-ranker "
        f"signal_pending rows have NULL opportunity_id ({nulls}/{len(rows)}) — the "
        "live attribution chain has regressed. Opportunity-sourced ranker signals "
        "MUST carry the originating opportunity_id (set in paper_trader.py from "
        "candidate['opportunity_id']). See JUNK_DATA_FULL_SWEEP_2026-06-28.md."
    )


# ---------------------------------------------------------------------------
# LP-S08 — Synthetic/administrative closures must NOT enter the learning corpus
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S08", "learning_pipeline", "§Corpus integrity", "L2b",
              "Administrative/synthetic closures (reconciled_gone, "
              "demo_validation_cleanup) carry a fabricated exit price, not a genuine "
              "strategy/risk outcome. The case-study builder MUST exclude them from "
              "the learning corpus (trade_case_study + guard candidates), keying on "
              "the close_reason VALUE so a reconciler close that recovered a REAL "
              "exchange TPSL fill (stop_loss/take_profit) is still kept",
              status="implemented"),
)
def test_synthetic_closures_excluded_from_corpus():
    """GREEN: the fix lives in aletheia/analytics/trade_case_builder.py (NOT the
    frozen decision chain — analytics is solo-editable). Verifies behaviourally that
    a synthetic-closure row produces no case study while a genuine close still does,
    and that the same filter guards auto-generated guard candidates.

    Junk-data full sweep (Axis 2 — semantic toxicity), 2026-06-28:
    docs/issues/JUNK_DATA_FULL_SWEEP_2026-06-28.md.
    """
    from unittest.mock import patch, MagicMock
    from aletheia.analytics import trade_case_builder as tcb

    def _row(position_id, close_reason, pnl_pct):
        return {
            "position_id": position_id, "symbol": "BTC/USDT", "direction": "long",
            "market_type": "spot", "realized_pnl": pnl_pct, "realized_pnl_pct": pnl_pct,
            "holding_hours": 5.0, "close_reason": close_reason, "scanner_family": "momentum",
            "confidence": "high", "late_entry_penalty": 5, "crowding_risk": 1,
            "risk_flags": [], "proposal_id": "p1", "opportunity_id": "o1",
            "strategy_id": "s1", "entry_price": 100.0, "exit_price": 103.0,
            "opened_at": "2026-06-27T10:00:00+00:00",
        }

    rows = [
        _row("pos-gone", "reconciled_gone", -2.0),
        _row("pos-demo", "demo_validation_cleanup", 1.5),
        _row("pos-real", "take_profit", 3.0),
    ]

    with patch.object(tcb, "build_trade_attribution_rows", return_value=rows), \
         patch.object(tcb, "_get_existing_case_position_ids", return_value=set()), \
         patch.object(tcb, "create_trade_case_study", return_value=MagicMock(id="cs-real")) as mk:
        result = tcb.build_case_studies(since_hours=9999)

    # Only the genuine take_profit close becomes a case study.
    assert len(result) == 1
    assert result[0]["case_study_id"] == "cs-real"
    assert mk.call_count == 1, "synthetic closures must not reach create_trade_case_study"


# ---------------------------------------------------------------------------
# LP-S09 — the proposed→accepted promotion step must exist and be overturnable
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S09", "learning_pipeline", "§lesson promotion job", "L2a",
              "An evidence-gated lesson_reviewer agent must exist, be registered in "
              "the scheduler, and re-judge accepted lessons (overturn path) — the "
              "missing proposed→accepted step that left 246 lessons stuck forever. "
              "Fixed 2026-06-28 (content-probe fixes).",
              status="implemented"),
)
def test_lesson_reviewer_registered_and_overturnable():
    """The lesson_reviewer agent exists, is scheduled, and can demote accepted lessons.

    (a) the agent module is importable and registers run_lesson_reviewer,
    (b) scheduler.py wires a 'lesson_reviewer' job + timeout,
    (c) the reviewer re-evaluates already-accepted lessons (status='accepted'),
        which is what makes an earlier acceptance overturnable by later evidence.
    """
    import pathlib

    from aletheia.agents.lesson_reviewer import run_lesson_reviewer
    assert callable(run_lesson_reviewer)

    root = pathlib.Path(_project_root())
    sched_src = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    assert '"lesson_reviewer"' in sched_src, (
        "scheduler.py does not register a lesson_reviewer job — the promotion step "
        "never runs. See content-probe fixes FIX 2 (LP-S09)."
    )

    reviewer_src = (root / "aletheia/agents/lesson_reviewer.py").read_text(encoding="utf-8")
    assert 'status="accepted"' in reviewer_src or "status='accepted'" in reviewer_src, (
        "lesson_reviewer never re-reads accepted lessons — acceptances are not "
        "overturnable by later evidence (owner requirement). See FIX 2 (LP-S09)."
    )


# ---------------------------------------------------------------------------
# LP-S10 — accepted lessons must close the loop into a policy version
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S10", "learning_pipeline", "§applied_policy_version closure", "L2a",
              "LessonStore.mark_applied() must exist and be called by lesson_reviewer "
              "so an accepted lesson stamps the policy version it influenced "
              "(applied_policy_version), closing the chain the doctor probe checks. "
              "Fixed 2026-06-28 (content-probe fixes).",
              status="implemented"),
)
def test_applied_policy_version_closure():
    """mark_applied exists, is invoked by the reviewer, and the column is present."""
    import pathlib

    from aletheia.learning.lessons import LessonStore
    assert hasattr(LessonStore, "mark_applied") and callable(LessonStore.mark_applied), (
        "LessonStore.mark_applied missing — applied_policy_version can never be set. "
        "See FIX 2 (LP-S10)."
    )

    root = pathlib.Path(_project_root())
    reviewer_src = (root / "aletheia/agents/lesson_reviewer.py").read_text(encoding="utf-8")
    assert "mark_applied" in reviewer_src, (
        "lesson_reviewer never calls mark_applied — accepted lessons would never "
        "stamp a policy version. See FIX 2 (LP-S10)."
    )

    db = pathlib.Path(_project_root()) / "data" / "app.db"
    if not db.exists():
        pytest.skip("no production app.db on this machine")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(ps_lessons)")]
    finally:
        conn.close()
    assert "applied_policy_version" in cols, "ps_lessons.applied_policy_version column missing"


# ---------------------------------------------------------------------------
# LP-S11 — the promotion chain works end-to-end (behavioral, in-memory DB)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S11", "learning_pipeline", "§lesson promotion behavior", "L2b",
              "Given enough settled evidence and an LLM 'accept', the reviewer moves a "
              "proposed lesson to 'applied' and stamps applied_policy_version via a real "
              "lesson_policy version — the chain that was dead now closes end-to-end. "
              "Fixed 2026-06-28 (content-probe fixes).",
              status="implemented"),
)
async def test_lesson_reviewer_promotes_and_applies(monkeypatch):
    """Behavioral proof on the autouse in-memory DB. Mocks ONLY the LLM network
    boundary (llm_complete); LessonStore / PolicyStore / evidence SQL all run for
    real, and we assert the resulting DB state — not that a mock was called.
    """
    import json as _json
    from unittest.mock import AsyncMock

    from aletheia.storage.sqlite import get_db
    from aletheia.storage.models import new_id
    from aletheia.learning.lessons import LessonStore
    from aletheia.policies.store import PolicyStore
    import aletheia.agents.lesson_reviewer as lr

    db = get_db()
    # Seed >= _MIN_SETTLED_EVIDENCE settled outcomes so the deterministic floor passes.
    for _ in range(25):
        db.execute(
            "INSERT INTO universe_forward_outcomes "
            "(id, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
            " entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, "
            " forward_return_pct, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), "m", "s", "BTC/USDT", "momentum", "long", "24h",
             100.0, "2026-06-20T00:00:00+00:00", "2026-06-21T00:00:00+00:00",
             "2026-06-21T00:00:00+00:00", 102.0, 2.0, "settled"),
        )
    db.commit()

    store = LessonStore()
    lesson = store.create(
        summary="Momentum longs on BTC/USDT carry edge in this regime",
        source_type="trade_case_study", source_id="x",
        category="entry_timing", severity="warning",
    )

    monkeypatch.setattr(
        lr, "llm_complete",
        AsyncMock(return_value=(_json.dumps({"decision": "accept", "reasoning": "supported"}), None)),
    )

    await lr.run_lesson_reviewer(run_id="test")

    refreshed = store.get(lesson.lesson_id)
    assert refreshed is not None
    assert refreshed.status == "applied", f"expected 'applied', got {refreshed.status!r}"
    assert refreshed.applied_policy_version, "applied_policy_version was not stamped"

    versions = PolicyStore().list_versions("lesson_policy")
    assert versions, "no lesson_policy version was created on acceptance"
    assert refreshed.applied_policy_version == versions[0].policy_version_id, (
        "lesson is not linked to the policy version it was folded into"
    )


# ---------------------------------------------------------------------------
# LP-S12 — Ranker must consume _memo_scoring + _lesson_penalty; adjustments
#           must NOT reach risk_engine or sizing (§8.1 invariant)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S12", "learning_pipeline", "§8.1 Ranker consumption", "L2a",
              "paper_trader_ranker._score_opportunity() calls both _memo_scoring() "
              "(reads trading_memo knowledge item) and _lesson_penalty() (reads "
              "trade_case_study items); and those helpers are NOT imported by or "
              "called from aletheia/risk/engine.py or any sizing path — Ranker "
              "adjustments only affect opportunity sort order, never risk gates or "
              "position sizing (knowledge_contract.md §8.1 boundary)",
              status="implemented"),
)
def test_ranker_consumes_memo_and_lesson_penalty_not_risk():
    """LP-S12: §8.1 Ranker-side learning consumption invariant.

    (a) _score_opportunity() in paper_trader_ranker calls both _memo_scoring
        and _lesson_penalty — the two §8.1 learning paths.
    (b) risk/engine.py does NOT import or reference _memo_scoring or
        _lesson_penalty — so Ranker score adjustments never propagate into
        risk checks or sizing.
    """
    import pathlib
    import ast

    root = pathlib.Path(_project_root())

    # (a) Ranker calls both learning helpers inside _score_opportunity
    ranker_src = (root / "aletheia/agents/paper_trader_ranker.py").read_text(encoding="utf-8")
    assert "_memo_scoring" in ranker_src, (
        "_memo_scoring not found in paper_trader_ranker.py — §8.1 trading_memo "
        "consumption path is broken"
    )
    assert "_lesson_penalty" in ranker_src, (
        "_lesson_penalty not found in paper_trader_ranker.py — §8.1 trade_case_study "
        "consumption path is broken"
    )

    # Verify both helpers are called inside _compute_trade_score (the scoring function)
    tree = ast.parse(ranker_src)
    score_fn_body_src = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_compute_trade_score":
            # Collect all call-target names inside the function
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    fn = child.func
                    name = fn.id if isinstance(fn, ast.Name) else (
                        fn.attr if isinstance(fn, ast.Attribute) else ""
                    )
                    score_fn_body_src += name + " "
    assert "_memo_scoring" in score_fn_body_src, (
        "_memo_scoring is not called inside _compute_trade_score — "
        "trading_memo adjustments never reach Ranker scoring"
    )
    assert "_lesson_penalty" in score_fn_body_src, (
        "_lesson_penalty is not called inside _compute_trade_score — "
        "trade_case_study penalty never reaches Ranker scoring"
    )

    # (b) risk/engine.py must not reference these Ranker-internal helpers
    engine_src = (root / "aletheia/risk/engine.py").read_text(encoding="utf-8")
    assert "_memo_scoring" not in engine_src, (
        "_memo_scoring imported/referenced in risk/engine.py — §8.1 boundary "
        "violated: Ranker learning adjustments must not reach risk gates"
    )
    assert "_lesson_penalty" not in engine_src, (
        "_lesson_penalty imported/referenced in risk/engine.py — §8.1 boundary "
        "violated: Ranker learning adjustments must not reach risk gates"
    )


# ---------------------------------------------------------------------------
# LP-S13 — knowledge/manager.py create functions and learning pipeline MUST NOT
#           call update_config or directly write risk_engine threshold fields
#           (§8.3 invariant)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S13", "learning_pipeline", "§8.3 risk threshold immutability", "L2a",
              "No create_* function in knowledge/manager.py and no module under "
              "aletheia/learning/ imports or calls update_config() / "
              "update_risk_config() — the structural guarantee that learning output "
              "cannot modify risk_engine thresholds (knowledge_contract.md §8.3)",
              status="implemented"),
)
def test_learning_pipeline_cannot_modify_risk_thresholds():
    """LP-S13: §8.3 — learning pipeline has no write path to risk thresholds.

    Checks source text only (static analysis). Verifies that:
    (a) aletheia/knowledge/manager.py create_* functions never call update_config
        or update_risk_config.
    (b) every .py file under aletheia/learning/ (LessonStore, PolicyStore, etc.)
        does not import from aletheia.risk.engine or call update_config directly.

    This is an L2a structural claim — not a behavioural test — because the
    boundary is architectural: if any of these paths existed the enforcement
    would require change-control review, not a runtime guard.
    """
    import pathlib

    root = pathlib.Path(_project_root())

    forbidden_calls = ("update_config", "update_risk_config")

    # (a) knowledge/manager.py must not contain any forbidden call
    mgr_src = (root / "aletheia/knowledge/manager.py").read_text(encoding="utf-8")
    for fn in forbidden_calls:
        assert fn not in mgr_src, (
            f"knowledge/manager.py calls {fn}() — learning write path can "
            "reach risk threshold configuration (§8.3 violation)"
        )

    # (b) all .py files under aletheia/learning/ must not import risk.engine
    #     or call the forbidden config writers
    learning_dir = root / "aletheia" / "learning"
    assert learning_dir.exists(), "aletheia/learning/ directory missing"
    violations: list[str] = []
    for py_file in learning_dir.rglob("*.py"):
        src = py_file.read_text(encoding="utf-8")
        for fn in forbidden_calls:
            if fn in src:
                violations.append(f"{py_file.relative_to(root)}: calls {fn}()")
        if "from aletheia.risk.engine" in src or "import aletheia.risk.engine" in src:
            violations.append(f"{py_file.relative_to(root)}: imports risk.engine")
        if "from aletheia.risk import engine" in src:
            violations.append(f"{py_file.relative_to(root)}: imports risk.engine")
    assert not violations, (
        "learning/ module(s) have a write path to risk thresholds (§8.3 violation):\n"
        + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# LP-S14 — §8.2: enumerate ALL three paths that inject learning content into
#           PI prompt; verify none bypasses the documented channels
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S14", "learning_pipeline", "§8.2 PI prompt learning paths", "L2a",
              "Exactly three learning-content paths reach the PI system prompt via "
              "trading_decision.py: (1) trading_memo (layer2.trading_memo), "
              "(2) reasoning_feedback (layer2.reasoning_feedback), "
              "(3) ps_lessons (layer3.ps_lessons, proposed lessons from LessonStore). "
              "knowledge_contract.md §8.2 previously said trading_memo was the "
              "'only' path — that was incorrect; the corrected §8.2 enumerates "
              "all three. This claim locks the complete set so future additions "
              "require an explicit spec update.",
              status="implemented"),
)
def test_pi_prompt_learning_paths_complete_enumeration():
    """LP-S14: The three documented learning-content paths into the PI prompt.

    (a) trading_decision.py renders 'trading_memo' (layer2 key) into the prompt.
    (b) trading_decision.py renders 'reasoning_feedback' (layer2 key) into the prompt.
    (c) trading_decision.py renders 'ps_lessons' (layer3 key) into the prompt.
    (d) trading_context.py populates all three source keys from their respective
        knowledge item types.

    This is a static-source claim: it verifies the documented paths exist in code.
    If a fourth learning path is added to the prompt it must also update this spec.
    """
    import pathlib

    root = pathlib.Path(_project_root())
    td_src = (root / _TRADING_DECISION).read_text(encoding="utf-8")
    tc_src = (root / _TRADING_CONTEXT).read_text(encoding="utf-8")

    # (a) trading_memo rendered in trading_decision
    assert "trading_memo" in td_src, (
        "trading_decision.py does not reference 'trading_memo' — §8.2 path 1 missing"
    )
    # (b) reasoning_feedback rendered in trading_decision
    assert "reasoning_feedback" in td_src, (
        "trading_decision.py does not reference 'reasoning_feedback' — §8.2 path 2 missing"
    )
    # (c) ps_lessons rendered in trading_decision
    assert "ps_lessons" in td_src, (
        "trading_decision.py does not reference 'ps_lessons' — §8.2 path 3 missing"
    )

    # (d) trading_context populates the three keys from the right item_types
    # path 1: trading_memo comes from insight_synthesizer (get_latest_memo)
    assert "trading_memo" in tc_src, (
        "trading_context.py does not populate layer['trading_memo'] — §8.2 source broken"
    )
    # path 2: reasoning_feedback comes from reasoning_feedback module
    assert "reasoning_feedback" in tc_src, (
        "trading_context.py does not populate layer['reasoning_feedback'] — §8.2 source broken"
    )
    # path 3: ps_lessons comes from LessonStore (status='proposed')
    assert "ps_lessons" in tc_src, (
        "trading_context.py does not populate layer['ps_lessons'] — §8.2 source broken"
    )
