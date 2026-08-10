"""Spec: Learning Pipeline (GREEN post-Round-1-fixes, updated 2026-06-29).

Originally written as RED tripwires during Round 1 freeze (2026-06-24 / 2026-06-27).
All six L-Bug fixes (A/A'/B/C/D) were applied in fix/round1-learning-fixes
(b9ad957, merged 873d464) and LP-S01..S06 now pass. LP-S07..S11 were always GREEN.

LP-S01..S04 (2026-06-24): consumption-side dead filters (Layer3 lessons/guards,
ps_lessons no consumer). LP-S05..S06 (2026-06-27, from the full-lifecycle audit
in docs/archive/legacy/issues/audits/LEARNING_LOOP_AUDIT_2026-06-26.md): the attribution-chain root
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
    test verifies that query alignment hermetically (source AST). Runtime
    non-emptiness is a machine-dependent data-health concern for `aletheia doctor`,
    not a spec assertion (#20: the old `assert n>0` against the real app.db failed
    on any machine/CI/worktree without seeded case studies).
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
              "trading_decision.py must reference ps_lessons when building System Operator prompt",
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
    the System Operator drops opportunity_id at order time and nothing backfills it.
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
    """GREEN tripwire on the selected runtime app.db.

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
    from aletheia.storage.sqlite import db_path

    db = db_path()
    if not db.exists():
        pytest.skip("no production app.db on this machine")

    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pi_decisions'"
        ).fetchone()
        if not exists:
            pytest.skip("production app.db has no pi_decisions table to evaluate")
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
    docs/archive/legacy/issues/audits/JUNK_DATA_FULL_SWEEP_2026-06-28.md.
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
            # Case studies are built from CLOSED positions (windowed on close time, #16),
            # so a realistic attribution row carries closed_at.
            "closed_at": "2026-06-27T15:00:00+00:00",
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

    from aletheia.storage.sqlite import db_path

    db = db_path()
    if not db.exists():
        pytest.skip("no production app.db on this machine")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ps_lessons'"
        ).fetchone()
        if not exists:
            pytest.skip("production app.db has no ps_lessons table to evaluate")
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
    from aletheia.learning.skills import SkillStore
    from aletheia.policies.store import PolicyStore
    import aletheia.agents.lesson_reviewer as lr

    db = get_db()
    # Seed >= _MIN_SETTLED_EVIDENCE settled outcomes so the deterministic floor passes.
    for _ in range(25):
        db.execute(
            "INSERT INTO universe_forward_outcomes "
            "(id, outcome_candidate_key, member_id, snapshot_id, symbol, scanner_family, direction, horizon, "
            " entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, "
            " forward_return_pct, gross_return_pct, directional_return_pct, "
            " net_return_pct, min_tradable_edge_pct, cost_model_version, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id(), new_id(), "m", "s", "BTC/USDT", "momentum", "long", "24h",
             100.0, "2026-07-12T08:00:00+00:00", "2026-07-12T09:00:00+00:00",
             "2026-07-12T09:00:00+00:00", 102.0, 2.0, 2.0, 2.0,
             1.8, 0.05, "v1", "settled"),
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
    skills = SkillStore().list_all(scope="learning_advisory")
    assert len(skills) == 1
    assert skills[0].status == "draft"
    assert skills[0].name == f"lesson:{lesson.lesson_id}"


async def test_lesson_reviewer_reuses_policy_after_partial_apply_failure():
    from aletheia.learning.lessons import LessonStore
    from aletheia.policies.store import PolicyStore
    import aletheia.agents.lesson_reviewer as lr

    store = LessonStore()
    first = store.create("first", "trade_case_study", "case-1")
    second = store.create("second", "trade_case_study", "case-2")
    store.accept(first.lesson_id)
    store.accept(second.lesson_id)

    policies = PolicyStore()
    version = policies.create_version(
        "lesson_policy",
        {"accepted_lesson_ids": [first.lesson_id, second.lesson_id]},
        created_by="lesson_reviewer",
    )
    store.mark_applied(first.lesson_id, version.policy_version_id)

    await lr.run_lesson_reviewer(run_id="test")

    assert store.get(second.lesson_id).applied_policy_version == version.policy_version_id
    assert len(policies.list_versions("lesson_policy")) == 1


