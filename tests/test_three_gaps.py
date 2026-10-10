"""Tests for the remaining real gaps wired in this pass:

1. agent_run_steps now populated automatically via log_tool/record_step.
2. Learning Ledger surfaces stage-1 quality signals (activation/retire/coverage).
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/strategies",
                "knowledge/post_trade_reviews", "knowledge/dreaming_logs",
                "knowledge/daily_reviews"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()


# === Gap 1: agent_run_steps actually populated ===

def test_log_tool_writes_a_step(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import log_tool, _release_run_counter
    log_tool("run-A", "get_market_snapshot", "read_tool", True, output="BTCUSDT")
    log_tool("run-A", "create_opportunity", "research_tool", True, output="opp-1")
    steps = repo.list_run_steps("run-A")
    assert len(steps) == 2
    assert steps[0]["step_number"] == 1
    assert steps[0]["action"] == "tool:get_market_snapshot"
    assert steps[1]["step_number"] == 2
    _release_run_counter("run-A")


def test_record_step_for_non_tool_reasoning(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import log_tool, record_step, _release_run_counter
    log_tool("run-B", "get_x", "read_tool", True)
    record_step("run-B", "considered 3 lessons", input_data="q=stop loss")
    log_tool("run-B", "create_y", "research_tool", True)
    steps = repo.list_run_steps("run-B")
    assert [s["step_number"] for s in steps] == [1, 2, 3]
    assert steps[1]["action"] == "considered 3 lessons"
    _release_run_counter("run-B")


def test_step_counters_isolated_per_run(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import log_tool, _release_run_counter
    log_tool("run-X", "a", "read_tool", True)
    log_tool("run-Y", "b", "read_tool", True)
    log_tool("run-X", "c", "read_tool", True)
    x = repo.list_run_steps("run-X")
    y = repo.list_run_steps("run-Y")
    assert [s["step_number"] for s in x] == [1, 2]
    assert [s["step_number"] for s in y] == [1]
    _release_run_counter("run-X"); _release_run_counter("run-Y")


def test_orchestrator_releases_counter(tmp_path, monkeypatch):
    """run_agent should drop the per-run counter so the dict stays bounded."""
    import asyncio
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.orchestrator import run_agent
    from aletheia.agents import helpers
    keys_before = set(helpers._step_counters)
    result = asyncio.run(run_agent("portfolio_agent", trigger_type="manual"))
    assert not any("Unknown agent" in error for error in result.get("errors", []))
    # No NEW counter should remain after run_agent completes (old residues from
    # other tests are allowed — we only own the keys introduced in this call).
    new_keys = set(helpers._step_counters) - keys_before
    assert not new_keys, f"run_agent leaked counters: {new_keys}"


# === Gap 3: Learning Ledger quality block ===

def test_ledger_quality_block_present(tmp_path, monkeypatch):
    # strategy_records retired in #3702 — ledger no longer includes strategy state metrics
    setup_env(tmp_path, monkeypatch)
    from extensions.research.dreaming_agent import _build_learning_ledger
    out = _build_learning_ledger()
    assert "Learning Ledger" in out
    assert "Knowledge items" in out
    assert "Lessons consulted (24h)" in out
    assert "Playbook reads (24h)" in out



def test_ledger_lessons_consulted_counts_only_hits(tmp_path, monkeypatch):
    """Lessons-consulted counts only retrieve_lessons calls that returned >=1
    lesson (parsed from the logged lessons:N output). A retrieve_lessons call that
    returned lessons:0 (empty FTS gate) must NOT count — counting it would let the
    metric read non-zero while the loop transferred nothing, the ROADMAP "false
    confidence" mode (BACKEND_ISSUES §2.1). load_playbooks is reported as a
    SEPARATE "Playbook reads" count, never folded into the lesson signal (it logs
    no count to hit-filter on)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import log_tool, _release_run_counter
    log_tool("run-L", "retrieve_lessons", "read_tool", True, output="lessons:3")  # hit
    log_tool("run-L", "retrieve_lessons", "read_tool", True, output="lessons:0")  # empty → NOT counted
    log_tool("run-L", "load_playbooks", "read_tool", True)                         # separate metric
    log_tool("run-L", "get_market_snapshot", "read_tool", True)                    # not counted

    from extensions.research.dreaming_agent import _build_learning_ledger
    out = _build_learning_ledger()
    assert "Lessons consulted (24h): 1" in out   # only the lessons:3 hit, not lessons:0
    assert "Playbook reads (24h): 1" in out
    _release_run_counter("run-L")
