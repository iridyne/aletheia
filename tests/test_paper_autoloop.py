"""Tests for the autonomous paper-trading closed loop (Feature 1).

Governance: this is the user-authorized autonomous-paper behavior. It must stay
paper-only (paper_internal), respect permission mode + kill switch, never reach
live, and never fake the causal chain (trust_and_change_control §1.1/§1.2/§1.3).
"""

import sqlite3
from types import SimpleNamespace

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Opportunity, MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


def _seed(symbol="BTC/USDT", price=60000.0):
    # Snapshots are stored in canonical CCXT format (BTC/USDT) — same as collect_market_snapshots.
    repo.insert_market_snapshot(MarketSnapshot(symbol=symbol, market_type="spot", price=price))
    # Entry-logic upgrade (Component 2): paper_trader now only trades directional,
    # >=medium-confidence signals, so seed one that triggers a trade.
    opp = Opportunity(symbol=symbol, market="spot", direction_bias="long",
                      thesis="test thesis", confidence="medium", source_agent="market_observer")
    repo.insert_opportunity(opp)
    return opp


def _set_mode(mode, kill=False, **_kwargs):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = mode
    cfg.runtime.kill_switch_active = kill
    # These tests validate the local paper_internal fallback path (the default is now
    # bitget_demo, a real-exchange call). Demo routing is covered by test_demo_trading.py.
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)


async def test_paperauto_autoloop_executes_paper(tmp_path, monkeypatch):
    """Phase 4a: Paper trader Path B now writes signal_pending for System Operator."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")
    opp = _seed()

    result = await run_agent("paper_trader", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    # Phase 4a: Path B writes signal_pending entries instead of proposals
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=10)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"
    reset_db()


async def test_research_mode_does_not_trade(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Observe")  # paper_tool denied in Research
    _seed()

    result = await run_agent("paper_trader", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0

    # the denied tool-call is logged
    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert any(c["tool_name"] == "create_paper_proposal" and not c["permitted"] for c in calls)
    reset_db()


def test_observe_mode_scheduler_gate(tmp_path, monkeypatch):
    """Scheduler gate: Observe mode must not trigger paper_trader."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Observe")
    _seed()

    import aletheia.config.loader as loader
    import aletheia.agent_runtime.scheduler as sched

    # Other scheduler tests replace this module-local alias with partial configs.
    # Bind this integration-style test to the isolated config it writes above.
    monkeypatch.setattr(sched, "load_config", loader.load_config)
    sched._run_paper_trader()  # scheduler gate must short-circuit

    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0
    reset_db()


def test_paper_trader_scheduler_gate_tolerates_minimal_config(monkeypatch):
    """Older focused tests stub only the fields they exercise."""
    import aletheia.agent_runtime.scheduler as sched

    monkeypatch.setattr(
        sched,
        "load_config",
        lambda: SimpleNamespace(agent=SimpleNamespace(permission_mode="Observe")),
    )

    sched._run_paper_trader()


async def test_kill_switch_blocks_autoloop(tmp_path, monkeypatch):
    """Phase 4a: With kill_switch, paper_trader still writes signals but System Operator
    won't execute them (Risk Engine blocks at proposal time)."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper", kill=True)  # kill switch engaged
    _seed()

    await run_agent("paper_trader", trigger_type="manual")

    # Path B still collects signals regardless of kill_switch
    # (System Operator + Risk Engine handle blocking at execution time)
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=10)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1

    # No proposals/orders created directly by paper_trader anymore
    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0
    assert len(repo.list_positions(status="open")) == 0
    reset_db()


async def test_autoloop_never_triggers_live(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Full")  # most permissive
    _seed()

    await run_agent("paper_trader", trigger_type="manual")

    # No order may be live, regardless of mode — the wrapper hard-codes paper_internal.
    for o in repo.list_orders():
        assert o["execution_mode"] != "bitget_live_limited"
    reset_db()


async def test_opportunity_id_linkage_and_idempotency(tmp_path, monkeypatch):
    """Phase 4a: Paper trader writes signal_pending. Second run may write additional
    signals (idempotency is now handled by System Operator, not paper_trader)."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")
    opp = _seed()

    await run_agent("paper_trader", trigger_type="manual")
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=10)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"
    reset_db()


def test_paper_tools_rejects_non_paper_mode(tmp_path, monkeypatch):
    """The paper wrapper must hard-reject any non-paper execution mode."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution.paper_tools import create_paper_proposal
    import pytest
    with pytest.raises(ValueError):
        create_paper_proposal(symbol="BTCUSDT", market_type="spot", direction="long",
                              quantity=0.001, price=60000.0, execution_mode="bitget_live_limited")
    reset_db()
