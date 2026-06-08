"""Tests for the autonomous paper-trading closed loop (Feature 1).

Governance: this is the user-authorized autonomous-paper behavior. It must stay
default-OFF (agent.autonomous_paper_trading), paper-only (paper_internal), respect
permission mode + kill switch, never reach live, and never fake the causal chain
(trust_and_change_control §1.1/§1.2/§1.3).
"""

import sqlite3

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


def _set_mode(mode, autonomous=True, kill=False):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = mode
    cfg.agent.autonomous_paper_trading = autonomous
    cfg.runtime.kill_switch_active = kill
    save_config(cfg)


async def test_paperauto_autoloop_executes_paper(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("PaperAuto")
    opp = _seed()

    result = await run_agent("paper_trader", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    proposals = repo.list_trade_proposals()
    assert len(proposals) == 1
    p = proposals[0]
    assert p["status"] == "executed"
    assert p["execution_mode"] == "paper_internal"

    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["proposal_id"] == p["id"]
    assert orders[0]["risk_decision_id"]  # real risk decision linked

    fills = repo.list_fills()
    assert len(fills) == 1
    assert fills[0]["order_id"] == orders[0]["id"]

    positions = repo.list_positions(status="open")
    assert len(positions) == 1
    reset_db()


async def test_research_mode_does_not_trade(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Research")  # paper_tool denied in Research
    _seed()

    result = await run_agent("paper_trader", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0

    # the denied tool-call is logged
    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert any(c["tool_name"] == "create_paper_proposal" and not c["permitted"] for c in calls)
    reset_db()


def test_autonomous_flag_off_no_trade(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("PaperAuto", autonomous=False)  # flag OFF
    _seed()

    import aletheia.agent_runtime.scheduler as sched
    sched._run_paper_trader()  # scheduler gate must short-circuit

    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0
    reset_db()


async def test_autonomous_flag_off_blocks_direct_trigger(tmp_path, monkeypatch):
    """§2.5: a direct CLI/API trigger bypasses the scheduler gate, so the agent body
    must also refuse to trade when autonomous_paper_trading is OFF."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("PaperAuto", autonomous=False)  # permission OK, but flag OFF
    _seed()

    result = await run_agent("paper_trader", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    assert len(repo.list_trade_proposals()) == 0
    assert len(repo.list_orders()) == 0

    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert any(c["tool_name"] == "create_paper_proposal" and not c["permitted"] for c in calls)
    reset_db()


async def test_kill_switch_blocks_autoloop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("PaperAuto", kill=True)  # kill switch engaged
    _seed()

    await run_agent("paper_trader", trigger_type="manual")

    proposals = repo.list_trade_proposals()
    assert len(proposals) == 1
    assert proposals[0]["status"] == "rejected"  # §1.3: proposal kept, marked rejected

    decisions = repo.list_risk_decisions()
    assert len(decisions) == 1
    assert decisions[0]["approved"] == 0
    assert "kill_switch_active" in decisions[0]["violations"]

    assert len(repo.list_orders()) == 0
    assert len(repo.list_positions(status="open")) == 0
    reset_db()


async def test_autoloop_never_triggers_live(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Auto")  # most permissive
    _seed()

    await run_agent("paper_trader", trigger_type="manual")

    # No order may be live, regardless of mode — the wrapper hard-codes paper_internal.
    for o in repo.list_orders():
        assert o["execution_mode"] != "bitget_live_limited"
    reset_db()


async def test_opportunity_id_linkage_and_idempotency(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("PaperAuto")
    opp = _seed()

    await run_agent("paper_trader", trigger_type="manual")
    proposals = repo.list_trade_proposals()
    assert len(proposals) == 1
    assert proposals[0]["opportunity_id"] == opp.id

    # opportunity consumed -> second run creates no duplicate proposal
    await run_agent("paper_trader", trigger_type="manual")
    assert len(repo.list_trade_proposals()) == 1
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
