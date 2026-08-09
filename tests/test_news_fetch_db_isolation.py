"""Regression test for the news_fetch "Cannot operate on a closed database" bug.

Root cause: the persistent async timeout worker runs multiple jobs on one shared
thread/event-loop. A sibling job's `close_db()` closes the thread-local SQLite
connection. `fetch_news_feeds` previously cached that connection at the top of the
coroutine (before an `await`), so after the await the cached handle was closed and
every subsequent `db.execute` raised "Cannot operate on a closed database".

The fix re-acquires the connection per entry (after every await boundary), so a
closed connection is simply recreated on the next `get_db()` call.
"""

import pytest


def test_fetch_news_feeds_reacquires_connection_after_sibling_close_db(
    monkeypatch, tmp_path
):
    from aletheia.agent_runtime import scheduler
    from aletheia.config import loader
    from aletheia.data import news_collector
    from aletheia.storage import sqlite
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import close_db, get_db

    # Isolate the database into a temp dir.
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")

    # Build the schema (incl. news_items) in the isolated DB. get_db() alone
    # does NOT create tables — migrations do. Mirror tests/test_news_integrity.py.
    sqlite.reset_db()
    run_migrations()

    # Skip the unrelated learning/opportunity-policy epoch dependency.
    monkeypatch.setattr(
        "aletheia.learning.opportunity_information_policy."
        "ensure_opportunity_information_policy_epoch",
        lambda: None,
    )

    class _Feed:
        url = "http://feed"
        name = "t"
        source = "s"
        enabled = True

    class _News:
        enabled = True
        feeds = [_Feed()]

    class _Cfg:
        news = _News()

    monkeypatch.setattr(loader, "load_config", lambda: _Cfg())

    entries = [
        {"id": "g1", "title": "BTC up", "link": "http://x/1", "summary": "BTC rose"},
        {"id": "g2", "title": "ETH down", "link": "http://x/2", "summary": "ETH fell"},
    ]
    monkeypatch.setattr(news_collector, "_fetch_feed_sync", lambda url: entries)

    # Spy on get_db: on the 2nd call (i.e. while processing the 2nd entry),
    # simulate a sibling async job finishing and closing the shared
    # thread-local connection via close_db(). This mimics the production bug
    # trigger: a sibling job's close_db() clears the thread-local connection
    # that this coroutine would otherwise have reused. The fixed code
    # re-acquires a live connection on every get_db(), so the INSERT for the
    # 2nd entry still lands on a fresh handle.
    real_get_db = get_db
    state = {"calls": 0}

    def spy_get_db():
        conn = real_get_db()
        state["calls"] += 1
        if state["calls"] == 2:
            close_db()  # sibling job closed the shared connection
        return real_get_db() if state["calls"] == 2 else conn

    monkeypatch.setattr(news_collector, "get_db", spy_get_db)

    worker = scheduler._PersistentAsyncTimeoutWorker()
    try:
        future = worker.submit(news_collector.fetch_news_feeds())
        result = future.result(timeout=10)
    finally:
        worker.close()

    # The job must have completed without "Cannot operate on a closed database",
    # writing both entries despite the mid-stream connection teardown.
    assert result["new"] == 2, result
    # And it must re-acquire the connection per entry (not cache one handle).
    assert state["calls"] >= 2

    rows = real_get_db().execute("SELECT count(*) FROM news_items").fetchone()[0]
    assert rows == 2
