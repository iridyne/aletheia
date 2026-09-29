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


@pytest.fixture
def reviewer_provider(monkeypatch):
    """Construct no client or credential when the inference boundary is mocked."""
    from extensions.research import lesson_reviewer

    provider = object()

    def resolve(tier):
        assert tier == "deep"
        return provider

    monkeypatch.setattr(lesson_reviewer, "get_provider_for_tier", resolve)
    return provider


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




# ---------------------------------------------------------------------------
# LP-S02 — Layer3 guards query must filter by item_type=guard_candidate
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# LP-S03 — ps_lessons must be consumed by trading_decision.py prompt
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# LP-S04 (informational) — Layer3 wisdom function must exist
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# LP-S05 — Attribution root: proposals MUST carry a real opportunity_id
# ---------------------------------------------------------------------------


_GUARDS_MODULE = "extensions/knowledge/guards.py"




# ---------------------------------------------------------------------------
# LP-S06 — Guard promotion: candidates MUST be reachable by the promoter
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S06", "learning_pipeline", "§Guard promotion", "L2a",
              "load_trading_guards reaches guard_candidate files (scans both guards/ and "
              "guard_candidates/) for explicit owner review and promotion",
              status="implemented"),
)
def test_guard_candidates_reachable_by_promoter():
    """guards.py must reference the guard_candidates directory so the promoter
    can see the堆积 candidates instead of an empty, non-existent guards/ dir.
    """
    import pathlib
    src = (pathlib.Path(_project_root()) / _GUARDS_MODULE).read_text(encoding="utf-8")
    assert "guard_candidates" in src, (
        "extensions/knowledge/guards.py never references 'guard_candidates' — the "
        "promoter reads knowledge/guards/ while candidates are written to "
        "knowledge/guard_candidates/ → 0 promotions ever. "
        "See round2_week1_decision_chain_fixes.md Fix L-Bug-D"
    )


