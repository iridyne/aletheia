"""Tests for the dreaming agent's Learning Ledger (survival-hardening #7).

The nightly dreaming run already produces a daily_review. We append a
"Learning Ledger" — a real snapshot of strategy-lifecycle state distribution and
knowledge-item counts — so the operator can tell whether self-evolution is
actually producing validated strategies, not just piling up notes. The ledger
must never break the dreaming run if its queries fail.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    from aletheia.storage.migrations import run_migrations
    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    # Register tool permissions so write_dreaming_log is permitted (Research mode),
    # mirroring what the orchestrator does before invoking any agent.
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()


def _mock_llm_unavailable(monkeypatch):
    """Force the LLM to be unavailable so the dreaming agent takes its
    deterministic fallback path (no network) but still writes the review."""
    from aletheia.agents import dreaming_agent

    async def _fail(*a, **k):
        return None, "llm_unavailable: test"
    monkeypatch.setattr(dreaming_agent, "llm_complete", _fail)


def _seed_trade_case_study(
    case_id: str = "case-1", created_at: str | None = None,
) -> None:
    from aletheia.storage.sqlite import get_db

    now = created_at or datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "INSERT INTO knowledge_items "
        "(id,item_type,title,status,confidence,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            case_id,
            "trade_case_study",
            f"Case: {case_id}",
            "active",
            "high_confidence",
            now,
            now,
        ),
    )
    get_db().commit()


def test_lesson_extraction_declares_its_structured_transport_contract():
    from aletheia.agents.dreaming_agent import _LessonExtractionResult
    from aletheia.model_provider.inference_contract import (
        OutputMode,
        build_inference_call,
    )

    call = build_inference_call(
        caller="dreaming.lesson_extraction",
        system="extract lessons",
        messages=[{"role": "user", "content": "one trade case"}],
        context_adapter_id="none-v1",
        schema=_LessonExtractionResult,
    )

    assert call.caller.output_mode is OutputMode.STRUCTURED


async def test_lesson_extraction_reports_invalid_structured_output(
    tmp_path, monkeypatch,
):
    """A transport-successful but unusable model response must not look healthy."""
    setup_env(tmp_path, monkeypatch)

    from aletheia.agents import dreaming_agent
    from aletheia.learning.control import get_learning_job_health
    from aletheia.storage.repositories import pending_trade_case_studies_summary
    _seed_trade_case_study()

    async def _legacy_invalid(*_args, **_kwargs):
        return "not-json", None

    async def _structured_invalid(*_args, **_kwargs):
        return None, "structured_parse_error: no valid JSON object found"

    monkeypatch.setattr(dreaming_agent, "llm_complete", _legacy_invalid)
    monkeypatch.setattr(
        dreaming_agent,
        "llm_structured",
        _structured_invalid,
        raising=False,
    )

    outputs: list[str] = []
    errors: list[str] = []
    await dreaming_agent._extract_lessons(None, outputs, errors)

    assert outputs == []
    assert errors == [
        "lesson_extraction_invalid_output:structured_parse_error: "
        "no valid JSON object found"
    ]

    assert get_learning_job_health("dreaming_daily").input_cursor is None
    assert pending_trade_case_studies_summary()[0] == 1


async def test_lesson_extraction_persists_schema_valid_lesson(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _seed_trade_case_study()

    from aletheia.agents import dreaming_agent
    from aletheia.storage.sqlite import get_db

    call_options = {}

    async def _structured_success(*_args, **kwargs):
        call_options.update(kwargs)
        return {
            "lessons": [
                {
                    "summary": "BTC long stop-loss cases require tighter entry timing.",
                    "category": "entry_timing",
                    "severity": "warning",
                    "supporting_factor_keys": ["invented:key:long"],
                    "evidence": {"outcome_counts": {"loss": 1}},
                    "recommendation": None,
                }
            ]
        }, None

    monkeypatch.setattr(dreaming_agent, "llm_structured", _structured_success)
    outputs: list[str] = []
    errors: list[str] = []

    await dreaming_agent._extract_lessons(None, outputs, errors)

    assert errors == []
    assert call_options["reasoning_effort"] == "none"
    assert len(outputs) == 1
    assert outputs[0].startswith("lesson:")
    row = get_db().execute(
        "SELECT source_type,source_id,status,summary,evidence_json "
        "FROM ps_lessons"
    ).fetchone()
    assert row is not None
    assert row["source_type"] == "trade_case_study"
    assert row["source_id"] == "case-1"
    assert row["status"] == "proposed"
    assert "BTC long stop-loss" in row["summary"]
    assert "outcome_counts" in row["evidence_json"]
    assert json.loads(row["evidence_json"])["supporting_factor_keys"] == []


async def test_lesson_extraction_drains_cases_with_durable_cursor(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents import dreaming_agent
    from aletheia.learning.control import (
        get_learning_job_health,
        job_health_view,
    )
    from aletheia.storage.repositories import pending_trade_case_studies_summary

    _seed_trade_case_study("case-1", "2026-08-15T01:00:00+00:00")

    async def _structured_success(*_args, **_kwargs):
        return {"lessons": [{"summary": "A durable lesson"}]}, None

    monkeypatch.setattr(dreaming_agent, "llm_structured", _structured_success)
    await dreaming_agent._extract_lessons(None, [], [])
    _seed_trade_case_study("case-2", "2026-08-15T02:00:00+00:00")
    await dreaming_agent._extract_lessons(None, [], [])

    # A delayed case older than the cursor is recovered by the durable wrap.
    _seed_trade_case_study("case-delayed", "2026-08-15T00:30:00+00:00")
    await dreaming_agent._extract_lessons(None, [], [])

    health = get_learning_job_health("dreaming_daily")
    assert health.input_cursor == "2026-08-15T02:00:00+00:00|case-2"
    view = job_health_view(health)
    assert view["input_cursor"] == health.input_cursor
    assert view["backlog_count"] == 0
    assert pending_trade_case_studies_summary() == (0, None)




async def test_reopened_prefix_does_not_starve_forward_cases(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from pathlib import Path

    from aletheia.agents import dreaming_agent
    from aletheia.learning.control import get_learning_job_health
    from aletheia.learning.lessons import LessonStore
    from aletheia.storage.sqlite import get_db

    db = get_db()
    factor_by_case = {
        "case-1": "momentum:BTC/USDT:long",
        "case-reopened": "momentum:ETH/USDT:long",
        "case-newer": "volatility:SOL/USDT:short",
    }

    def seed_case(case_id: str, created_at: str) -> None:
        factor = factor_by_case[case_id]
        path = Path(tmp_path) / f"{case_id}.md"
        path.write_text(
            "---\n"
            "outcome: loss\n"
            "realized_pnl_pct: -1.0\n"
            f"factor_key: {factor}\n"
            "scanner_family: momentum\n"
            "---\n",
            encoding="utf-8",
        )
        db.execute(
            "INSERT INTO knowledge_items "
            "(id,item_type,title,status,confidence,file_path,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                case_id, "trade_case_study", case_id, "active", "high_confidence",
                str(path), created_at, created_at,
            ),
        )
        db.commit()

    seed_case("case-1", "2026-08-15T01:00:00+00:00")
    calls = 0

    async def _structured_success(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"lessons": [{
                "summary": "Initial lesson",
                "supporting_factor_keys": [factor_by_case["case-1"]],
            }]}, None
        return {"lessons": [
            {
                "summary": "Reopened lesson",
                "supporting_factor_keys": [factor_by_case["case-reopened"]],
            },
            {
                "summary": "Forward lesson",
                "supporting_factor_keys": [factor_by_case["case-newer"]],
            },
        ]}, None

    monkeypatch.setattr(dreaming_agent, "llm_structured", _structured_success)
    await dreaming_agent._extract_lessons(None, [], [])

    seed_case("case-reopened", "2026-08-15T00:30:00+00:00")
    rejected = LessonStore().create(
        "Reopened lesson", source_type="trade_case_study", source_id="case-reopened",
    )
    LessonStore().reject(rejected.lesson_id)
    seed_case("case-newer", "2026-08-15T02:00:00+00:00")

    await dreaming_agent._extract_lessons(None, [], [])

    assert calls == 2
    assert get_learning_job_health("dreaming_daily").input_cursor == (
        "2026-08-15T02:00:00+00:00|case-newer"
    )
    source_ids = {
        row["source_id"]
        for row in db.execute(
            "SELECT source_id FROM ps_lessons WHERE status='proposed'"
        ).fetchall()
    }
    assert {"case-reopened", "case-newer"} <= source_ids


async def test_nomination_failure_keeps_case_pending(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from pathlib import Path

    from aletheia.agents import dreaming_agent
    from aletheia.learning import guard_routing, hypotheses
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import pending_trade_case_studies_summary
    from tests.specs.spec_learning_attribution import _ready_research

    factor = "momentum:BTC/USDT:long:medium_confidence:not_late"
    path = Path(tmp_path) / "case-retry.md"
    path.write_text(
        "---\n"
        "outcome: loss\n"
        "realized_pnl_pct: -2.0\n"
        f"factor_key: {factor}\n"
        "scanner_family: momentum\n"
        "---\n",
        encoding="utf-8",
    )
    db = get_db()
    db.execute(
        "INSERT INTO knowledge_items "
        "(id,item_type,title,status,confidence,file_path,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "case-retry", "trade_case_study", "case-retry", "active",
            "high_confidence", str(path), "2026-08-15T01:00:00+00:00",
            "2026-08-15T01:00:00+00:00",
        ),
    )
    db.commit()

    async def _structured_recommendation(*_args, **_kwargs):
        return {"lessons": [{
            "summary": "Retryable guard hypothesis",
            "supporting_factor_keys": [factor],
            "recommendation": {"action": "block_scanner", "scanner": "momentum"},
        }]}, None

    def fail_nomination(**_):
        raise RuntimeError("temporary nomination failure")

    async def ready_research(**kwargs):
        return _ready_research(*kwargs["source_artifact_ids"])

    monkeypatch.setattr(dreaming_agent, "llm_structured", _structured_recommendation)
    monkeypatch.setattr(guard_routing, "research_learning_hypothesis", ready_research)
    monkeypatch.setattr(hypotheses, "nominate_learning_hypothesis", fail_nomination)

    with pytest.raises(RuntimeError, match="temporary nomination failure"):
        await dreaming_agent._extract_lessons(None, [], [])

    assert db.execute("SELECT COUNT(*) FROM ps_lessons").fetchone()[0] == 0
    assert pending_trade_case_studies_summary()[0] == 1


def test_pending_trade_case_reopens_after_rejected_lesson(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.learning.lessons import LessonStore
    from aletheia.storage.repositories import (
        list_pending_trade_case_studies,
        pending_trade_case_studies_summary,
    )

    _seed_trade_case_study("case-rejected", "2026-08-15T01:00:00+00:00")
    lesson = LessonStore().create(
        "Rejected lesson",
        source_type="trade_case_study",
        source_id="case-rejected",
    )
    LessonStore().reject(lesson.lesson_id)

    assert [row["id"] for row in list_pending_trade_case_studies()] == ["case-rejected"]
    assert pending_trade_case_studies_summary()[0] == 1


async def test_lesson_extraction_links_each_lesson_to_its_supporting_case(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    from pathlib import Path

    from aletheia.agents import dreaming_agent
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    for case_id, factor in (("case-1", "momentum:BTC/USDT:long"),
                            ("case-2", "volatility:ETH/USDT:short")):
        path = Path(tmp_path) / f"{case_id}.md"
        path.write_text(f"---\nfactor_key: {factor}\n---\n", encoding="utf-8")
        db.execute(
            "INSERT INTO knowledge_items "
            "(id,item_type,title,status,confidence,file_path,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (case_id, "trade_case_study", case_id, "active", "high_confidence",
             str(path), now, now),
        )
    db.commit()

    async def _structured_success(*_args, **_kwargs):
        return {"lessons": [
            {"summary": "Momentum lesson", "supporting_factor_keys": ["momentum:BTC/USDT:long"]},
            {"summary": "Volatility lesson", "supporting_factor_keys": ["volatility:ETH/USDT:short"]},
        ]}, None

    monkeypatch.setattr(dreaming_agent, "llm_structured", _structured_success)
    outputs: list[str] = []
    errors: list[str] = []
    await dreaming_agent._extract_lessons(None, outputs, errors)

    rows = db.execute(
        "SELECT summary,source_id,evidence_json FROM ps_lessons ORDER BY summary"
    ).fetchall()
    assert errors == []
    assert [(row["summary"], row["source_id"]) for row in rows] == [
        ("Momentum lesson", "case-1"),
        ("Volatility lesson", "case-2"),
    ]
    assert [json.loads(row["evidence_json"])["supporting_source_ids"] for row in rows] == [
        ["case-1"], ["case-2"],
    ]


async def test_dreaming_review_includes_learning_ledger(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)

    # Seed strategies across lifecycle states + a couple of knowledge items.
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy
    from aletheia.storage.repositories import update_strategy
    s1 = StrategyRecord(name="S1", description="draft one", state="draft")
    insert_strategy(s1)
    s2 = StrategyRecord(name="S2", description="to hypothesis", state="draft")
    insert_strategy(s2)
    update_strategy(s2.id, state="strategy_hypothesis")

    from aletheia.knowledge.manager import create_research_note
    # Content must be ≥80 chars to pass _is_quality_pass's _MIN_CONTENT_LENGTH gate.
    create_research_note("note A", "Evidence A: momentum divergence observed in BTC 4h chart. RSI overbought while price flat — bearish signal.")
    create_research_note("note B", "Evidence B: ETH funding rates turned negative on perpetuals, suggesting leveraged longs being squeezed out gradually.")

    captured = {}
    from aletheia.agents import dreaming_agent
    orig = dreaming_agent.create_daily_review

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_daily_review", _capture)

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    assert any(o.startswith("daily_review:") for o in result["outputs"])

    content = captured["content"]
    assert "Learning Ledger" in content
    # state distribution reflects the seeded strategies
    assert "draft" in content
    assert "strategy_hypothesis" in content
    # knowledge counts present
    assert "research_note" in content


async def test_learning_ledger_failure_does_not_break_run(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)

    # Make the ledger builder blow up; the dreaming run must still succeed.
    from aletheia.agents import dreaming_agent
    monkeypatch.setattr(dreaming_agent, "_build_learning_ledger",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    # The run must complete without raising even when the ledger builder blows up
    # AND the LLM is unavailable. With both failing, the review body is empty and
    # is correctly rejected by create_daily_review's quality gate (KL-1) — so the
    # contract is "run survives + records the skip", not "must write a review".
    assert isinstance(result.get("outputs"), list)
    assert "boom" in " ".join(result.get("errors", [])) or any(
        "skipped:quality_gate" in str(o) or o.startswith("daily_review:")
        for o in result["outputs"]
    )


async def test_dreaming_triggers_git_snapshot_when_enabled(tmp_path, monkeypatch):
    """§3: dreaming run snapshots the versioned data dirs via commit_all (Trading-as-Git
    was wired but never called by any scheduler/agent)."""
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)

    calls = []
    import aletheia.files.git as git_mod
    def fake_commit_all(message):
        calls.append(message)
        return {"committed": True, "message": message}
    monkeypatch.setattr(git_mod, "commit_all", fake_commit_all)

    from aletheia.agents import dreaming_agent
    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")

    assert len(calls) == 1
    assert "git_snapshot:committed" in result["outputs"]


async def test_dreaming_git_snapshot_failure_does_not_break_run(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)

    import aletheia.files.git as git_mod
    def boom(message):
        raise RuntimeError("git exploded")
    monkeypatch.setattr(git_mod, "commit_all", boom)

    from aletheia.agents import dreaming_agent
    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    # Run still completes and wrote its review despite git failure.
    assert any(
        o.startswith("dreaming_log:") or o.startswith("daily_review:")
        for o in result["outputs"]
    )


# ---------------------------------------------------------------------------
# Trading performance block (#201)
# ---------------------------------------------------------------------------

def _seed_closed_position(realized_pnl: float, outcome_eligible: bool = True) -> None:
    from datetime import datetime, timezone, timedelta
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position, update_position

    p = Position(
        symbol="BTC/USDT",
        market_type="futures",
        direction="long",
        quantity=0.01,
        entry_price=60000.0,
        execution_mode="paper_internal",
        status="closed",
        realized_pnl=realized_pnl,
        outcome_eligible=outcome_eligible,
        outcome_exclusion_reason="none" if outcome_eligible else "unclassified",
        closed_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
    )
    insert_position(p)
    update_position(p.id, status="closed")


async def test_dreaming_review_loss_day_verdict(tmp_path, monkeypatch):
    """A single outcome-eligible closed position with negative pnl → LOSS_DAY in review."""
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)
    _seed_closed_position(realized_pnl=-25.0)

    captured = {}
    from aletheia.agents import dreaming_agent
    orig = dreaming_agent.create_daily_review

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_daily_review", _capture)

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    assert any(o.startswith("daily_review:") for o in result["outputs"]), result
    assert "LOSS_DAY" in captured["content"], captured["content"]


async def test_dreaming_review_not_available_when_no_trades(tmp_path, monkeypatch):
    """No eligible closed trades → NOT_AVAILABLE in review (never coerced to FLAT)."""
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)
    # No positions seeded — table is empty

    captured = {}
    from aletheia.agents import dreaming_agent
    orig = dreaming_agent.create_daily_review

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_daily_review", _capture)

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    assert any(o.startswith("daily_review:") for o in result["outputs"]), result
    assert "NOT_AVAILABLE" in captured["content"], captured["content"]


async def test_dreaming_review_profit_day_verdict(tmp_path, monkeypatch):
    """Net positive realized_pnl → PROFIT_DAY in review."""
    setup_env(tmp_path, monkeypatch)
    _mock_llm_unavailable(monkeypatch)
    _seed_closed_position(realized_pnl=10.0)
    _seed_closed_position(realized_pnl=5.0)
    _seed_closed_position(realized_pnl=-2.0)  # net still positive

    captured = {}
    from aletheia.agents import dreaming_agent
    orig = dreaming_agent.create_daily_review

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_daily_review", _capture)

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Observe")
    assert any(o.startswith("daily_review:") for o in result["outputs"]), result
    assert "PROFIT_DAY" in captured["content"], captured["content"]


async def test_dreaming_performance_block_precedes_llm_narrative(tmp_path, monkeypatch):
    """Trading performance block must appear before the LLM narrative in the document."""
    setup_env(tmp_path, monkeypatch)

    from aletheia.agents import dreaming_agent

    async def _fake_llm(*a, **kw):
        return "## Narrative\nToday was uneventful.", None
    monkeypatch.setattr(dreaming_agent, "llm_complete", _fake_llm)
    _seed_closed_position(realized_pnl=-5.0)

    captured = {}
    orig = dreaming_agent.create_dreaming_log

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_dreaming_log", _capture)

    await dreaming_agent.run_dreaming_agent(permission_mode="Observe")

    content = captured.get("content", "")
    assert "Trading Performance" in content, "performance block missing from dreaming_log"
    assert "## Narrative" in content, "LLM narrative missing from dreaming_log"
    perf_pos = content.index("Trading Performance")
    narrative_pos = content.index("## Narrative")
    assert perf_pos < narrative_pos, (
        f"Trading Performance block (at {perf_pos}) must precede LLM narrative (at {narrative_pos})"
    )
