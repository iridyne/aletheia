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
    from aletheia.trading.strategy_lifecycle import create_strategy
    from aletheia.storage.repositories import update_strategy
    s1 = create_strategy("S1", "draft one")
    s2 = create_strategy("S2", "to hypothesis")
    update_strategy(s2.id, state="strategy_hypothesis")

    from aletheia.knowledge.manager import create_research_note
    create_research_note("note A", "evidence A")
    create_research_note("note B", "evidence B")

    captured = {}
    from aletheia.agents import dreaming_agent
    orig = dreaming_agent.create_daily_review

    def _capture(title, content):
        captured["content"] = content
        return orig(title, content)
    monkeypatch.setattr(dreaming_agent, "create_daily_review", _capture)

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Research")
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

    result = await dreaming_agent.run_dreaming_agent(permission_mode="Research")
    # Still wrote its outputs, did not raise.
    assert any(o.startswith("daily_review:") for o in result["outputs"])
