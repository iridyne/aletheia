"""§32 MVP acceptance criteria, exercised through the API/CLI (not internals).

Each test maps to one of the 16 acceptance criteria in IMPLEMENTATION_GUIDE.md §32.
"""

import json
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from typer.testing import CliRunner

from aletheia.server.app import app
from aletheia.main import app as cli_app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

runner = CliRunner()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    reset_db()


# #3 gateway status/stop/pause/resume works (CLI)
def test_ac03_gateway_status_cli(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    r = runner.invoke(cli_app, ["gateway", "status"])
    assert r.exit_code == 0
    assert "stopped" in r.output


# #4 SQLite + JSONL + Markdown storage initialized
@pytest.mark.anyio
async def test_ac04_storage_initialized(client):
    c, tmp_path = client
    await c.get("/api/status")
    assert (tmp_path / "test.db").exists()  # sqlite
    # audit jsonl after an action
    await c.post("/api/settings/permission-mode", json={"mode": "Observe"})
    assert (tmp_path / "logs" / "audit.jsonl").exists()


# #5 Dashboard pages backed by data endpoints
@pytest.mark.anyio
async def test_ac05_dashboard_data_endpoints(client):
    c, _ = client
    for path in ["/api/agent-runs", "/api/opportunities", "/api/proposals", "/api/trades",
                 "/api/positions", "/api/strategies", "/api/portfolio", "/api/settings",
                 "/api/audit/events"]:
        assert (await c.get(path)).status_code == 200


# #6 Model provider layer (OpenAI/Anthropic/Codex)
@pytest.mark.anyio
async def test_ac06_model_providers(client):
    c, _ = client
    data = (await c.get("/api/model")).json()
    assert set(["openai_compatible", "anthropic_compatible", "codex_cli"]).issubset(data["providers"].keys())


# #7 Agent runtime creates research notes + opportunities
@pytest.mark.anyio
async def test_ac07_agent_creates_artifacts(client, monkeypatch):
    c, _ = client
    # Mock LLM to return substantive content so quality gate passes.
    async def _fake_llm(system, user, max_tokens=800):
        return ("BTC 4h shows momentum divergence with RSI overbought while price consolidates. "
                "ETH funding rates suggest crowded longs. Recommend reducing spot exposure.", None)
    monkeypatch.setattr("aletheia.agents.research_agent.llm_complete", _fake_llm)

    await c.post("/api/agent-runs/start", json={"agent_name": "research_agent"})
    await c.post("/api/agent-runs/start", json={"agent_name": "market_observer"})
    assert len((await c.get("/api/agent-runs")).json()) >= 2
    assert len(repo.list_knowledge_items(item_type="research_note")) >= 1


# #8 Knowledge base stores items
@pytest.mark.anyio
async def test_ac08_knowledge_base(client, monkeypatch):
    c, _ = client
    # Mock LLM to return substantive content so quality gate passes.
    async def _fake_llm(system, user, max_tokens=800, **kw):
        return ("Daily dreaming log: market observer ran 3 cycles, research agent produced 2 notes. "
                "Insight: momentum correlated with funding divergence. Focus: validate SOL breakout.", None)
    monkeypatch.setattr("aletheia.agents.dreaming_agent.llm_complete", _fake_llm)

    await c.post("/api/agent-runs/start", json={"agent_name": "dreaming_agent"})
    assert len((await c.get("/api/knowledge/dreaming-logs")).json()) >= 1
    assert len((await c.get("/api/knowledge?type=daily_review")).json()) >= 1


@pytest.mark.anyio
async def test_review_summary_reports_missing_post_trade_review_coverage(client):
    c, tmp_path = client
    from aletheia.knowledge.manager import create_post_trade_review
    from aletheia.storage.models import Position

    for path in ("knowledge/post_trade_reviews", "knowledge/daily_reviews", "knowledge/dreaming_logs"):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)

    repo.insert_position(
        Position(
            symbol="BTCUSDT",
            market_type="spot",
            direction="long",
            quantity=0.1,
            entry_price=60000,
            execution_mode="paper_internal",
            strategy_id="strat-1",
            status="closed",
            opened_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    first = await c.get("/api/reviews/summary")
    assert first.status_code == 200
    first_data = first.json()
    assert len(first_data["recent_closed_positions"]) == 1
    assert first_data["missing_review_strategy_ids"] == ["strat-1"]

    create_post_trade_review(
        "Post-Trade Review",
        "Reviewed the BTC paper position closed at a loss. Entry momentum confirmed but exit was premature. "
        "Recommend tighter stop placement and trailing stop on profitable positions.",
        strategy_id="strat-1",
    )

    second = await c.get("/api/reviews/summary")
    assert second.status_code == 200
    second_data = second.json()
    assert len(second_data["recent_post_trade_reviews"]) == 1
    assert second_data["missing_review_strategy_ids"] == []


@pytest.mark.anyio
async def test_strategy_summary_reports_strategies_needing_operator_evaluation(client):
    c, _ = client
    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    strategy = create_strategy("Operator Review Candidate", "paper strategy awaiting review")
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    insert_pi_decision(PiDecision(symbol=None, direction=None, action="recommend_promote", conviction=0, reasoning="test", proposal_id=strategy.id, debate_ran=False))
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        result = promote_strategy(strategy.id, target)
        assert result["success"] is True

    repo.update_strategy(
        strategy.id,
        under_review=1,
        review_notes="drawdown needs operator review",
        review_updated_at=datetime.now(timezone.utc).isoformat(),
    )

    first = await c.get("/api/strategies/summary")
    assert first.status_code == 200
    first_data = first.json()
    assert any(item["id"] == strategy.id for item in first_data["under_review_strategies"])
    assert strategy.id in first_data["needs_operator_evaluation_strategy_ids"]

    create_artifact(
        task_id=None,
        session_id="operator-main",
        artifact_type="strategy_evaluation",
        title="Operator strategy evaluation",
        content={"strategy_id": strategy.id, "summary": "reviewed by operator"},
    )

    second = await c.get("/api/strategies/summary")
    assert second.status_code == 200
    second_data = second.json()
    assert any(item["strategy_id"] == strategy.id for item in second_data["recent_strategy_evaluations"])
    assert strategy.id not in second_data["needs_operator_evaluation_strategy_ids"]


@pytest.mark.anyio
async def test_strategy_summary_reports_promotion_ready_strategies_needing_proposal(client):
    c, _ = client
    from aletheia.agent_shell.operator_store import create_artifact
    from aletheia.trading.strategy_lifecycle import create_strategy, promote_strategy

    strategy = create_strategy("Promotion Candidate", "paper strategy ready for graduation")
    from aletheia.storage.models import PiDecision
    from aletheia.storage.repositories import insert_pi_decision
    insert_pi_decision(PiDecision(symbol=None, direction=None, action="recommend_promote", conviction=0, reasoning="test", proposal_id=strategy.id, debate_ran=False))
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        result = promote_strategy(strategy.id, target)
        assert result["success"] is True

    create_artifact(
        task_id=None,
        session_id="strategy-eval-session",
        artifact_type="strategy_evaluation",
        title="Promotion-ready evaluation",
        content={
            "strategy_id": strategy.id,
            "strategy_name": "Promotion Candidate",
            "summary": "Promotion gates passed for paper_running strategy.",
            "evaluation": {
                "strategy": {
                    "id": strategy.id,
                    "name": "Promotion Candidate",
                    "state": "paper_running",
                },
                "promotion_gates": {
                    "passed": True,
                    "failures": [],
                },
            },
        },
    )

    first = await c.get("/api/strategies/summary")
    assert first.status_code == 200
    first_data = first.json()
    assert strategy.id in first_data["needs_operator_promotion_proposal_strategy_ids"]

    create_artifact(
        task_id=None,
        session_id="strategy-proposal-session",
        artifact_type="strategy_promotion_proposal",
        title="Promote Promotion Candidate to paper_passed",
        content={
            "strategy_id": strategy.id,
            "strategy_name": "Promotion Candidate",
            "current_state": "paper_running",
            "target_state": "paper_passed",
            "requires_human_approval": True,
        },
    )

    second = await c.get("/api/strategies/summary")
    assert second.status_code == 200
    second_data = second.json()
    assert any(item["strategy_id"] == strategy.id for item in second_data["recent_strategy_promotion_proposals"])
    assert strategy.id not in second_data["needs_operator_promotion_proposal_strategy_ids"]


# #11 paper proposal -> risk -> order -> fill -> position
@pytest.mark.anyio
async def test_ac11_paper_trade_flow(client):
    c, _ = client
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)
    from aletheia.storage.models import TradeProposal
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      quantity=0.001, price=60000, execution_mode="paper_internal")
    repo.insert_trade_proposal(p)
    resp = await c.post(f"/api/proposals/{p.id}/approve-paper")
    assert resp.json()["execution"]["status"] == "executed"
    assert resp.json()["execution"]["proposal_id"] == p.id
    assert len(repo.list_trade_proposals()) == 1
    assert repo.list_orders()[0]["proposal_id"] == p.id
    assert len(repo.list_orders()) == 1
    assert len(repo.list_fills()) == 1
    assert len(repo.list_positions(status="open")) == 1