# ---------------------------------------------------------------------------
# LP-S07 — Attribution chain must hold at RUNTIME, not just in isolation
# ---------------------------------------------------------------------------




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
    """GREEN: the fix lives in extensions/analytics/trade_case_builder.py (NOT the
    frozen decision chain — analytics is solo-editable). Verifies behaviourally that
    a synthetic-closure row produces no case study while a genuine close still does,
    and that the same filter guards auto-generated guard candidates.

    Junk-data full sweep (Axis 2 — semantic toxicity), 2026-06-28:
    docs/archive/legacy/issues/audits/JUNK_DATA_FULL_SWEEP_2026-06-28.md.
    """
    from unittest.mock import MagicMock, patch

    from extensions.analytics import trade_case_builder as tcb

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
    assert len(result["created"]) == 1
    assert result["created"][0]["case_study_id"] == "cs-real"
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

    from extensions.research.lesson_reviewer import run_lesson_reviewer
    assert callable(run_lesson_reviewer)

    root = pathlib.Path(_project_root())
    sched_src = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
    assert '"lesson_reviewer"' in sched_src, (
        "scheduler.py does not register a lesson_reviewer job — the promotion step "
        "never runs. See content-probe fixes FIX 2 (LP-S09)."
    )

    reviewer_src = (root / "extensions/research/lesson_reviewer.py").read_text(encoding="utf-8")
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

    from extensions.learning.lessons import LessonStore
    assert hasattr(LessonStore, "mark_applied") and callable(LessonStore.mark_applied), (
        "LessonStore.mark_applied missing — applied_policy_version can never be set. "
        "See FIX 2 (LP-S10)."
    )

    root = pathlib.Path(_project_root())
    reviewer_src = (root / "extensions/research/lesson_reviewer.py").read_text(encoding="utf-8")
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
async def test_lesson_reviewer_promotes_and_applies(monkeypatch, reviewer_provider):
    """Behavioral proof on the autouse in-memory DB. Mocks ONLY the LLM network
    boundary and its provider construction; LessonStore / PolicyStore / evidence SQL all run for
    real, and we assert the resulting DB state — not that a mock was called.
    """
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock

    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore
    from extensions.learning.skills import SkillStore
    from extensions.policies.store import PolicyStore
    from aletheia.storage.models import new_id
    from aletheia.storage.sqlite import get_db
    _ts = datetime.now(timezone.utc).isoformat()

    db = get_db()
    # Seed >= _MIN_SETTLED_EVIDENCE settled outcomes so the deterministic floor passes.
    for _ in range(25):
        db.execute(
            "INSERT INTO filter_candidate_outcomes "
                "(id, outcome_candidate_key, venue, symbol, scanner_family, direction, horizon, "
                " market_type, entry_price_usd, entry_ts, due_at, settled_at, exit_price_usd, "
                " settlement_price_source, settlement_lag_sec, "
                " gross_return_pct, directional_return_pct, "
                " estimated_fee_pct, estimated_spread_slippage_pct, estimated_funding_pct, "
                " net_return_pct, min_tradable_edge_pct, min_cost_covering_edge_pct, cost_model_version, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id(), new_id(), "bitget", "BTC/USDT", "momentum", "long", "24h",
                 "spot", 100.0, _ts, _ts, _ts, 102.0, "horizon_ohlcv", 0, 2.0, 2.0,
                 0.1, 0.1, 0.0,
                 1.8, 0.05, 0.25, "v1", "settled"),
        )
    db.commit()

    store = LessonStore()
    lesson = store.create(
        summary="Momentum longs on BTC/USDT carry edge in this regime",
        source_type="trade_case_study", source_id="x",
        category="entry_timing", severity="warning",
    )

    monkeypatch.setattr(
        lr, "llm_structured",
        AsyncMock(return_value=({"decision": "accept", "reasoning": "supported"}, None)),
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


async def test_lesson_reviewer_surfaces_structured_judgment_failure(monkeypatch, reviewer_provider):
    from unittest.mock import AsyncMock

    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore
    from aletheia.model_provider.inference_contract import CALLER_SPECS, OutputMode

    assert CALLER_SPECS["lesson_reviewer.judge"].output_mode is OutputMode.STRUCTURED

    lesson = LessonStore().create("scoped", "trade_case_study", "case-1")
    monkeypatch.setattr(
        lr, "_fetch_evidence",
        lambda _lesson: {
            "aggregate": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
            "exact_scope": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
        },
    )
    call = AsyncMock(return_value=(None, "structured_empty_content"))
    monkeypatch.setattr(lr, "llm_structured", call)

    result = await lr.run_lesson_reviewer(run_id="test")

    assert LessonStore().get(lesson.lesson_id).status == "proposed"
    assert result["errors"] == [
        f"judgment_failed:{lesson.lesson_id}:structured_empty_content"
    ]
    assert call.await_args.kwargs["schema"] is lr._LessonJudgment
    assert call.await_args.kwargs["provider"] is reviewer_provider
    assert call.await_args.kwargs["reasoning_effort"] == "none"
    assert len(lr._LessonJudgment(
        decision="accept", reasoning="x" * 201,
    ).reasoning) == 201


async def test_lesson_reviewer_does_not_treat_model_reasoning_as_transport_failure(
    monkeypatch, reviewer_provider,
):
    from unittest.mock import AsyncMock

    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore

    lesson = LessonStore().create("scoped", "trade_case_study", "case-1")
    monkeypatch.setattr(
        lr, "_fetch_evidence",
        lambda _lesson: {
            "aggregate": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
            "exact_scope": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
        },
    )
    monkeypatch.setattr(
        lr, "llm_structured",
        AsyncMock(return_value=({
            "decision": "defer", "reasoning": "llm_error: is part of the evidence",
        }, None)),
    )

    result = await lr.run_lesson_reviewer(run_id="test")

    assert result["errors"] == []
    assert LessonStore().get(lesson.lesson_id).status == "proposed"


async def test_lesson_reviewer_cannot_revive_human_rejection(monkeypatch):
    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore
    from extensions.learning.skills import SkillStore
    from extensions.policies.store import PolicyStore

    store = LessonStore()
    lesson = store.create("stale review", "trade_case_study", "case-stale")
    monkeypatch.setattr(
        lr,
        "_fetch_evidence",
        lambda _lesson: {
            "aggregate": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
            "exact_scope": {"total": 20, "win_rate": 50.0, "avg_return": 0.1},
        },
    )

    async def reject_while_judging(*_args, **_kwargs):
        store.reject(lesson.lesson_id)
        return "accept", "stale acceptance", False

    monkeypatch.setattr(lr, "_judge", reject_while_judging)

    await lr.run_lesson_reviewer(run_id="test")

    assert store.get(lesson.lesson_id).status == "rejected"
    assert PolicyStore().list_versions("lesson_policy") == []
    assert SkillStore().get_by_source_lesson_id(lesson.lesson_id) is None


async def test_lesson_reviewer_revalidates_before_policy_handoff(monkeypatch):
    import extensions.research.lesson_reviewer as lr
    import extensions.policies.store as policy_module
    from extensions.learning.lessons import LessonStore
    from extensions.learning.skills import SkillStore
    from aletheia.storage.sqlite import get_db

    store = LessonStore()
    lesson = store.create("handoff race", "trade_case_study", "case-handoff")
    store.accept(lesson.lesson_id)
    monkeypatch.setattr(
        lr,
        "_fetch_evidence",
        lambda _lesson: {
            "aggregate": {"total": 0, "win_rate": 0.0, "avg_return": 0.0},
            "exact_scope": {"total": 0, "win_rate": 0.0, "avg_return": 0.0},
        },
    )
    real_policy_store = policy_module.PolicyStore

    class RejectBeforePolicyStore:
        def __init__(self):
            db = get_db()
            db.execute(
                "UPDATE ps_lessons SET status='rejected' WHERE lesson_id=?",
                (lesson.lesson_id,),
            )
            db.commit()
            self.delegate = real_policy_store()

        def list_versions(self, policy_type):
            return self.delegate.list_versions(policy_type)

        def create_version(self, **kwargs):
            return self.delegate.create_version(**kwargs)

    monkeypatch.setattr(policy_module, "PolicyStore", RejectBeforePolicyStore)

    await lr.run_lesson_reviewer(run_id="test")

    assert store.get(lesson.lesson_id).status == "rejected"
    assert real_policy_store().list_versions("lesson_policy") == []
    assert SkillStore().get_by_source_lesson_id(lesson.lesson_id) is None


async def test_lesson_reviewer_reuses_policy_after_partial_apply_failure():
    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore
    from extensions.policies.store import PolicyStore

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


async def test_lesson_reviewer_recovers_applied_lesson_missing_draft_skill(monkeypatch):
    import extensions.research.lesson_reviewer as lr
    from extensions.learning.lessons import LessonStore
    from extensions.learning.skills import SkillStore
    from extensions.policies.store import PolicyStore

    store = LessonStore()
    lesson = store.create("recover draft", "trade_case_study", "case-recover")
    store.accept(lesson.lesson_id)
    policies = PolicyStore()
    version = policies.create_version(
        "lesson_policy",
        {"accepted_lesson_ids": [lesson.lesson_id]},
        created_by="lesson_reviewer",
    )
    store.mark_applied(lesson.lesson_id, version.policy_version_id)
    assert SkillStore().get_by_source_lesson_id(lesson.lesson_id) is None

    monkeypatch.setattr(
        lr,
        "_fetch_evidence",
        lambda _lesson: {
            "aggregate": {"total": 0, "win_rate": 0.0, "avg_return": 0.0},
            "exact_scope": {"total": 0, "win_rate": 0.0, "avg_return": 0.0},
        },
    )

    result = await lr.run_lesson_reviewer(run_id="test")

    skill = SkillStore().get_by_source_lesson_id(lesson.lesson_id)
    assert skill is not None
    assert skill.status == "draft"
    assert skill.source_lesson_id == lesson.lesson_id
    assert store.get(lesson.lesson_id).status == "applied"
    assert len(policies.list_versions("lesson_policy")) == 1
    assert any(item.startswith("recovered_draft_skill:") for item in result["outputs"])


# ---------------------------------------------------------------------------
# LP-S12 — Ranker routes proposed memo/case deltas through its authority boundary;
#           they must NOT reach risk_engine or sizing (§8.1 invariant)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# LP-S13 — knowledge/manager.py create functions and learning pipeline MUST NOT
#           call update_config or directly write risk_engine threshold fields
#           (§8.3 invariant)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S13", "learning_pipeline", "§8.3 risk threshold immutability", "L2a",
              "No create_* function in knowledge/manager.py and no module under "
              "extensions/learning/ imports or calls update_config() / "
              "update_risk_config() — the structural guarantee that learning output "
              "cannot modify risk_engine thresholds (knowledge_contract.md §8.3)",
              status="implemented"),
)
def test_learning_pipeline_cannot_modify_risk_thresholds():
    """LP-S13: §8.3 — learning pipeline has no write path to risk thresholds.

    Checks source text only (static analysis). Verifies that:
    (a) extensions/knowledge/manager.py create_* functions never call update_config
        or update_risk_config.
    (b) every .py file under extensions/learning/ (LessonStore, PolicyStore, etc.)
        does not import from aletheia.risk.engine or call update_config directly.

    This is an L2a structural claim — not a behavioural test — because the
    boundary is architectural: if any of these paths existed the enforcement
    would require change-control review, not a runtime guard.
    """
    import pathlib

    root = pathlib.Path(_project_root())

    forbidden_calls = ("update_config", "update_risk_config")

    # (a) knowledge/manager.py must not contain any forbidden call
    mgr_src = (root / "extensions/knowledge/manager.py").read_text(encoding="utf-8")
    for fn in forbidden_calls:
        assert fn not in mgr_src, (
            f"knowledge/manager.py calls {fn}() — learning write path can "
            "reach risk threshold configuration (§8.3 violation)"
        )

    # (b) all .py files under extensions/learning/ must not import risk.engine
    #     or call the forbidden config writers
    learning_dir = root / "extensions" / "learning"
    assert learning_dir.exists(), "extensions/learning/ directory missing"
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
    sched_src = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")

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
    reviewer_src = (root / "extensions/research/post_trade_reviewer.py").read_text(encoding="utf-8")
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
    import ast
    import pathlib

    root = pathlib.Path(_project_root())
    engine_src = (root / "core/aletheia/risk/engine.py").read_text(encoding="utf-8")
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
    sched_src = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")

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
    sched_src = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
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
    sched_src = (root / "core/aletheia/agent_runtime/scheduler.py").read_text(encoding="utf-8")
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
    import ast
    import pathlib

    root = pathlib.Path(_project_root())

    # 精准关键字：必须是 trading_memo / regime_playbook 的 half-life 权重衰减函数名
    # 不匹配 trade_path.py 的 time_exit_decay（trade 分类标签）
    # 不匹配 exit_policy_advisor 的 time_decay_frac（统计变量）
    # 不匹配 retrieval.py 的 _recency_weight（检索层通用衰减，非 memo half-life）
    decay_fn_patterns = ("memo_decay", "memo_half_life", "regime_decay", "playbook_decay",
                         "half_life_weight", "memo_time_weight")

    # 通过 AST 扫描函数名（FunctionDef/AsyncFunctionDef），不扫字符串字面量
    found_fns: list[str] = []
    for search_dir in ("core/aletheia/analytics", "extensions/analytics", "extensions/learning", "extensions/knowledge"):
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
        "未在 aletheia/analytics/、extensions/learning/ 或 extensions/knowledge/ 中找到 "
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
    import inspect
    import pathlib

    from extensions.research.post_trade_reviewer import _fills_window_key

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
    reviewer_src = (root / "extensions/research/post_trade_reviewer.py").read_text(encoding="utf-8")
    assert "window:" in reviewer_src, (
        "window: tag prefix missing from post_trade_reviewer.py — "
        "idempotency tag not written"
    )

    # create_post_trade_review must accept tags
    from extensions.knowledge.manager import create_post_trade_review
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
    import ast
    import pathlib

    root = pathlib.Path(_project_root())
    dreaming_src = (root / "extensions/research/dreaming_agent.py").read_text(encoding="utf-8")

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
    import inspect

    from extensions.learning.lessons import LessonStore
    sig = inspect.signature(LessonStore.create)
    assert "evidence" in sig.parameters, (
        "LessonStore.create() must accept an `evidence` parameter"
    )


