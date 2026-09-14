"""Tests for Knowledge Base: no hard delete, archive/deprecate, confidence levels."""

from datetime import datetime, timezone

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.knowledge.manager import (
    create_daily_review, create_post_trade_review, create_research_note,
    create_dreaming_log, create_strategy_memory, create_trade_case_study,
    archive_item, deprecate_item, delete_item, detect_contradictions,
)
from extensions.learning.post_trade_reviewer import run_post_trade_reviewer


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

    from extensions.learning import post_trade_reviewer
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage.repositories import insert_strategy
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    init_tool_registry()
    strategy = StrategyRecord(name="Post Trade Review Strategy", state="draft")
    insert_strategy(strategy)
    _insert_closed_position_via_producer(
        position_id="position-knowledge-review",
        symbol="BTC/USDT", market_type="spot", direction="long", quantity=0.01,
        entry_price=60_000.0, realized_pnl=10.0, execution_mode="bitget_demo",
        strategy_id=strategy.id, close_reason="take_profit",
        closed_at=datetime.now(timezone.utc).isoformat(),
    )

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

    from extensions.learning import post_trade_reviewer
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
                "bitget_demo",
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
    first = create_strategy_memory(
        "a1b2c3d4e5f6", "Trend Following V1", "# Strategy\nDetails here"
    )
    second = create_strategy_memory(
        "a1b2c3d4e5f6", "Trend Following V2", "# Strategy\nUpdated details"
    )
    assert first.item_type == "strategy_memory"
    assert first.file_path != second.file_path
    from aletheia.knowledge.document import read_knowledge_document
    from aletheia.storage.repositories import get_knowledge_item

    first_row = get_knowledge_item(first.id)
    assert first_row is not None
    assert read_knowledge_document(first_row).state == "available"
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

def test_contradiction_detection_rejects_integrity_conflicts():
    from aletheia.knowledge.document import canonicalize_inline_document

    tampered = {
        "id": "memo-tampered",
        "item_type": "trading_memo",
        "title": "BTC/USDT long support",
        "status": "active",
        **canonicalize_inline_document(
            "Original canonical evidence with enough detail to remain independently checkable."
        ),
    }
    tampered["content"] = "Research supports a BTC/USDT long opportunity."
    guard = {
        "id": "guard-1",
        "item_type": "trading_guard",
        "title": "Avoid BTC/USDT long",
        "content": "Avoid BTC/USDT long entries.",
        "metadata": {
            "symbol": "BTC/USDT",
            "direction": "long",
            "block_candidate": True,
        },
        "status": "active",
    }
    assert detect_contradictions([tampered, guard]) == []


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


def test_trade_case_dedup_is_position_keyed(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    first = create_trade_case_study(
        "Case: BTC/USDT long (stop_loss)",
        _VALID_CONTENT,
        metadata={"position_id": "pos-1", "symbol": "BTC/USDT"},
        confidence="high_confidence",
    )
    second = create_trade_case_study(
        "Case: BTC/USDT long (stop_loss)",
        _VALID_CONTENT,
        metadata={"position_id": "pos-2", "symbol": "BTC/USDT"},
        confidence="high_confidence",
    )
    retry = create_trade_case_study(
        "Case: BTC/USDT long (stop_loss)",
        _VALID_CONTENT + " Extra retry prose with the same durable Position identity.",
        metadata={"position_id": "pos-1", "symbol": "BTC/USDT"},
        confidence="high_confidence",
    )

    assert first is not None
    assert second is not None
    assert retry is None
    assert first.content_hash != second.content_hash
    assert len(repo.list_knowledge_items(item_type="trade_case_study")) == 2
    reset_db()


def test_trade_case_requires_position_identity(tmp_path, monkeypatch):
    setup_test_env(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="metadata.position_id"):
        create_trade_case_study(
            "Case: missing identity",
            _VALID_CONTENT,
            metadata={"symbol": "BTC/USDT"},
            confidence="high_confidence",
        )


@pytest.mark.parametrize(
    ("create_name", "writer_name", "subdir", "kwargs"),
    [
        ("create_research_note", "write_research_note", "research_notes", {"confidence": "high_confidence"}),
        ("create_post_trade_review", "write_post_trade_review", "post_trade_reviews", {}),
    ],
)
def test_concurrent_content_hash_dedup_removes_loser_artifact(
    tmp_path, monkeypatch, create_name, writer_name, subdir, kwargs,
):
    import concurrent.futures
    import threading
    import aletheia.knowledge.manager as manager
    from aletheia.storage.sqlite import get_db

    setup_test_env(tmp_path, monkeypatch)
    original_writer = getattr(manager, writer_name)
    barrier = threading.Barrier(2)

    def racing_writer(*args, **writer_kwargs):
        path = original_writer(*args, **writer_kwargs)
        barrier.wait(timeout=5)
        return path

    monkeypatch.setattr(manager, writer_name, racing_writer)
    create = getattr(manager, create_name)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda title: create(title, _VALID_CONTENT, **kwargs),
                ("Race One", "Race Two"),
            )
        )

    rows = get_db().execute(
        "SELECT id, file_path FROM knowledge_items WHERE content_hash IS NOT NULL"
    ).fetchall()
    files = list((tmp_path / "knowledge" / subdir).glob("*.md"))

    assert sum(item is not None for item in results) == 1
    assert len(rows) == 1
    assert len(files) == 1
    assert files[0].relative_to(tmp_path).as_posix() == rows[0]["file_path"]
    reset_db()


def test_concurrent_dreaming_title_window_removes_loser_artifact(tmp_path, monkeypatch):
    import concurrent.futures
    import threading
    import aletheia.knowledge.manager as manager
    from aletheia.storage.sqlite import get_db

    setup_test_env(tmp_path, monkeypatch)
    original_writer = manager.write_dreaming_log
    barrier = threading.Barrier(2)

    def racing_writer(*args, **kwargs):
        path = original_writer(*args, **kwargs)
        barrier.wait(timeout=5)
        return path

    monkeypatch.setattr(manager, "write_dreaming_log", racing_writer)
    content_a = _VALID_CONTENT + " First concurrent dreaming synthesis."
    content_b = _VALID_CONTENT + " Second concurrent dreaming synthesis."

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda content: manager.create_dreaming_log("Same Dream", content),
                (content_a, content_b),
            )
        )

    rows = get_db().execute(
        "SELECT id, file_path FROM knowledge_items WHERE item_type='dreaming_log'"
    ).fetchall()
    files = list((tmp_path / "knowledge" / "dreaming_logs").glob("*.md"))

    assert sum(item is not None for item in results) == 1
    assert len(rows) == 1
    assert len(files) == 1
    assert files[0].relative_to(tmp_path).as_posix() == rows[0]["file_path"]
    reset_db()