# ---------------------------------------------------------------------------
# LP-S12 — Ranker routes proposed memo/case deltas through its authority boundary;
#           they must NOT reach risk_engine or sizing (§8.1 invariant)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S12", "learning_pipeline", "§8.1 Ranker consumption", "L2a",
              "paper_trader_ranker._compute_trade_score() calls both _memo_scoring() "
              "(reads trading_memo knowledge item) and _lesson_penalty() (reads "
              "trade_case_study items), then routes each proposed delta through "
              "_authorized_ranker_delta(); those helpers are NOT imported by or "
              "called from aletheia/risk/engine.py or any sizing path. Unsupported "
              "measurement stays at the zero-delta baseline.",
              status="implemented"),
)
def test_ranker_consumes_memo_and_lesson_penalty_not_risk():
    """LP-S12: §8.1 Ranker-side learning consumption invariant.

    (a) _compute_trade_score() calls both proposed-delta producers and the
        prospective authority adapter; current unsupported paths remain zero.
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
    assert "_authorized_ranker_delta" in score_fn_body_src, (
        "Ranker case/memo deltas bypassed the prospective authority boundary"
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
#           System Operator prompt; verify none bypasses the documented channels
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S14", "learning_pipeline", "§8.2 System Operator prompt learning paths", "L2a",
              "Exactly three learning-content paths reach the System Operator prompt via "
              "trading_decision.py: (1) trading_memo (layer2.trading_memo), "
              "(2) reasoning_feedback (layer2.reasoning_feedback), "
              "(3) ps_lessons (layer3.ps_lessons, applied lessons from LessonStore). "
              "knowledge_contract.md §8.2 previously said trading_memo was the "
              "'only' path — that was incorrect; the corrected §8.2 enumerates "
              "all three. This claim locks the complete set so future additions "
              "require an explicit spec update.",
              status="implemented"),
)
def test_pi_prompt_learning_paths_complete_enumeration():
    """LP-S14: The three documented learning-content paths into the System Operator prompt.

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
    # path 3: ps_lessons comes from LessonStore (status='applied')
    assert "ps_lessons" in tc_src, (
        "trading_context.py does not populate layer['ps_lessons'] — §8.2 source broken"
    )


# ---------------------------------------------------------------------------
# KL-S01 — 两路并行学习触发器：个例 reflection (post_trade_reviewer) +
#           批次归纳 (insight_synthesizer / dreaming_daily) 相互独立
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("KL-S01", "learning_pipeline", "§two-track triggers", "L2a",
              "两路并行学习触发器结构：个例 reflection 由 post_trade_reviewer job "
              "（内部调用 trade_case_builder）在平仓后触发（interval 型）；"
              "批次归纳由独立的 insight_synthesizer / dreaming_daily job 触发（cron 型）；"
              "两路均已注册在 scheduler 且触发源类型互相独立，不存在单点合并。",
              status="implemented"),
)
def test_two_track_learning_triggers_independent():
    """KL-S01: 两路学习触发器独立注册在 scheduler.

    (a) 个例路径: post_trade_reviewer job 已注册（interval 型），其实现内部
        调用 trade_case_builder.build_case_studies。
    (b) 批次归纳路径: insight_synthesizer + dreaming_daily 已注册（cron 型）。
    (c) 两路触发类型不同（interval vs cron），验证独立性。
    """
    import pathlib

    root = pathlib.Path(_project_root())
    sched_src = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")

    # (a) 个例 review job 已注册
    assert '"post_trade_reviewer"' in sched_src, (
        "scheduler.py 未注册 post_trade_reviewer job — 个例 reflection 路径不存在"
    )

    # (b) 批次归纳 jobs 均已注册
    assert '"insight_synthesizer"' in sched_src, (
        "scheduler.py 未注册 insight_synthesizer job — 批次归纳路径断开"
    )
    assert '"dreaming_daily"' in sched_src, (
        "scheduler.py 未注册 dreaming_daily job — 批次 dreaming 路径断开"
    )

    # (c) 触发类型独立: post_trade_reviewer 是 interval，dreaming_daily 是 cron
    # 验证方法：在 scheduler 源码中 post_trade_reviewer 的 add_job 调用使用 "interval"
    #           而 dreaming_daily 和 insight_synthesizer 使用 "cron"
    import ast
    tree = ast.parse(sched_src)
    trigger_types: dict[str, str] = {}  # job_id -> trigger type
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else ""
            )
            if fn_name == "add_job":
                # Extract id= kwarg and positional trigger arg
                job_id = None
                trigger = None
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        job_id = kw.value.value
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    trigger = node.args[1].value
                if job_id and trigger:
                    trigger_types[job_id] = trigger

    assert trigger_types.get("post_trade_reviewer") == "interval", (
        f"post_trade_reviewer trigger 期望 'interval'，实际={trigger_types.get('post_trade_reviewer')!r}"
    )
    assert trigger_types.get("dreaming_daily") == "cron", (
        f"dreaming_daily trigger 期望 'cron'，实际={trigger_types.get('dreaming_daily')!r}"
    )
    assert trigger_types.get("insight_synthesizer") == "cron", (
        f"insight_synthesizer trigger 期望 'cron'，实际={trigger_types.get('insight_synthesizer')!r}"
    )

    # (d) post_trade_reviewer 的实现内部调用 trade_case_builder
    reviewer_src = (root / "aletheia/agents/post_trade_reviewer.py").read_text(encoding="utf-8")
    assert "trade_case_builder" in reviewer_src, (
        "post_trade_reviewer.py 未引用 trade_case_builder — 个例 reflection "
        "与 case_builder 的链接断开"
    )


