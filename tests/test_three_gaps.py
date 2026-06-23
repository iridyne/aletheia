"""Tests for the three remaining real gaps wired in this pass:

1. agent_run_steps now populated automatically via log_tool/record_step.
2. Stability monitor reports orphan/drift/uptime + score.
3. Learning Ledger surfaces stage-1 quality signals (activation/retire/coverage).
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
    asyncio.run(run_agent("market_observer", trigger_type="manual"))
    # No NEW counter should remain after run_agent completes (old residues from
    # other tests are allowed — we only own the keys introduced in this call).
    new_keys = set(helpers._step_counters) - keys_before
    assert not new_keys, f"run_agent leaked counters: {new_keys}"


# === Gap 2: stability monitor ===

def test_stability_clean_state(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agent_runtime.stability_monitor import compute_stability
    s = compute_stability()
    # Empty DB = no orphans, no drift signal, no failure ratio → score stays 100.
    assert s["score"] == 100
    assert s["reasons"] == []
    assert s["orphans"]["orphan_fills"] == 0
    assert s["strategy_id_drift"]["paper_proposals"] == 0


def test_stability_detects_orphan_fill(tmp_path, monkeypatch):
    """An orphan fill (FK to a non-existent order) must count and dock the score."""
    setup_env(tmp_path, monkeypatch)
    # Insert raw orphan fill bypassing FK (PRAGMA foreign_keys=ON would normally block;
    # we simulate the corruption that the monitor is meant to detect).
    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute(
        "INSERT INTO fills(id, order_id, fill_price, fill_quantity, fee, fee_currency) "
        "VALUES('f-orphan', 'no-such-order', 100, 1, 0, 'USDT')"
    )
    db.commit()
    db.execute("PRAGMA foreign_keys=ON")

    from aletheia.agent_runtime.stability_monitor import compute_stability
    s = compute_stability()
    assert s["orphans"]["orphan_fills"] == 1
    assert s["score"] < 100
    assert any("orphans" in r for r in s["reasons"])


def test_stability_detects_strategy_id_drift(tmp_path, monkeypatch):
    """Paper proposals without strategy_id are the exact dead-link signal."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import TradeProposal
    repo.insert_trade_proposal(TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal", strategy_id=None,
    ))
    from aletheia.agent_runtime.stability_monitor import compute_stability
    s = compute_stability()
    assert s["strategy_id_drift"]["ratio"] == 1.0
    assert s["score"] < 100


# === Gap 3: Learning Ledger quality block ===

def test_ledger_quality_block_present(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.dreaming_agent import _build_learning_ledger
    out = _build_learning_ledger()
    assert "Quality (stage-1 evidence)" in out
    assert "Activation ratio" in out
    assert "Retire/deprecate ratio" in out
    assert "Post-trade review coverage" in out
    assert "Lessons consulted (24h)" in out


def test_ledger_activation_ratio_reflects_state(tmp_path, monkeypatch):
    """A strategy promoted to paper_running should bump activation ratio above 0."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    s = create_strategy("Active", parameters={"k": "v"})
    insert_pi_decision(PiDecision(symbol=None, direction=None, action="recommend_promote", conviction=0, reasoning="test", proposal_id=s.id, debate_ran=False))
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        promote_strategy(s.id, target)

    from aletheia.agents.dreaming_agent import _build_learning_ledger
    out = _build_learning_ledger()
    # 1 of 1 strategy reached paper_running → 100%
    assert "Activation ratio: 100%" in out


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

    from aletheia.agents.dreaming_agent import _build_learning_ledger
    out = _build_learning_ledger()
    assert "Lessons consulted (24h): 1" in out   # only the lessons:3 hit, not lessons:0
    assert "Playbook reads (24h): 1" in out
    _release_run_counter("run-L")