# #12 Risk engine blocks disallowed symbols + live by default
@pytest.mark.anyio
async def test_ac12_risk_blocks(client):
    c, _ = client
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    save_config(cfg)
    from aletheia.execution.gateway import submit_proposal
    bad = submit_proposal(symbol="DOGEUSDT", market_type="spot", direction="long",
                          quantity=1, price=1, execution_mode="paper_internal")
    assert bad["status"] == "rejected"


# #13 permission changes visible + audited
@pytest.mark.anyio
async def test_ac13_permission_visible_audited(client):
    c, tmp_path = client
    await c.post("/api/settings/permission-mode", json={"mode": "Full"})
    assert (await c.get("/api/status")).json()["permission_mode"] == "Full"
    events = [json.loads(l) for l in (tmp_path / "logs" / "audit.jsonl").read_text().splitlines() if l.strip()]
    assert any(e["event_type"] == "permission_changed" for e in events)


# #15 no live trade unless explicitly unlocked
@pytest.mark.anyio
async def test_ac15_live_locked_by_default(client):
    c, _ = client
    assert (await c.get("/api/status")).json()["live_state"] == "live_disabled"


# #1/#2 helpers exist (gateway start + browser) — smoke that lifespan wiring imports
def test_ac01_lifespan_imports():
    from aletheia.server.app import lifespan, app as fastapi_app
    assert lifespan is not None
    # health route registered
    paths = {r.path for r in fastapi_app.routes}
    assert "/api/health" in paths