# ---------------------------------------------------------------------------
# KL-S02 — missing_tripwire: risk/engine.py 不得出现 learning override 接口
#           (Phase 2 风控开放 defer 的防护 tripwire)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("KL-S02", "learning_pipeline", "§risk threshold immutability tripwire", "L2a",
              "missing_tripwire：risk/engine.py 中不存在任何名为 "
              "accept_learning_override、apply_learning_update、set_risk_threshold "
              "或类似语义的公开函数/方法 — Phase 2 风控开放 defer 承诺未被意外打穿。"
              "接口一旦出现则此测试失败，强制触发 change-control 审查。",
              status="implemented"),
)
def test_risk_engine_no_learning_override_interface():
    """KL-S02: risk/engine.py 不得出现 Phase 2 learning override 接口.

    这是一个防穿透 tripwire（永远 GREEN 为正常状态）：
    - 当前 Phase 1：接口不存在 → assert 全部通过 → 测试 GREEN
    - Phase 2 实施时：接口出现 → assert 失败 → 强制触发 change-control 审查

    使用 AST 扫描确保检测精度（不被注释/字符串字面量误报）。
    """
    import pathlib
    import ast

    root = pathlib.Path(_project_root())
    engine_src = (root / "aletheia/risk/engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine_src)

    forbidden_patterns = (
        "learning_override",
        "learning_update",
        "set_risk_threshold",
        "accept_learning",
    )

    violating: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for pattern in forbidden_patterns:
                if pattern in node.name:
                    violating.append(node.name)

    assert not violating, (
        f"risk/engine.py 出现了 Phase 2 learning override 接口: {violating} — "
        "这是 KL-S02 tripwire 触发的 change-control 审查信号。"
        "在实施前必须更新 knowledge_contract.md §8.3 并通过 spec review。"
    )


# ---------------------------------------------------------------------------
# KL-S03 — 批次模式归纳三核心 job 均已注册在 scheduler
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("KL-S03", "learning_pipeline", "§batch synthesis jobs", "L2a",
              "批次模式归纳三个核心 job 均已注册在 scheduler: "
              "insight_synthesizer（trading_memo 合成）、"
              "confidence_calibration（置信度校准）、"
              "dreaming_daily（批次 dreaming）— 保证'定期批次归纳'不是空话。",
              status="implemented"),
)
def test_batch_synthesis_jobs_all_registered():
    """KL-S03: 三个批次归纳 job 均已在 scheduler 注册.

    检查 scheduler.py 中同时存在三个 job id 字符串，对应三条独立的
    add_job 调用（三路批次学习的基础设施保证）。
    """
    import pathlib

    root = pathlib.Path(_project_root())
    sched_src = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")

    missing: list[str] = []
    for job_id in ("insight_synthesizer", "confidence_calibration", "dreaming_daily"):
        if f'"{job_id}"' not in sched_src:
            missing.append(job_id)

    assert not missing, (
        f"以下批次归纳 job 未在 scheduler.py 中注册: {missing} — "
        "批次学习基础设施不完整。"
    )

    # 额外验证：三个 job id 均出现在 add_job 调用的 id= 参数中
    # （用 AST 扫描，防止只匹配注释/字符串字面量）
    import ast
    tree = ast.parse(sched_src)
    registered_ids: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else ""
            )
            if fn_name == "add_job":
                for kw in node.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        registered_ids.add(kw.value.value)
    for job_id in ("insight_synthesizer", "confidence_calibration", "dreaming_daily"):
        assert job_id in registered_ids, (
            f"{job_id} 未出现在任何 scheduler.add_job(id=...) 调用中 — "
            "job 可能只是字符串字面量引用，并未真正注册为定时任务。"
            f"已注册的 job ids: {sorted(registered_ids)}"
        )


@spec_claim(
    SpecClaim(
        "LP-S16", "learning_pipeline", "§batch synthesis startup catch-up", "L2a",
        "insight_synthesizer keeps its daily cron cadence and participates in the "
        "durable Learning startup catch-up, so a missed overnight window runs once "
        "without repeating on every gateway restart.",
        status="implemented",
    )
)
def test_insight_synthesizer_has_startup_catchup():
    import ast
    import pathlib

    root = pathlib.Path(_project_root())
    sched_src = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(sched_src)

    target = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = keywords.get("id")
        if isinstance(job_id, ast.Constant) and job_id.value == "insight_synthesizer":
            target = node
            break

    assert target is not None, "insight_synthesizer scheduler job is not registered"
    assert len(target.args) >= 2 and isinstance(target.args[1], ast.Constant)
    assert target.args[1].value == "cron", "daily cron cadence must remain intact"

    catchup = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = keywords.get("id")
        if isinstance(job_id, ast.Constant) and job_id.value == "learning_startup_catchup":
            catchup = node
            break
    assert catchup is not None
    rendered = ast.unparse(catchup)
    assert "minutes=5" in rendered.replace(" ", "")
    assert "_run_learning_startup_catchup" in rendered
    assert "daily_catchup_due" in sched_src


