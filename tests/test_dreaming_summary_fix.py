"""Regression test for #267 — dreaming summary excludes archived/deprecated items.

Before the fix, GET /api/dreaming/summary returned archived and deprecated
knowledge items in the recent_* collections while also listing them in
deprecated_memories, producing a contradictory response.

The fix adds (status = 'active' OR status IS NULL) to each recent_* query.
"""

from __future__ import annotations

import sqlite3

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_dreaming_summary_excludes_archived_from_recent(client):
    """#267: archived/deprecated recent items must not appear in recent_* collections."""
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import insert_knowledge_item, update_knowledge_item

    # Insert an ACTIVE research note created very recently
    active_note = KnowledgeItem(
        item_type="research_note",
        title="active note",
        status="active",
    )
    insert_knowledge_item(active_note)

    # Insert an ARCHIVED research note also created recently
    archived_note = KnowledgeItem(
        item_type="research_note",
        title="archived note",
        status="active",
    )
    insert_knowledge_item(archived_note)
    update_knowledge_item(archived_note.id, status="archived")

    # Insert a DEPRECATED strategy memory created recently
    deprecated_strat = KnowledgeItem(
        item_type="strategy_memory",
        title="deprecated strategy",
        status="active",
    )
    insert_knowledge_item(deprecated_strat)
    update_knowledge_item(deprecated_strat.id, status="deprecated")

    resp = await client.get("/api/dreaming/summary")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    research_titles = {item["title"] for item in data["recent_research_notes"]}
    hypotheses_titles = {item["title"] for item in data["recent_strategy_hypotheses"]}
    deprecated_titles = {item["title"] for item in data["deprecated_memories"]}

    # Active note must appear in recent_research_notes
    assert "active note" in research_titles, (
        f"active note must be in recent_research_notes; got {research_titles}"
    )
    # Archived note must NOT appear in recent_research_notes
    assert "archived note" not in research_titles, (
        f"archived note must not be in recent_research_notes; got {research_titles}"
    )
    # Archived note MUST appear in deprecated_memories
    assert "archived note" in deprecated_titles, (
        f"archived note must be in deprecated_memories; got {deprecated_titles}"
    )
    # Deprecated strategy must NOT appear in recent_strategy_hypotheses
    assert "deprecated strategy" not in hypotheses_titles, (
        f"deprecated strategy must not be in recent_strategy_hypotheses; got {hypotheses_titles}"
    )
    # Deprecated strategy MUST appear in deprecated_memories
    assert "deprecated strategy" in deprecated_titles, (
        f"deprecated strategy must be in deprecated_memories; got {deprecated_titles}"
    )
