"""Tests for storage layer: SQLite, JSONL, Markdown, repositories."""

import json
from unittest.mock import patch
from pathlib import Path

from aletheia.storage.sqlite import get_db, reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    AuditEvent, AgentRun, Opportunity, TradeProposal,
    RiskDecision, Order, Fill, Position, StrategyRecord, KnowledgeItem,
)
from aletheia.storage import repositories as repo
from aletheia.files.jsonl import append_jsonl, read_jsonl, write_audit
from aletheia.files.markdown import write_research_note, write_dreaming_log


def setup_test_db(tmp_path):
    """Configure sqlite to use a temp path."""
    reset_db()
    import aletheia.storage.sqlite as sq
    sq._db_conn = None
    db_file = tmp_path / "test.db"
    import sqlite3
    conn = sqlite3.connect(str(db_file), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    return conn


def test_migrations_create_tables(tmp_path):
    setup_test_db(tmp_path)
    db = get_db()
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    expected = ["audit_events", "agent_runs", "opportunities", "orders", "fills",
                "positions", "strategy_records", "knowledge_items", "trade_proposals", "risk_decisions"]
    for t in expected:
        assert t in tables, f"Missing table: {t}"
    reset_db()


def test_audit_event_crud(tmp_path):
    setup_test_db(tmp_path)
    event = AuditEvent(actor="system", source="cli", event_type="permission_changed", message="Research -> PaperAuto")
    repo.insert_audit_event(event)
    events = repo.list_audit_events()
    assert len(events) == 1
    assert events[0]["event_type"] == "permission_changed"
    reset_db()


def test_agent_run_crud(tmp_path):
    setup_test_db(tmp_path)
    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    repo.insert_agent_run(run)
    repo.update_agent_run(run.id, status="completed", ended_at="2026-01-01T00:00:00")
    result = repo.get_agent_run(run.id)
    assert result["status"] == "completed"
    reset_db()


def test_opportunity_crud(tmp_path):
    setup_test_db(tmp_path)
    opp = Opportunity(symbol="BTCUSDT", market="spot", direction_bias="long", thesis="breakout")
    repo.insert_opportunity(opp)
    items = repo.list_opportunities()
    assert len(items) == 1
    assert items[0]["symbol"] == "BTCUSDT"
    reset_db()


def test_trade_proposal_to_order_flow(tmp_path):
    setup_test_db(tmp_path)
    proposal = TradeProposal(symbol="ETHUSDT", market_type="spot", direction="long")
    repo.insert_trade_proposal(proposal)

    decision = RiskDecision(proposal_id=proposal.id, approved=True, risk_score=0.5, allowed_execution_mode="paper_internal")
    repo.insert_risk_decision(decision)

    order = Order(
        proposal_id=proposal.id, risk_decision_id=decision.id,
        symbol="ETHUSDT", market_type="spot", direction="long",
        order_type="market", quantity=0.01, execution_mode="paper_internal"
    )
    repo.insert_order(order)

    fill = Fill(order_id=order.id, fill_price=3000.0, fill_quantity=0.01)
    repo.insert_fill(fill)

    fills = repo.list_fills()
    assert len(fills) == 1
    assert fills[0]["fill_price"] == 3000.0
    reset_db()


def test_jsonl_roundtrip(tmp_path):
    with patch("aletheia.files.jsonl._log_dir", return_value=tmp_path):
        append_jsonl("test.jsonl", {"event": "test", "value": 42})
        append_jsonl("test.jsonl", {"event": "test2", "value": 99})
        records = read_jsonl("test.jsonl")
        assert len(records) == 2
        assert records[0]["value"] == 42


def test_write_audit_jsonl(tmp_path):
    with patch("aletheia.files.jsonl._log_dir", return_value=tmp_path):
        write_audit({"actor": "human", "source": "dashboard", "event_type": "live_unlocked"})
        records = read_jsonl("audit.jsonl")
        assert len(records) == 1
        assert records[0]["event_type"] == "live_unlocked"
        assert "timestamp" in records[0]


def test_markdown_research_note(tmp_path):
    with patch("aletheia.config.loader.get_data_dir", return_value=tmp_path):
        (tmp_path / "knowledge" / "research_notes").mkdir(parents=True)
        path = write_research_note("BTC Breakout Analysis", "Content here", {"confidence": "high"})
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "# BTC Breakout Analysis" in text
        assert "confidence: high" in text


def test_markdown_dreaming_log(tmp_path):
    with patch("aletheia.config.loader.get_data_dir", return_value=tmp_path):
        (tmp_path / "knowledge" / "dreaming_logs").mkdir(parents=True)
        path = write_dreaming_log("Daily Consolidation", "Synthesized 3 research notes.")
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert "Daily Consolidation" in text