@spec_claim(
    SpecClaim(
        "LP-S17", "learning_pipeline", "§Serenity daily scheduling continuity", "L2a",
        "dreaming_daily retains its 02:00 CST cron while a separate one-shot "
        "serenity_startup_catchup runs within five minutes of scheduler startup. "
        "The catch-up uses a durable CST batch key instead of rerunning on every restart.",
        status="implemented",
    )
)
def test_serenity_daily_has_durable_startup_catchup():
    import ast
    import pathlib

    root = pathlib.Path(_project_root())
    sched_src = (root / "aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(sched_src)
    jobs: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
        job_id = keywords.get("id")
        if isinstance(job_id, ast.Constant) and isinstance(job_id.value, str):
            jobs[job_id.value] = node

    daily = jobs.get("dreaming_daily")
    catchup = jobs.get("serenity_startup_catchup")
    assert daily is not None and len(daily.args) >= 2
    assert isinstance(daily.args[1], ast.Constant) and daily.args[1].value == "cron"
    daily_kw = {kw.arg: kw.value for kw in daily.keywords if kw.arg}
    assert ast.literal_eval(daily_kw["hour"]) == 2
    assert ast.literal_eval(daily_kw["minute"]) == 0

    assert catchup is not None, "Serenity needs a startup catch-up after a missed daily cron"
    assert len(catchup.args) >= 2 and isinstance(catchup.args[1], ast.Constant)
    assert catchup.args[1].value == "date"
    catchup_kw = {kw.arg: kw.value for kw in catchup.keywords if kw.arg}
    rendered = ast.unparse(catchup_kw["run_date"])
    assert "datetime.now()" in rendered and "timedelta" in rendered
    assert "minutes=4" in rendered.replace(" ", "")
    assert "_enqueue_serenity_research_if_due" in sched_src
    assert "serenity:scheduled:" in sched_src


# ---------------------------------------------------------------------------
# KL-B01 (L2b implemented) — 时间衰减推翻路径（three-path fusion §方案2）
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("KL-B01", "learning_pipeline", "§time decay overturn path", "L2b",
              "时间衰减推翻路径（三路融合之一）："
              "trading_memo knowledge item 在生成超过 N 天后，其 regime_playbook 条目的"
              "有效边缘权重应随时间衰减（half-life 模型），使旧 memo 影响力自动降低，"
              "即使没有显式 overturn 信号。"
              "来源: learning_mechanism_research_plan.md §已定案 方案2。",
              status="implemented"),
)
def test_time_decay_overturn_path_exists():
    """KL-B01: trading memo/regime playbook half-life decay exists."""
    import pathlib
    import ast

    root = pathlib.Path(_project_root())

    # 精准关键字：必须是 trading_memo / regime_playbook 的 half-life 权重衰减函数名
    # 不匹配 trade_path.py 的 time_exit_decay（trade 分类标签）
    # 不匹配 exit_policy_advisor 的 time_decay_frac（统计变量）
    # 不匹配 retrieval.py 的 _recency_weight（检索层通用衰减，非 memo half-life）
    decay_fn_patterns = ("memo_decay", "memo_half_life", "regime_decay", "playbook_decay",
                         "half_life_weight", "memo_time_weight")

    # 通过 AST 扫描函数名（FunctionDef/AsyncFunctionDef），不扫字符串字面量
    found_fns: list[str] = []
    for search_dir in ("aletheia/analytics", "aletheia/learning", "aletheia/knowledge"):
        d = root / search_dir
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            try:
                src = py_file.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except Exception:
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for pattern in decay_fn_patterns:
                        if pattern in node.name:
                            found_fns.append(f"{py_file.relative_to(root)}::{node.name}")

    assert found_fns, (
        "未在 aletheia/analytics/、aletheia/learning/ 或 aletheia/knowledge/ 中找到 "
        "trading_memo/regime_playbook 专属的 half-life 时间衰减函数 "
        f"（目标函数名模式: {decay_fn_patterns}）— "
        "Phase 2 时间衰减推翻路径缺失或发生回归。"
    )


