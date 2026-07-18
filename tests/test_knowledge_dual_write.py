"""Dual-write knowledge writers — FTS survives a lost Markdown file.

Phase E1 changed the five high-confidence writers to persist their body into
``knowledge_items.content`` alongside the disk Markdown. This test locks the
resulting invariant: if the Markdown file is later deleted (migration, backup
rollover, cross-machine move), ``reindex_all`` still rebuilds the FTS row from
the DB body, and ``retrieve_lessons`` still returns the item.

Before Phase E1 the same scenario silently dropped 611 out of 682 rows from the
index once ``data/knowledge/`` was cleaned; this test refuses to let that
regression return.
"""

from pathlib import Path

from tests.test_experience_loop import setup_env


def _delete_markdown_body(file_path: str) -> None:
    p = Path(file_path)
    assert p.is_file(), f"expected disk body at {file_path}"
    p.unlink()
    assert not p.exists()


def test_reindex_survives_disk_body_loss(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import (
        create_research_note,
        create_dreaming_log,
        create_daily_review,
        create_trading_guard,
    )
    from aletheia.knowledge.retrieval import reindex_all, retrieve_lessons
    from aletheia.storage.sqlite import get_db

    body = (
        "Momentum long on TRUMP/USDT after breakout: 15m confirmation, 1h uptrend, "
        "OI delta +1.5 stddev. Structure valid until 4h swing low invalidates."
    )
    research = create_research_note(
        title="Momentum long on TRUMP/USDT", content=body, confidence="high_confidence"
    )
    dream = create_dreaming_log(title="Overnight review 2026-07-18", content=body)
    review = create_daily_review(title="Daily review 2026-07-18", content=body)
    guard = create_trading_guard(
        title="Avoid stacked shorts into thin books", content=body
    )
    assert research and dream and review and guard, "writers must succeed"

    # Precondition: DB content was populated (the dual-write, not just file_path).
    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) FROM knowledge_items "
        "WHERE confidence='high_confidence' AND COALESCE(TRIM(content), '') != ''"
    ).fetchone()
    assert row[0] >= 4, "dual-write should populate content on every high_confidence writer"

    # Nuke every disk body. This simulates the exact failure mode that produced
    # the 10% FTS coverage: writer left file_path in DB, file was later removed.
    for item in (research, dream, review, guard):
        _delete_markdown_body(item.file_path)

    # Reindex from DB content. Expect a full rebuild, not the pre-E1 "0 indexed".
    count = reindex_all()
    assert count >= 4, f"reindex should rebuild from DB content, got {count}"

    hits = retrieve_lessons("TRUMP momentum long breakout", k=10)
    titles = {h.title for h in hits}
    assert "Momentum long on TRUMP/USDT" in titles, (
        "high_confidence body must remain retrievable even after disk loss"
    )
