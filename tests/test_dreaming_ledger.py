"""Tests for the dreaming agent's Learning Ledger (survival-hardening #7).

The nightly dreaming run already produces a daily_review. We append a
"Learning Ledger" — a real snapshot of strategy-lifecycle state distribution and
knowledge-item counts — so the operator can tell whether self-evolution is
actually producing validated strategies, not just piling up notes. The ledger
must never break the dreaming run if its queries fail.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

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


def test_trading_performance_summary_uses_verified_recent_pnl(monkeypatch):
    from aletheia.agents.dreaming_agent import _build_trading_performance_summary
    from aletheia.analytics import outcome_integrity

    now = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(outcome_integrity, "eligible_strategy_outcomes", lambda: [
        {
            "closed_at": (now - timedelta(hours=2)).isoformat(),
            "realized_pnl": -12.5,
        },
        {
            "closed_at": (now - timedelta(hours=30)).isoformat(),
            "realized_pnl": 100.0,
        },
    ])

    summary = _build_trading_performance_summary(now=now)

    assert "Assessment: LOSS_DAY" in summary
    assert "Eligible closed trades: 1" in summary
    assert "Net realized PnL: -12.50 USDT" in summary


async def test_daily_review_leads_with_loss_fact_not_agent_success(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents import dreaming_agent
    from aletheia.analytics import outcome_integrity

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(outcome_integrity, "eligible_strategy_outcomes", lambda: [{
        "closed_at": (now - timedelta(hours=1)).isoformat(),
        "realized_pnl": -8.25,
    }])
    prompt = {}

    async def cheerful_llm(*, system, user, **kwargs):
        prompt["system"] = system
        prompt["user"] = user
        return "All agents completed successfully; operations were stable.", None

    monkeypatch.setattr(dreaming_agent, "llm_complete", cheerful_llm)
    captured = {}
    original = dreaming_agent.create_daily_review

    def capture(title, content):
        captured["content"] = content
        return original(title, content)

    monkeypatch.setattr(dreaming_agent, "create_daily_review", capture)

    result = await dreaming_agent.run_dreaming_agent(
        permission_mode="Observe", trigger_type="cron"
    )

    assert any(item.startswith("daily_review:") for item in result["outputs"])
    assert "Assessment: LOSS_DAY" in prompt["user"]
    assert "LOSS_DAY" in prompt["system"]
    assert captured["content"].startswith("## Verified Trading Performance")
    assert "Net realized PnL: -8.25 USDT" in captured["content"]
    assert "Operational Narrative (LLM-generated, non-authoritative)" in captured["content"]


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