# ---------------------------------------------------------------------------
# LP-IDEM — post_trade_reviewer MUST be idempotent per fills-window
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-IDEM", "learning_pipeline", "§post_trade_review idempotency", "L2a",
              "post_trade_reviewer MUST skip LLM and DB write when the same set of "
              "fills was already reviewed. A window key (SHA256 of sorted fill-IDs) is "
              "tagged on each written review and checked before the LLM call. "
              "Fixed 2026-06-30: previously every 4h run re-reviewed the same fill "
              "pool, generating 2012+ redundant reviews.",
              status="implemented"),
)
def test_post_trade_reviewer_idempotent():
    """LP-IDEM: idempotency helpers exist and produce stable keys.

    (a) _fills_window_key exists and is order-independent (same fills in different
        order → same key; extra fill → different key).
    (b) _review_already_written exists and queries tags with a window: prefix.
    (c) create_post_trade_review in knowledge/manager.py accepts a `tags` parameter
        so the window key can be stored.
    """
    import pathlib
    import inspect

    from aletheia.agents.post_trade_reviewer import _fills_window_key, _review_already_written

    fills_a = [{"id": "f1"}, {"id": "f2"}]
    fills_b = [{"id": "f2"}, {"id": "f1"}]  # reversed order
    fills_c = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]

    assert _fills_window_key("s", fills_a) == _fills_window_key("s", fills_b), (
        "_fills_window_key must be order-independent"
    )
    assert _fills_window_key("s", fills_a) != _fills_window_key("s", fills_c), (
        "_fills_window_key must change when a new fill is added"
    )

    # Different strategy_ids must yield different keys even for the same fills
    assert _fills_window_key("s1", fills_a) != _fills_window_key("s2", fills_a), (
        "_fills_window_key must be scoped per strategy_id"
    )

    root = pathlib.Path(_project_root())
    reviewer_src = (root / "aletheia/agents/post_trade_reviewer.py").read_text(encoding="utf-8")
    assert "window:" in reviewer_src, (
        "window: tag prefix missing from post_trade_reviewer.py — "
        "idempotency tag not written"
    )

    # create_post_trade_review must accept tags
    from aletheia.knowledge.manager import create_post_trade_review
    sig = inspect.signature(create_post_trade_review)
    assert "tags" in sig.parameters, (
        "create_post_trade_review() must accept a `tags` parameter to store the window key"
    )


# ---------------------------------------------------------------------------
# LP-EVIDENCE — dreaming_agent lessons MUST carry quantitative evidence
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-EVIDENCE", "learning_pipeline", "§lesson evidence chain", "L2a",
              "dreaming_agent._extract_lessons must write quantitative evidence "
              "(outcome_counts, avg_pnl_pct, factor_keys, scanner_families) into "
              "ps_lessons.evidence_json so lesson_reviewer can judge by numbers, "
              "not just LLM narrative. Fixed 2026-06-30: previously evidence_json "
              "was always {} / [] causing 246 lessons to be stuck 'proposed' forever.",
              status="implemented"),
)
def test_dreaming_agent_lesson_evidence_populated():
    """LP-EVIDENCE: _extract_lessons enriches ps_lesson evidence with real PnL data.

    (a) LessonStore.create() is called with evidence= keyword containing at
        least the 'outcome_counts' key.
    (b) The aggregate evidence dict is built from trade_case_study file metadata
        (deterministic fallback that works even if the LLM skips the evidence field).
    (c) LessonStore.create() accepts `evidence` kwarg (has been the API since day 0).
    """
    import pathlib
    import ast

    root = pathlib.Path(_project_root())
    dreaming_src = (root / "aletheia/agents/dreaming_agent.py").read_text(encoding="utf-8")

    # (a) store.create is called with evidence= kwarg
    tree = ast.parse(dreaming_src)
    has_evidence_kwarg = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else ""
            )
            if fname == "create":
                for kw in node.keywords:
                    if kw.arg == "evidence":
                        has_evidence_kwarg = True
    assert has_evidence_kwarg, (
        "LessonStore.create() is never called with evidence= in dreaming_agent.py — "
        "lessons will have empty evidence_json and cannot be promoted by lesson_reviewer"
    )

    # (b) deterministic aggregate evidence is built (outcome_counts key)
    assert "outcome_counts" in dreaming_src, (
        "'outcome_counts' not found in dreaming_agent.py — "
        "deterministic PnL evidence aggregation is missing"
    )
    assert "avg_pnl_pct" in dreaming_src, (
        "'avg_pnl_pct' not found in dreaming_agent.py — "
        "deterministic PnL evidence aggregation is missing"
    )

    # (c) LessonStore.create signature accepts evidence
    from aletheia.learning.lessons import LessonStore
    import inspect
    sig = inspect.signature(LessonStore.create)
    assert "evidence" in sig.parameters, (
        "LessonStore.create() must accept an `evidence` parameter"
    )


