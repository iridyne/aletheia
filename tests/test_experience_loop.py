"""Tests for the experience loop — read prior lessons back into decisions (§6).

Governance under test:
- READ-ONLY + QUALITY GATE: only high_confidence + active items are retrievable.
- ADVISORY: the injected block is labelled advisory and is pure prompt text.
- DEGRADES: missing FTS / no lessons => agents still run (no crash, no behavior break).
- OBSERVABLE: retrieval is logged as a tool call.
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/post_trade_reviews",
                "knowledge/strategies", "knowledge/dreaming_logs", "playbooks"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


# --- retrieval correctness + quality gate ---

def test_retrieve_only_high_confidence_active(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons

    create_research_note("High conf funding lesson",
                         "Extreme perp funding signals crowded positioning; reduce exposure.",
                         confidence="high_confidence")
    create_research_note("Medium conf note",
                         "Some speculative funding and exposure musing.",
                         confidence="medium_confidence")
    create_research_note("Speculative note",
                         "Maybe funding matters for exposure sometimes.",
                         confidence="speculative")

    res = retrieve_lessons("funding exposure positioning risk", k=5)
    assert len(res) == 1
    assert res[0].confidence == "high_confidence"
    reset_db()


def test_archived_item_not_recalled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note, archive_item
    from aletheia.knowledge.retrieval import retrieve_lessons

    item = create_research_note("Drawdown lesson",
                                "Tighten stops after three consecutive losses to limit drawdown.",
                                confidence="high_confidence")
    assert len(retrieve_lessons("drawdown stops losses", k=5)) == 1
    archive_item(item.id)
    assert len(retrieve_lessons("drawdown stops losses", k=5)) == 0
    reset_db()


def test_memory_block_has_provenance(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block

    create_research_note("Volatility lesson",
                         "Volatility expands after contraction; size down into squeezes.",
                         confidence="high_confidence")
    block = format_memory_block(retrieve_lessons("volatility squeeze size", k=3))
    assert "ADVISORY" in block
    assert "not instructions" in block.lower()
    assert "research_note" in block
    assert "high_confidence" in block


def test_empty_query_and_empty_index_return_nothing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.retrieval import retrieve_lessons, format_memory_block
    assert retrieve_lessons("", k=3) == []
    assert retrieve_lessons("anything at all", k=3) == []  # empty index
    assert format_memory_block([]) == ""
    reset_db()


def test_reindex_backfills_existing(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons, reindex_all
    import aletheia.storage.sqlite as sq

    create_research_note("Trend lesson",
                         "In a confirmed uptrend, buy pullbacks to the moving average.",
                         confidence="high_confidence")
    # Wipe the FTS index to simulate pre-existing data written before the loop existed.
    sq.get_db().execute("DELETE FROM knowledge_fts")
    sq.get_db().commit()
    assert len(retrieve_lessons("uptrend pullback moving average", k=5)) == 0
    n = reindex_all()
    assert n >= 1
    assert len(retrieve_lessons("uptrend pullback moving average", k=5)) == 1
    reset_db()


# --- playbooks (read-only) ---

def test_load_playbooks_reads_seeds(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.trading.playbooks import seed_playbooks, load_playbooks, format_playbook_block
    seed_playbooks(tmp_path)
    pbs = load_playbooks()
    assert len(pbs) == 5
    block = format_playbook_block(pbs)
    assert "Playbooks" in block
    assert "read-only" in block.lower()
    reset_db()


# --- injection: observable + degrades safely ---

async def test_observer_logs_retrieval_and_survives_offline_llm(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Research"; save_config(cfg)

    from aletheia.knowledge.manager import create_research_note
    create_research_note("BTC regime lesson",
                         "When BTC loses the 200-day, treat rallies as distribution.",
                         confidence="high_confidence")
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000.0))

    # LLM is offline in tests → observer must still complete (injection is prompt text).
    result = await run_agent("market_observer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    calls = repo.list_tool_calls(run_id=result["run_id"])
    assert any(c["tool_name"] == "retrieve_lessons" for c in calls), "retrieval must be observable"
    reset_db()


async def test_designer_injects_and_survives_offline_llm(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Research"; save_config(cfg)
    from aletheia.trading.playbooks import seed_playbooks
    seed_playbooks(tmp_path)

    result = await run_agent("strategy_designer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")
    # Strategy still created despite offline LLM (graceful default spec).
    assert repo.list_strategies(limit=10)
    calls = repo.list_tool_calls(run_id=result["run_id"])
    names = {c["tool_name"] for c in calls}
    assert "retrieve_lessons" in names
    assert "load_playbooks" in names
    reset_db()
