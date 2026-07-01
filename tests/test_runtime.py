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


def setup_real_db_env(tmp_path, monkeypatch):
    """Use the production per-thread SQLite connection path for lock tests."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    run_migrations()


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
    from datetime import datetime, timedelta
    import aletheia.agent_runtime.scheduler as sched
    sched._scheduler = None
    started_at = datetime.now()
    s = sched.init_scheduler()
    assert s is not None
    assert sched.is_running()
    # Assert the CRITICAL jobs are registered by NAME rather than a brittle exact
    # count: a hard `== N` breaks on every newly-added job (no behavioral
    # regression) while silently passing if two jobs are swapped. The real
    # regression to catch is a critical loop going MISSING — so check membership.
    # (Some jobs are config-conditional, e.g. ohlcv_nightly_backfill only when
    # historical_data.nightly_backfill_enabled — excluded from the required set.)
    job_ids = {j.id for j in s.get_jobs()}
    required = {
        "market_data", "market_observer", "paper_trader", "pi_trading_decision",
        "position_manager", "reconciliation", "position_reconcile",
        "portfolio_snapshot", "exchange_sync", "health_check", "cleanup",
        "price_feed", "tpsl_fill_check", "connectivity_monitor",
        "strategy_designer_daily", "post_trade_reviewer", "risk_critic",
        "universe_refresh", "pi_operator_loop",
    }
    missing = required - job_ids
    assert not missing, f"critical scheduler jobs missing: {missing}"
    # Floor guards against wholesale registration failure without pinning an exact N.
    assert len(job_ids) >= len(required)
    market_observer_job = s.get_job("market_observer")
    assert market_observer_job is not None
    assert market_observer_job.next_run_time <= started_at.astimezone(market_observer_job.next_run_time.tzinfo) + timedelta(seconds=120)
    assert market_observer_job.executor == "observer"
    assert market_observer_job.max_instances == 1
    assert market_observer_job.coalesce is True
    market_data_job = s.get_job("market_data")
    assert market_data_job is not None
    assert market_data_job.executor == "io"
    post_trade_job = s.get_job("post_trade_reviewer")
    assert post_trade_job is not None
    assert post_trade_job.executor == "agents"
    sched.stop_scheduler()
    reset_db()


@pytest.mark.real_db
def test_scheduler_guard_rolls_back_leaked_sqlite_transaction(tmp_path, monkeypatch):
    """Guard rolls back a leaked explicit transaction (BEGIN without COMMIT).

    With isolation_level=None (autocommit), standalone DML auto-commits and can't
    leak. But an explicit BEGIN without COMMIT (e.g. a crashed transaction() block)
    still needs cleanup — the guard handles that case.
    """
    setup_real_db_env(tmp_path, monkeypatch)

    from aletheia.agent_runtime import scheduler as sched
    from aletheia.storage.sqlite import db_path, get_db
    import sqlite3

    def leaky_job():
        db = get_db()
        db.execute("BEGIN")
        db.execute(
            "INSERT INTO audit_events (id, actor, source, event_type, message, timestamp) "
            "VALUES ('leaked','system','test','leaked_tx','uncommitted','2026-01-01T00:00:00')"
        )
        assert db.in_transaction

    sched._run_job_guarded("leaky_job", leaky_job)

    other = sqlite3.connect(str(db_path()), timeout=1)
    try:
        leaked_count = other.execute(
            "SELECT COUNT(*) FROM audit_events WHERE id = 'leaked'"
        ).fetchone()[0]
        assert leaked_count == 0
        other.execute(
            "INSERT INTO audit_events (id, actor, source, event_type, message, timestamp) "
            "VALUES ('after','system','test','after_guard','writer available','2026-01-01T00:00:01')"
        )
        other.commit()
    finally:
        other.close()
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

    # Disable xStocks so this test covers only the 3 crypto symbols (spot-only
    # collection path).  Must clear both the new stock-token field and the
    # legacy alias — the new field has a non-empty default that would add
    # r-series instruments to the collect run.
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_xstocks = []
    cfg.market_data.observed_stock_tokens = []
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
        return ("LLM-generated research content: BTC consolidating near 200MA with declining volume; "
                "ETH funding rate turning negative signals positioning squeeze. Medium-confidence.", None)

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
    # LLM unavailable in test → agents skip writes (by design), only log errors
    assert r1["outputs"] or r1["errors"]
    assert r2["outputs"] or r2["errors"]
    reset_db()