# ---------------------------------------------------------------------------
# LP-S15 — factor_feedback (signal gate) excludes synthetic/thrash closes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S15", "learning_pipeline", "§factor_feedback excludes synthetic closes", "L2b",
              "build_factor_feedback / lookup_factor_signal must exclude synthetic "
              "closes (reconciled_gone etc.) from win-rate/expectancy — reconciler "
              "orphan sweeps are not trade outcomes and were mis-teaching the signal "
              "gate. See ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.",
              status="implemented"),
)
def test_factor_feedback_excludes_synthetic_closes():
    """Mock attribution rows (no DB): a factor whose rows are ALL reconciled_gone
    must read as no-history (watch, trade_count 0); a mixed factor counts only the
    genuine rows."""
    from unittest.mock import patch
    from aletheia.analytics import factor_feedback as ff
    from tests.test_outcome_closure import _insert_entry_chain

    genuine_index = 0
    def _row(reason, pnl):
        nonlocal genuine_index
        synthetic = reason == "reconciled_gone"
        opportunity_id = proposal_id = order_id = None
        position_id = f"{reason}:{pnl}"
        if not synthetic:
            position_id = f"factor-feedback-{genuine_index}"
            genuine_index += 1
            opportunity_id, proposal_id, order_id = _insert_entry_chain(
                position_id=position_id,
                symbol="BTC/USDT",
                market_type="usdt_futures",
                direction="long",
                quantity=1.0,
                price=100.0,
                execution_mode="paper_internal",
            )
        return {"id": position_id, "position_id": position_id,
                "opportunity_id": opportunity_id, "proposal_id": proposal_id,
                "order_id": order_id,
                "scanner_family": "momentum", "direction": "long", "symbol": "BTC/USDT",
                "confidence": "high", "late_entry_penalty": 0,
                "market_type": "usdt_futures", "execution_mode": "paper_internal",
                "status": "closed", "entry_price": 100.0,
                "current_price": 100.0 + pnl, "quantity": 1.0,
                "entry_fee": 0.0, "exit_fee": 0.0,
                "funding_cost": 0.0, "funding_source": "local_simulation",
                "entry_price_source": "unavailable" if synthetic else "local_fill",
                "entry_fill_verified": not synthetic,
                "closed_at": "2026-07-12T08:00:00+00:00",
                "realized_pnl": pnl, "close_reason": reason,
                "close_category": "administrative" if synthetic else "strategy",
                "exit_price_source": "unavailable" if synthetic else "local_fill",
                "exit_fill_verified": not synthetic, "outcome_eligible": not synthetic,
                "outcome_exclusion_reason": "administrative_close" if synthetic else "eligible",
                "outcome_semantics_version": "oi-v1"}

    # All-synthetic factor → excluded → no history.
    all_synth = [_row("reconciled_gone", p) for p in (5.0, -3.0, 4.0, -2.0, 6.0)]
    with patch.object(ff, "build_trade_attribution_rows", lambda limit=500: all_synth):
        sig = ff.lookup_factor_signal({"scanner_family": "momentum", "direction": "long",
                                       "symbol": "BTC/USDT", "confidence": "high",
                                       "late_entry_penalty": 0})
        assert sig["trade_count"] == 0, (
            f"reconciled_gone rows must be excluded from the signal gate, got "
            f"trade_count={sig['trade_count']}"
        )
        assert sig["status"] == "watch", "all-synthetic factor must read as no-history/watch"

    # Mixed factor → only the 3 genuine rows count.
    mixed = ([_row("take_profit", 5.0), _row("stop_loss", -2.0), _row("take_profit", 4.0)]
             + [_row("reconciled_gone", -99.0) for _ in range(5)])
    with patch.object(ff, "build_trade_attribution_rows", lambda limit=500: mixed):
        sig = ff.lookup_factor_signal({"scanner_family": "momentum", "direction": "long",
                                       "symbol": "BTC/USDT", "confidence": "high",
                                       "late_entry_penalty": 0})
        assert sig["trade_count"] == 3, (
            f"only genuine closes must count, got trade_count={sig['trade_count']}"
        )


# ---------------------------------------------------------------------------
# LP-DUAL-01 — trading_decision.py MUST read ps_lessons from LessonStore
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-DUAL-01", "learning_pipeline", "§8.4 dual path invariant", "L2c",
        "trading_decision.py MUST read structured ps_lessons from LessonStore "
        "(not retrieve_lessons) for the System Operator prompt block, because FTS results "
        "lack evidence_json and guard timestamps",
        status="implemented",
    ),
)
def test_trading_decision_reads_lessons_from_lesson_store():
    """LP-DUAL-01: the structured prompt block comes from LessonStore, not FTS.

    Dual-path invariant: trading_context.py populates layer3["ps_lessons"]
    from LessonStore.list_all(status="applied") because only the structured
    store carries evidence_json + activated_at + expires_at. The FTS path
    (retrieve_lessons) returns knowledge_fts-sourced Lesson dataclasses that
    lack these fields — they serve a different consumer (ranker / advisory).
    trading_decision.py renders the pre-populated ps_lessons from the context.
    Neither module calls retrieve_lessons() for the structured block.
    """
    import pathlib
    root = pathlib.Path(_project_root())
    tc_src = (root / "aletheia" / "agent_shell" / "trading_context.py").read_text(encoding="utf-8")
    td_src = (root / "aletheia" / "agent_shell" / "trading_decision.py").read_text(encoding="utf-8")

    # trading_context.py must reference LessonStore (the structured CRUD store)
    assert "LessonStore" in tc_src, (
        "trading_context.py does not reference LessonStore for ps_lessons"
    )
    # trading_context.py must use list_all to read ps_lessons
    assert "list_all" in tc_src, (
        "trading_context.py does not call list_all for ps_lessons"
    )
    # trading_decision.py must render ps_lessons from the context
    assert "ps_lessons" in td_src, (
        "trading_decision.py does not render ps_lessons into the System Operator prompt"
    )
    # Neither module must call retrieve_lessons for the structured block
    assert "retrieve_lessons" not in tc_src, (
        "trading_context.py must not use retrieve_lessons for ps_lessons"
    )
    assert "retrieve_lessons" not in td_src, (
        "trading_decision.py must not use retrieve_lessons for ps_lessons"
    )

