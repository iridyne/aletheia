"""Tests for storage layer: SQLite, JSONL, Markdown, repositories."""

import json
import pytest
from unittest.mock import patch
from pathlib import Path

from aletheia.storage.sqlite import get_db, reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    AuditEvent, AgentRun, Opportunity, TradeProposal,
    RiskDecision, Order, Fill, Position, StrategyRecord, KnowledgeItem,
    SerenityTheme, SerenityEvidence, SerenityOpportunity,
    SerenityRiskGate, SerenityReplaySnapshot, SerenityPostMortem,
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
                "positions", "strategy_records", "knowledge_items", "trade_proposals", "risk_decisions",
                "serenity_themes", "serenity_evidence", "serenity_opportunities",
                "serenity_risk_gates", "serenity_replay_snapshots", "serenity_post_mortems"]
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
    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=3000.0,
    )
    repo.insert_trade_proposal(proposal)

    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        risk_score=0.5,
        allowed_execution_mode="paper_internal",
        proposal_execution_mode="paper_internal",
        decision_role="authoritative",
        evaluated_quantity=0.01,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)

    order = Order(
        proposal_id=proposal.id, risk_decision_id=decision.id,
        symbol="ETH/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.01, price=proposal.price,
        execution_mode="paper_internal"
    )
    repo.insert_order(order)

    fill = Fill(order_id=order.id, fill_price=3000.0, fill_quantity=0.01)
    repo.insert_fill(fill)

    fills = repo.list_fills()
    assert len(fills) == 1
    assert fills[0]["fill_price"] == 3000.0
    reset_db()


def test_serenity_repo_crud(tmp_path):
    setup_test_db(tmp_path)
    theme = SerenityTheme(name="AI Supply Chain")
    repo.insert_serenity_theme(theme)
    themes = repo.list_serenity_themes()
    assert len(themes) == 1
    assert themes[0]["name"] == "AI Supply Chain"

    evidence = SerenityEvidence(theme_id=themes[0]["id"], title="Substack Article", source="manual")
    repo.insert_serenity_evidence(evidence)
    evidence_rows = repo.list_serenity_evidence(theme_id=themes[0]["id"])
    assert len(evidence_rows) == 1
    assert evidence_rows[0]["title"] == "Substack Article"

    opp = SerenityOpportunity(
        theme_id=themes[0]["id"],
        symbol="NVDA",
        direction="long",
        stage="research",
    )
    repo.insert_serenity_opportunity(opp)
    opp_rows = repo.list_serenity_opportunities(theme_id=themes[0]["id"])
    assert len(opp_rows) == 1
    assert opp_rows[0]["symbol"] == "NVDA"

    gate = SerenityRiskGate(
        opportunity_id=opp_rows[0]["id"],
        gate_name="liquidity",
        status="warn",
        metric="adv",
        observed=0.1,
        threshold=0.2,
        message="monitor",
    )
    repo.insert_serenity_risk_gate(gate)
    gates = repo.list_serenity_risk_gates(opportunity_id=opp_rows[0]["id"])
    assert len(gates) == 1
    assert gates[0]["status"] == "warn"

    snapshot = SerenityReplaySnapshot(
        opportunity_id=opp_rows[0]["id"],
        as_of="2026-06-11T00:00:00",
        event_type="snapshot",
    )
    repo.insert_serenity_replay_snapshot(snapshot)
    snapshots = repo.list_serenity_replay_snapshots(opportunity_id=opp_rows[0]["id"])
    assert len(snapshots) == 1

    post_mortem = SerenityPostMortem(
        opportunity_id=opp_rows[0]["id"],
        outcome="hold",
        summary="no thesis drift observed",
    )
    repo.insert_serenity_post_mortem(post_mortem)
    post_mortems = repo.list_serenity_post_mortems(opportunity_id=opp_rows[0]["id"])
    assert len(post_mortems) == 1
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


# --- Regression: writes must COMMIT (release WAL lock), not hang open ---
# Root cause caught 2026-06-09: _insert/_update committed only when
# `not was_in_tx and not db.in_transaction`, but sqlite3 auto-opens a tx on the
# write so `db.in_transaction` is always True afterwards → commit never ran → the
# write tx hung open holding the WAL lock → every other thread's write hit
# `database is locked`. These tests use the REAL get_db() path (per-thread conn
# on a file DB), NOT the shared injected conn that masks the bug.

def _real_db_env(tmp_path, monkeypatch):
    """Point sqlite at a real file via get_data_dir and clear any injected conn."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sq.reset_db()
    sq._db_conn = None
    run_migrations()  # runs on the real per-thread connection


@pytest.mark.real_db
def test_insert_commits_and_releases_lock(tmp_path, monkeypatch):
    import sqlite3
    from aletheia.storage.sqlite import db_path
    _real_db_env(tmp_path, monkeypatch)

    repo.insert_audit_event(AuditEvent(actor="system", source="cli",
                                       event_type="lock_test", message="m"))

    # A SEPARATE connection must see the row immediately (proves it committed) and
    # must be able to WRITE without timing out (proves the lock was released).
    other = sqlite3.connect(str(db_path()), timeout=2)
    try:
        cnt = other.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='lock_test'"
        ).fetchone()[0]
        assert cnt == 1, "row not visible to a second connection — write never committed"
        other.execute("PRAGMA journal_mode=WAL")
        other.execute(
            "INSERT INTO audit_events (id, actor, source, event_type, message, timestamp) "
            "VALUES ('x','system','cli','other','m','2026-01-01T00:00:00')"
        )
        other.commit()  # would raise OperationalError: database is locked if held
    finally:
        other.close()


@pytest.mark.real_db
def test_update_commits_and_releases_lock(tmp_path, monkeypatch):
    import sqlite3
    from aletheia.storage.sqlite import db_path
    _real_db_env(tmp_path, monkeypatch)

    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    repo.insert_agent_run(run)
    repo.update_agent_run(run.id, status="completed")

    other = sqlite3.connect(str(db_path()), timeout=2)
    try:
        row = other.execute(
            "SELECT status FROM agent_runs WHERE id=?", (run.id,)
        ).fetchone()
        assert row is not None and row[0] == "completed", "update not committed"
    finally:
        other.close()
