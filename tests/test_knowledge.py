"""Tests for Knowledge Base: no hard delete, archive/deprecate, confidence levels."""

from datetime import datetime, timezone

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


_VALID_CONTENT = "BTC showed a breakout above the 50-day SMA with volume confirmation. Entry at 64200 with stop at 62800 and target 68000."


def test_create_research_note(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("BTC Analysis", _VALID_CONTENT, confidence="high_confidence")
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
    item = create_research_note("To Archive", _VALID_CONTENT, confidence="high_confidence")
    archive_item(item.id)
    from aletheia.storage.sqlite import get_db
    row = get_db().execute("SELECT status FROM knowledge_items WHERE id = ?", (item.id,)).fetchone()
    assert row["status"] == "archived"
    reset_db()


def test_deprecate_item(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("To Deprecate", _VALID_CONTENT, confidence="high_confidence")
    deprecate_item(item.id)
    from aletheia.storage.sqlite import get_db
    row = get_db().execute("SELECT status, confidence FROM knowledge_items WHERE id = ?", (item.id,)).fetchone()
    assert row["status"] == "deprecated"
    assert row["confidence"] == "deprecated"
    reset_db()


def test_hard_delete_blocked(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("Protected", _VALID_CONTENT, confidence="high_confidence")
    with pytest.raises(PermissionError, match="not allowed"):
        delete_item(item.id)
    # Item still exists
    items = repo.list_knowledge_items()
    assert len(items) == 1
    reset_db()


def test_dreaming_log_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_dreaming_log("Nightly Review", _VALID_CONTENT)
    assert item.item_type == "dreaming_log"
    items = repo.list_knowledge_items(item_type="dreaming_log")
    assert len(items) == 1
    reset_db()


def test_daily_review_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_daily_review("Daily Review", "Daily review content: market opened flat, momentum indicators neutral, no trades taken today, system operating normally.")
    assert item.item_type == "daily_review"
    assert "daily_reviews" in item.file_path
    items = repo.list_knowledge_items(item_type="daily_review")
    assert len(items) == 1
    reset_db()


def test_post_trade_review_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_post_trade_review("Post-Trade Review", _VALID_CONTENT, metadata={"source": "test"})
    assert item.item_type == "post_trade_review"
    assert "post_trade_reviews" in item.file_path
    items = repo.list_knowledge_items(item_type="post_trade_review")
    assert len(items) == 1
    reset_db()


@pytest.mark.anyio
async def test_post_trade_review_links_strategy_from_order_chain(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)

    from aletheia.agents import post_trade_reviewer
    from aletheia.storage.models import Position, StrategyRecord
    from aletheia.storage.repositories import insert_strategy
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from tests.test_outcome_closure import _insert_entry_chain

    init_tool_registry()
    strategy = StrategyRecord(name="Post Trade Review Strategy", state="draft")
    insert_strategy(strategy)
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id="position-knowledge-review",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=60000,
        execution_mode="paper_internal",
        strategy_id=strategy.id,
    )
    repo.insert_position(Position(
        id="position-knowledge-review",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        entry_price=60_000.0,
        current_price=61_000.0,
        realized_pnl=10.0,
        execution_mode="paper_internal",
        strategy_id=strategy.id,
        proposal_id=proposal_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        status="closed",
        close_reason="take_profit",
        closed_at=datetime.now(timezone.utc).isoformat(),
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="not_applicable",
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
    ))

    async def deterministic_review(*args, **kwargs):
        return (
            "The verified close supports the original long thesis; repeat this setup "
            "only when the same entry evidence and risk controls are present.",
            None,
        )

    monkeypatch.setattr(post_trade_reviewer, "llm_complete", deterministic_review)

    result = await run_post_trade_reviewer(permission_mode="Observe")
    review_items = repo.list_knowledge_items(item_type="post_trade_review", strategy_id=strategy.id)
    assert result["errors"] == []
    assert len(review_items) == 1
    assert "position:position-knowledge-review" in review_items[0]["tags"]
    reset_db()

@pytest.mark.anyio
async def test_post_trade_reviewer_scans_prefix_while_forward_page_stays_nonempty(
    tmp_path, monkeypatch
):
    """A growing suffix cannot permanently hide older strategy identities."""
    setup_test_env(tmp_path, monkeypatch)

    from aletheia.agents import post_trade_reviewer
    from aletheia.agents.round_robin import DurableRoundRobin
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for strategy_id in (
        [f"a-{index:02d}" for index in range(21)]
        + [f"z-{index:02d}" for index in range(21)]
    ):
        db.execute(
            "INSERT INTO positions (id, symbol, market_type, direction, quantity, "
            "entry_price, execution_mode, strategy_id, status, outcome_eligible, "
            "outcome_exclusion_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"position-{strategy_id}",
                "BTC/USDT",
                "spot",
                "long",
                1.0,
                100.0,
                "paper_internal",
                strategy_id,
                "closed",
                1,
                "eligible",
            ),
        )

    # These archived reviews represent re-opened evidence. Ordinary completed
    # strategies remain on the forward cursor rather than being rescanned.
    db.executemany(
        "INSERT INTO knowledge_items "
        "(id, item_type, title, strategy_id, status, tags) "
        "VALUES (?, 'post_trade_review', ?, ?, 'archived', ?)",
        [
            (
                f"archived-review-{strategy_id}",
                "Archived review",
                strategy_id,
                f'["position:position-{strategy_id}"]',
            )
            for strategy_id in (f"a-{index:02d}" for index in range(21))
        ],
    )

    state_key = "agents.post_trade_reviewer.strategy_round_robin.v1"
    DurableRoundRobin(state_key).advance_after(
        [{"strategy_key": "1:y-99"}, {"strategy_key": "1:z-00"}],
        completed_key="1:y-99",
        key=lambda row: row["strategy_key"],
    )

    calls: list[str] = []

    def no_outcomes(*, strategy_id, limit):
        calls.append(strategy_id)
        return []

    monkeypatch.setattr(post_trade_reviewer, "is_tool_permitted", lambda *args, **kwargs: True)
    monkeypatch.setattr(post_trade_reviewer, "eligible_strategy_outcomes", no_outcomes)
    monkeypatch.setattr(post_trade_reviewer, "_run_case_builder_safe", lambda *_: ([], [], []))

    result = await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")
    assert result["errors"] == []
    assert calls == [
        value
        for index in range(10)
        for value in (f"a-{index:02d}", f"z-{index:02d}")
    ]

    restarted = DurableRoundRobin(state_key)
    assert restarted.next_key() == "1:z-10"
    assert restarted.prefix_key() == "1:a-10"

    calls.clear()
    await post_trade_reviewer.run_post_trade_reviewer(permission_mode="Observe")
    assert calls == [
        value
        for index in range(10)
        for value in (f"a-{index + 10:02d}", f"z-{index + 10:02d}")
    ]
    assert DurableRoundRobin(state_key).prefix_key() == "1:a-20"
    reset_db()


def test_strategy_memory_creation(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    # strategy_id must be a safe hex id (path-traversal guard in markdown writer).
    item = create_strategy_memory("a1b2c3d4e5f6", "Trend Following V1", "# Strategy\nDetails here")
    assert item.item_type == "strategy_memory"
    reset_db()


def test_contradiction_detection_empty_input():
    assert detect_contradictions([]) == []


def test_contradiction_detection_flags_support_vs_block():
    result = detect_contradictions([
        {
            "id": "memo-1",
            "item_type": "trading_memo",
            "title": "BTC/USDT long opportunity",
            "content": "Research supports a BTC/USDT long opportunity after breakout.",
            "confidence": "high_confidence",
            "status": "active",
        },
        {
            "id": "guard-1",
            "item_type": "trading_guard",
            "title": "Avoid BTC/USDT long",
            "content": "Avoid BTC/USDT long entries when the setup is crowded.",
            "metadata": {
                "symbol": "BTC/USDT",
                "direction": "long",
                "block_candidate": True,
            },
            "confidence": "high_confidence",
            "status": "active",
        },
    ])

    assert len(result) == 1
    assert result[0]["kind"] == "support_vs_block"
    assert result[0]["symbol"] == "BTC/USDT"
    assert result[0]["direction"] == "long"
    assert result[0]["supporting_items"][0]["id"] == "memo-1"
    assert result[0]["blocking_items"][0]["id"] == "guard-1"


def test_contradiction_detection_ignores_inactive_items():
    result = detect_contradictions([
        {
            "id": "memo-1",
            "item_type": "trading_memo",
            "title": "ETH/USDT short opportunity",
            "content": "Research supports an ETH/USDT short opportunity.",
            "status": "active",
        },
        {
            "id": "guard-1",
            "item_type": "trading_guard",
            "title": "Avoid ETH/USDT short",
            "content": "Avoid ETH/USDT short entries.",
            "metadata": {"symbol": "ETH/USDT", "direction": "short", "block_candidate": True},
            "status": "archived",
        },
    ])

    assert result == []


def test_quality_gate_rejects_short_content(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item = create_research_note("Short", "Too short", confidence="high_confidence")
    assert item is None
    items = repo.list_knowledge_items(item_type="research_note")
    assert len(items) == 0
    reset_db()


def test_quality_gate_rejects_placeholder(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    placeholder = "Content here " * 10  # Long enough but matches placeholder pattern
    item = create_research_note("Placeholder", placeholder, confidence="high_confidence")
    assert item is None
    reset_db()


def test_dedup_rejects_identical_content(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    item1 = create_research_note("First", _VALID_CONTENT, confidence="high_confidence")
    assert item1 is not None
    item2 = create_research_note("Second", _VALID_CONTENT, confidence="high_confidence")
    assert item2 is None  # Duplicate blocked
    items = repo.list_knowledge_items(item_type="research_note")
    assert len(items) == 1
    reset_db()
