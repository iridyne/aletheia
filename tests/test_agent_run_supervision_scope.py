from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from aletheia.storage import repositories as repo


def _agent_runs_db(monkeypatch) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE agent_runs ("
        "id TEXT PRIMARY KEY, agent_name TEXT NOT NULL, started_at TEXT NOT NULL, "
        "ended_at TEXT, status TEXT NOT NULL)"
    )
    monkeypatch.setattr(repo, "get_db", lambda: db)
    return db


def test_supervision_queries_are_not_capped_by_unrelated_agent_runs(monkeypatch):
    db = _agent_runs_db(monkeypatch)
    now = datetime.now(timezone.utc)
    observer_at = (now - timedelta(minutes=20)).isoformat()
    db.execute(
        "INSERT INTO agent_runs VALUES (?,?,?,?,?)",
        ("observer", "market_observer", observer_at, observer_at, "completed"),
    )
    db.executemany(
        "INSERT INTO agent_runs VALUES (?,?,?,?,?)",
        [
            (
                f"noise-{index}",
                "research_agent",
                (now - timedelta(minutes=index / 10)).isoformat(),
                (now - timedelta(minutes=index / 10)).isoformat(),
                "completed",
            )
            for index in range(51)
        ],
    )

    window = repo.list_agent_runs_window(hours=24)
    latest = repo.list_latest_agent_runs()

    assert len(window) == 52
    assert {row["id"] for row in latest} == {"observer", "noise-0"}


def test_latest_agent_run_uses_rowid_as_equal_timestamp_tiebreaker(monkeypatch):
    db = _agent_runs_db(monkeypatch)
    timestamp = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO agent_runs VALUES (?,?,?,?,?)",
        ("older-row", "market_observer", timestamp, timestamp, "failed"),
    )
    db.execute(
        "INSERT INTO agent_runs VALUES (?,?,?,?,?)",
        ("newer-row", "market_observer", timestamp, timestamp, "completed"),
    )

    assert repo.list_latest_agent_runs()[0]["id"] == "newer-row"
