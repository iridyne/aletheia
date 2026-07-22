"""Tests for the dreaming agent's Learning Ledger (survival-hardening #7).

The nightly dreaming run already produces a daily_review. We append a
"Learning Ledger" — a real snapshot of strategy-lifecycle state distribution and
knowledge-item counts — so the operator can tell whether self-evolution is
actually producing validated strategies, not just piling up notes. The ledger
must never break the dreaming run if its queries fail.
"""

import sqlite3

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