# ---------------------------------------------------------------------------
# LP-S15 — factor feedback excludes administrative closes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("LP-S15", "learning_pipeline", "§factor feedback excludes administrative closes", "L2b",
              "build_factor_feedback excludes reconciled_gone administrative records "
              "from trade count, win rate and expectancy; only verified external "
              "closes contribute to current statistics.", status="implemented"),
)
def test_factor_feedback_excludes_synthetic_closes():
    """Administrative cleanup contributes no trade history or economic weight."""
    from datetime import datetime, timezone
    from unittest.mock import patch

    from aletheia.analytics import factor_feedback as ff
    from aletheia.analytics.outcome_semantics import assess_realized_position
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    synthetic = [{
        "id": "administrative-close", "status": "closed",
        "execution_mode": "bitget_demo", "close_reason": "reconciled_gone",
        "close_category": "administrative", "realized_pnl": -99.0,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "scanner_family": "momentum", "direction": "long", "symbol": "BTC/USDT",
        "confidence": "high", "late_entry_penalty": 0,
    }]
    assert assess_realized_position(synthetic[0]).reason == "administrative_close"
    with patch.object(ff, "build_trade_attribution_rows", return_value=synthetic):
        assert ff.build_factor_feedback() == []

    genuine = []
    for i, pnl in enumerate((5.0, -2.0, 4.0)):
        row = _insert_closed_position_via_producer(
            position_id=f"factor-feedback-{i}", symbol="BTC/USDT",
            market_type="usdt_futures", direction="long", quantity=1.0,
            entry_price=100.0, realized_pnl=pnl, execution_mode="bitget_demo",
            closed_at=datetime.now(timezone.utc).isoformat(),
            close_reason="take_profit" if pnl > 0 else "stop_loss",
            scanner_family="momentum", confidence="high",
        )
        row.update(scanner_family="momentum", confidence="high", late_entry_penalty=0)
        genuine.append(row)

    with patch.object(ff, "build_trade_attribution_rows", return_value=genuine + synthetic):
        groups = ff.build_factor_feedback()
    exact, = [group for group in groups if group["group_type"] == "exact"]
    assert exact["trade_count"] == 3
    assert exact["win_rate"] == pytest.approx(2 / 3, abs=0.0001)
    assert exact["total_pnl"] == 7.0