# ---------------------------------------------------------------------------
# LP-P3-01 — SkillStore producer is wired but draft-only
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-P3-01", "learning_pipeline", "§SkillStore producer", "L2a",
        "lesson_reviewer turns applied lessons into draft SkillStore entries, "
        "without auto-activating them or granting access to forbidden layers.",
        status="implemented",
    ),
)
def test_skill_store_producer_is_draft_only():
    """LP-P3-01: lesson_reviewer creates draft skills from applied lessons."""
    import pathlib

    root = pathlib.Path(_project_root())
    reviewer_src = (root / "aletheia" / "agents" / "lesson_reviewer.py").read_text(encoding="utf-8")
    skill_src = (root / "aletheia" / "learning" / "skills.py").read_text(encoding="utf-8")

    assert "SkillStore" in reviewer_src and "create_from_lesson" in reviewer_src
    assert "status=\"draft\"" in skill_src
    assert "activate(" not in reviewer_src
    for forbidden in ("risk_limits", "execution_gateway", "credentials", "live_mode", "order_submission"):
        assert forbidden not in reviewer_src


# ---------------------------------------------------------------------------
# LP-S18 — dreaming_agent must have a deterministic 24h trading-performance block
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-S18", "learning_pipeline", "§24h trading-performance block exists", "L2a",
        "_build_daily_trading_performance must exist in dreaming_agent.py and be wired "
        "into run_dreaming_agent so every daily_review begins with an authoritative P&L block. "
        "Closes the gap identified in issue #201: dreaming log omitted realised P&L entirely.",
        status="implemented",
    ),
)
def test_dreaming_agent_has_trading_performance_block():
    """LP-S18: static probe that the function and wiring exist."""
    import pathlib
    root = pathlib.Path(_project_root())
    src = (root / "aletheia" / "agents" / "dreaming_agent.py").read_text(encoding="utf-8")

    assert "_build_daily_trading_performance" in src, (
        "_build_daily_trading_performance not found in dreaming_agent.py — "
        "the 24h P&L block is missing"
    )
    assert "trading_block" in src, (
        "'trading_block' variable not found in dreaming_agent.py — "
        "_build_daily_trading_performance is defined but not wired into run_dreaming_agent"
    )
    # The block must be prepended to review_body (not just appended)
    assert "trading_block + " in src or "trading_block +" in src, (
        "trading_block is not prepended to review_body — "
        "the daily review must begin with the deterministic P&L block"
    )


# ---------------------------------------------------------------------------
# LP-S19 — LOSS_DAY label is emitted for a negative eligible close
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-S19", "learning_pipeline", "§LOSS_DAY label on negative eligible close", "L2b",
        "When outcome_eligible closed positions sum to a negative realized_pnl, "
        "_build_daily_trading_performance must return 'LOSS_DAY'. "
        "NULL realized_pnl must not be coerced to zero — any NULL → NOT_AVAILABLE.",
        status="implemented",
    ),
)
def test_dreaming_agent_loss_day_label():
    """LP-S19: negative eligible close → LOSS_DAY; non-eligible close excluded."""
    import aletheia.storage.repositories as repos_mod
    from unittest.mock import patch
    from aletheia.agents import dreaming_agent as da

    eligible_loss = {"outcome_eligible": 1, "realized_pnl": -42.5, "closed_at": "2026-01-01T01:00:00+00:00"}
    non_eligible = {"outcome_eligible": 0, "realized_pnl": 999.0, "closed_at": "2026-01-01T01:00:00+00:00"}

    with patch.object(repos_mod, "list_closed_positions", return_value=[eligible_loss, non_eligible]):
        result = da._build_daily_trading_performance("2026-01-01T00:00:00+00:00")

    assert "LOSS_DAY" in result, (
        f"Expected LOSS_DAY for net pnl=-42.5, got: {result!r}"
    )
    assert "NOT_AVAILABLE" not in result, (
        "LOSS_DAY must not include NOT_AVAILABLE language when pnl is known"
    )


