"""Tests for Knowledge Base: no hard delete, archive/deprecate, confidence levels."""

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.knowledge.manager import (
    create_daily_review, create_post_trade_review, create_research_note,
    create_dreaming_log, create_strategy_memory,
    archive_item, deprecate_item, delete_item, detect_contradictions,
)
from aletheia.agents.post_trade_reviewer import run_post_trade_reviewer
from aletheia.storage.models import Order, Fill, TradeProposal


def setup_test_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "knowledge" / "research_notes").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "daily_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "dreaming_logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "post_trade_reviews").mkdir(parents=True, exist_ok=True)
    (tmp_path / "knowledge" / "strategies").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def test_create_research_note(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("BTC Analysis", "Content here", confidence="high_confidence")
    assert item.item_type == "research_note"
    assert item.confidence == "high_confidence"
    items = repo.list_knowledge_items(item_type="research_note")
    assert len(items) == 1
    reset_db()


def test_invalid_confidence_rejected(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Invalid confidence"):
        create_research_note("Test", "Content", confidence="invalid")
    reset_db()


def test_archive_item(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("To Archive", "Content")
    archive_item(item.id)
    from aletheia.storage.sqlite import get_db
    row = get_db().execute("SELECT status FROM knowledge_items WHERE id = ?", (item.id,)).fetchone()
    assert row["status"] == "archived"
    reset_db()


def test_deprecate_item(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("To Deprecate", "Content")
    deprecate_item(item.id)
    from aletheia.storage.sqlite import get_db
    row = get_db().execute("SELECT status, confidence FROM knowledge_items WHERE id = ?", (item.id,)).fetchone()
    assert row["status"] == "deprecated"
    assert row["confidence"] == "deprecated"
    reset_db()


def test_hard_delete_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("Protected", "Content")
    with pytest.raises(PermissionError, match="not allowed"):
        delete_item(item.id)
    # Item still exists
    items = repo.list_knowledge_items()
    assert len(items) == 1
    reset_db()


def test_dreaming_log_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_dreaming_log("Nightly Review", "Synthesized findings")
    assert item.item_type == "dreaming_log"
    items = repo.list_knowledge_items(item_type="dreaming_log")
    assert len(items) == 1
    reset_db()


def test_daily_review_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_daily_review("Daily Review", "Daily review content")
    assert item.item_type == "daily_review"
    assert "daily_reviews" in item.file_path
    items = repo.list_knowledge_items(item_type="daily_review")
    assert len(items) == 1
    reset_db()


def test_post_trade_review_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_post_trade_review("Post-Trade Review", "Review content", metadata={"source": "test"})
    assert item.item_type == "post_trade_review"
    assert "post_trade_reviews" in item.file_path
    items = repo.list_knowledge_items(item_type="post_trade_review")
    assert len(items) == 1
    reset_db()


@pytest.mark.anyio
async def test_post_trade_review_links_strategy_from_order_chain(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)

    from aletheia.trading.strategy_lifecycle import create_strategy
    from aletheia.agent_runtime.tool_registry import init_tool_registry

    init_tool_registry()
    strategy = create_strategy("Post Trade Review Strategy")
    proposal = TradeProposal(
        strategy_id=strategy.id,
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=60000,
    )
    repo.insert_trade_proposal(proposal)
    order = Order(
        proposal_id=proposal.id,
        risk_decision_id="x",
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.01,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id,
        fill_price=60000,
        fill_quantity=0.01,
    ))

    result = await run_post_trade_reviewer(permission_mode="Observe")
    review_items = repo.list_knowledge_items(item_type="post_trade_review", strategy_id=strategy.id)
    assert len(review_items) == 1
    if result["errors"]:
        assert any("llm_unavailable" in err for err in result["errors"])
    reset_db()


def test_strategy_memory_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    # strategy_id must be a safe hex id (path-traversal guard in markdown writer).
    item = create_strategy_memory("a1b2c3d4e5f6", "Trend Following V1", "# Strategy\nDetails here")
    assert item.item_type == "strategy_memory"
    reset_db()


def test_contradiction_detection_stub():
    result = detect_contradictions([])
    assert result == []