# ---------------------------------------------------------------------------
# LP-DUAL-01 — trading_decision.py MUST read ps_lessons from LessonStore
# ---------------------------------------------------------------------------



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
    reviewer_src = (root / "extensions" / "research" / "lesson_reviewer.py").read_text(encoding="utf-8")
    skill_src = (root / "extensions" / "learning" / "skills.py").read_text(encoding="utf-8")

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
    src = (root / "extensions" / "research" / "dreaming_agent.py").read_text(encoding="utf-8")

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
    assert "eligible_strategy_outcomes" in src
    assert "build_outcome_cohort_summary" in src
    assert "list_closed_positions" not in src


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
def test_dreaming_agent_loss_day_label(monkeypatch):
    """LP-S19: central eligible close → LOSS_DAY; raw exclusions are ignored."""
    from aletheia.analytics import outcome_integrity
    from extensions.research import dreaming_agent as da

    eligible_loss = {"outcome_eligible": 1, "realized_pnl": -42.5, "closed_at": "2026-01-01T01:00:00+00:00"}
    monkeypatch.setattr(
        outcome_integrity, "eligible_strategy_outcomes",
        lambda **_: [eligible_loss],
    )
    monkeypatch.setattr(
        outcome_integrity, "build_outcome_cohort_summary",
        lambda **_: {
            "raw_event_count": 2,
            "eligible_outcome_count": 1,
            "excluded_outcome_count": 1,
            "outcome_exclusion_reasons": {"incomplete_realized_economics": 1},
            "cohort_state": "available",
        },
    )

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
def test_dreaming_agent_null_pnl_is_not_available(monkeypatch):
    """LP-S20: an incomplete raw close is excluded by the central authority."""
    from aletheia.analytics import outcome_integrity
    from extensions.research import dreaming_agent as da

    monkeypatch.setattr(
        outcome_integrity, "eligible_strategy_outcomes", lambda **_: [],
    )
    monkeypatch.setattr(
        outcome_integrity, "build_outcome_cohort_summary",
        lambda **_: {
            "raw_event_count": 2,
            "eligible_outcome_count": 0,
            "excluded_outcome_count": 2,
            "outcome_exclusion_reasons": {
                "incomplete_realized_economics": 2,
            },
            "cohort_state": "insufficient",
        },
    )

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
    from extensions.policies.store import PolicyStore
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