# ---------------------------------------------------------------------------
# LP-S20 — NULL realized_pnl yields NOT_AVAILABLE (no silent coercion to zero)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-S20", "learning_pipeline", "§NULL realized_pnl → NOT_AVAILABLE", "L2b",
        "If any outcome_eligible close has realized_pnl=NULL the block must report "
        "NOT_AVAILABLE rather than coercing NULL to zero. Prevents the daily review "
        "from presenting a misleading P&L figure when outcome-integrity is incomplete.",
        status="implemented",
    ),
)
def test_dreaming_agent_null_pnl_is_not_available():
    """LP-S20: one NULL realized_pnl among eligible closes → NOT_AVAILABLE."""
    import aletheia.storage.repositories as repos_mod
    from unittest.mock import patch
    from aletheia.agents import dreaming_agent as da

    eligible_with_pnl = {"outcome_eligible": 1, "realized_pnl": 10.0, "closed_at": "2026-01-01T01:00:00+00:00"}
    eligible_null_pnl = {"outcome_eligible": 1, "realized_pnl": None, "closed_at": "2026-01-01T01:30:00+00:00"}

    with patch.object(repos_mod, "list_closed_positions",
                      return_value=[eligible_with_pnl, eligible_null_pnl]):
        result = da._build_daily_trading_performance("2026-01-01T00:00:00+00:00")

    assert "NOT_AVAILABLE" in result, (
        f"Expected NOT_AVAILABLE when realized_pnl is NULL on eligible close, got: {result!r}"
    )
    for label in ("PROFIT_DAY", "LOSS_DAY", "FLAT_DAY"):
        assert label not in result, (
            f"Must not assign {label} when any eligible close has NULL realized_pnl"
        )


# ---------------------------------------------------------------------------
# LP-S21 — Dreaming summary separates current knowledge from deprecated memory
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-S21",
        "learning_pipeline",
        "api_reference §Knowledge & Learning",
        "L2b",
        "dreaming summary recent collections contain active knowledge only, "
        "with archived and deprecated records returned separately",
        status="implemented",
    ),
)
def test_dreaming_summary_separates_active_and_deprecated_knowledge():
    """Archived knowledge must not also inflate the owner-facing recent count."""
    from aletheia.server.routes.data import get_dreaming_summary
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item

    collections = {
        "research_note": "recent_research_notes",
        "strategy_memory": "recent_strategy_hypotheses",
        "dreaming_log": "recent_dreaming_logs",
        "daily_review": "recent_daily_reviews",
    }
    active_ids = set()
    inactive_ids = set()
    for item_type in collections:
        active_id = f"active-{item_type}"
        active_ids.add(active_id)
        insert_knowledge_item(
            KnowledgeItem(id=active_id, item_type=item_type, title=active_id)
        )
        for status in ("archived", "deprecated"):
            item_id = f"{status}-{item_type}"
            inactive_ids.add(item_id)
            insert_knowledge_item(
                KnowledgeItem(
                    id=item_id,
                    item_type=item_type,
                    title=item_id,
                    status=status,
                )
            )

    summary = get_dreaming_summary()

    for item_type, collection_name in collections.items():
        ids = {item["id"] for item in summary[collection_name]}
        assert f"active-{item_type}" in ids
        assert ids.isdisjoint(inactive_ids)

    deprecated_ids = {item["id"] for item in summary["deprecated_memories"]}
    assert inactive_ids <= deprecated_ids
    assert deprecated_ids.isdisjoint(active_ids)


# ---------------------------------------------------------------------------
# LP-S22 — static policy diff route must win over the dynamic detail route
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "LP-S22",
        "learning_pipeline",
        "api_reference §Policy history",
        "L2b",
        "GET /api/persistence/policies/diff reaches the policy-version diff "
        "consumer while policy detail and version-history routes remain reachable.",
        status="implemented",
    ),
)
def test_policy_diff_route_is_reachable_without_shadowing(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite_store
    from aletheia.policies.store import PolicyStore
    from aletheia.server.app import app
    from aletheia.storage.migrations import run_migrations

    # TestClient serves the async route on another thread. Use the production
    # file-backed DB boundary so both threads see the same immutable versions.
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite_store.reset_db()
    sqlite_store._db_conn = None
    run_migrations()

    store = PolicyStore()
    first = store.create_version(
        "entry_policy",
        {"threshold": 60, "legacy": True},
        created_by="spec",
    )
    second = store.create_version(
        "entry_policy",
        {"threshold": 70, "new_guard": "enabled"},
        created_by="spec",
        parent_version_id=first.policy_version_id,
    )

    client = TestClient(app)
    diff_response = client.get(
        "/api/persistence/policies/diff",
        params={
            "from_id": first.policy_version_id,
            "to_id": second.policy_version_id,
        },
    )
    assert diff_response.status_code == 200, diff_response.text
    diff = diff_response.json()
    assert diff["added"] == {"new_guard": "enabled"}
    assert diff["removed"] == {"legacy": True}
    assert diff["changed"] == {"threshold": {"from": 60, "to": 70}}

    detail_response = client.get("/api/persistence/policies/entry_policy")
    assert detail_response.status_code == 200
    assert detail_response.json()["policy_version_id"] == second.policy_version_id

    versions_response = client.get(
        "/api/persistence/policies/entry_policy/versions"
    )
    assert versions_response.status_code == 200
    assert {
        row["policy_version_id"] for row in versions_response.json()
    } >= {first.policy_version_id, second.policy_version_id}
