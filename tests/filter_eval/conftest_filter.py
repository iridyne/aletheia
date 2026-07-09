from __future__ import annotations
import sqlite3
import pytest
from pathlib import Path


@pytest.fixture
def mock_proposals_db(tmp_path, monkeypatch):
    """
    Returns an inject(scenario) callable.
    Call it at the start of each test with the scenario to load — it inserts
    scenario.history into a temporary SQLite and patches get_db so the filter
    function reads from it.
    """
    db_path = tmp_path / "test_proposals.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE trade_proposals (
            id TEXT PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            created_at TEXT,
            source TEXT DEFAULT 'native',
            execution_mode TEXT DEFAULT 'paper_internal',
            status TEXT DEFAULT 'executed'
        )
    """)
    conn.commit()

    def inject(scenario):
        # Clear previous data
        conn.execute("DELETE FROM trade_proposals")
        for i, p in enumerate(scenario.history):
            conn.execute(
                "INSERT INTO trade_proposals (id, symbol, direction, created_at, source) VALUES (?,?,?,?,?)",
                (f"hist_{i}", p.symbol, p.direction, p.created_at, p.source),
            )
        conn.commit()

        class _FakeDB:
            def execute(self, sql, params=()):
                return conn.execute(sql, params)

        monkeypatch.setattr(
            "aletheia.storage.sqlite.get_db",
            lambda: _FakeDB(),
        )

    return inject
