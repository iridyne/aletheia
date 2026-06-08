"""Phase 3 tests: scheduler, market data, LLM-backed agents, tool logging."""

import pytest
from unittest.mock import patch, AsyncMock

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in (
        "logs", "knowledge/research_notes", "knowledge/daily_reviews",
        "knowledge/dreaming_logs", "knowledge/post_trade_reviews", "knowledge/strategies",
    ):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


# --- Scheduler ---

def test_scheduler_disabled_returns_none(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.scheduler_enabled = False
    save_config(cfg)
    import aletheia.agent_runtime.scheduler as sched
    sched._scheduler = None
    result = sched.init_scheduler()
    assert result is None
    reset_db()


def test_scheduler_starts_when_enabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.agent_runtime.scheduler as sched
    sched._scheduler = None
    s = sched.init_scheduler()
    assert s is not None
    assert sched.is_running()
    # market_data + market_observer + dreaming_daily + strategy_designer_daily
    # + reconciliation + portfolio_snapshot + exchange_sync + paper_trader
    # + position_manager + cleanup + health_check + stock_observer + prediction_observer
    # + post_trade_reviewer + risk_critic
    assert len(s.get_jobs()) == 15
    sched.stop_scheduler()
    reset_db()


# --- Market data ---

@pytest.mark.anyio
async def test_market_data_writes_snapshot(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.data.providers import Ticker
    from aletheia.data import ccxt_provider

    class _FakeCCXT:
        async def get_ticker(self, symbol):
            return Ticker(symbol=symbol, last_price=60000.0, volume_24h=123.0,
                          change_pct_24h=1.5, provider="fake")
        async def get_ohlcv(self, symbol, timeframe, limit):
            return []
        async def close(self):
            pass

    monkeypatch.setattr(ccxt_provider, "CCXTProvider", lambda **kw: _FakeCCXT())

    # Disable xStocks so this test covers only the 3 crypto symbols.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_xstocks = []
    save_config(cfg)

    from aletheia.trading.market_data import collect_market_snapshots
    result = await collect_market_snapshots()
    # 3 observed symbols (BTC/USDT, ETH/USDT, SOL/USDT) × 1 spot each.
    assert len(result["collected"]) == 3
    snaps = repo.list_market_snapshots()
    assert len(snaps) == 3
    assert snaps[0]["price"] == 60000.0
    reset_db()


# --- Agents: graceful degradation when LLM unreachable ---

@pytest.mark.anyio
async def test_research_agent_degrades_without_llm(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # No model server running → llm_complete returns (None, error); agent still completes
    result = await run_agent("research_agent", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    assert any("research_note:" in o for o in result["outputs"])
    notes = repo.list_knowledge_items(item_type="research_note")
    assert len(notes) == 1
    reset_db()


@pytest.mark.anyio
async def test_agent_calls_llm_when_available(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)

    async def fake_complete(system, user, max_tokens=800):
        return ("LLM-generated research content.", None)

    monkeypatch.setattr("aletheia.agents.research_agent.llm_complete", fake_complete)
    result = await run_agent("research_agent", trigger_type="manual")
    assert result["status"] == "completed"
    notes = repo.list_knowledge_items(item_type="research_note")
    assert len(notes) == 1
    reset_db()


# --- Tool-call logging ---

@pytest.mark.anyio
async def test_tool_calls_logged(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    result = await run_agent("research_agent", trigger_type="manual")
    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert len(calls) >= 1
    assert any(c["tool_name"] == "create_research_note" for c in calls)
    reset_db()


# --- New agents exist and run ---

@pytest.mark.anyio
async def test_strategy_designer_creates_strategy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    result = await run_agent("strategy_designer", trigger_type="manual")
    assert any("strategy:" in o for o in result["outputs"])
    assert len(repo.list_strategies()) == 1
    reset_db()


@pytest.mark.anyio
async def test_portfolio_agent_writes_snapshot(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    result = await run_agent("portfolio_agent", trigger_type="manual")
    assert any("portfolio_snapshot:" in o for o in result["outputs"])
    assert len(repo.list_portfolio_snapshots()) == 1
    reset_db()


@pytest.mark.anyio
async def test_risk_critic_and_post_trade_reviewer_run(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    r1 = await run_agent("risk_critic", trigger_type="manual")
    r2 = await run_agent("post_trade_reviewer", trigger_type="manual")
    assert any("research_note:" in o for o in r1["outputs"])
    assert any("post_trade_review:" in o for o in r2["outputs"])
    reset_db()
