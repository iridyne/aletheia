from __future__ import annotations

import json
import sqlite3

from typer.testing import CliRunner

import aletheia.storage.repositories as repositories
from aletheia.cli.inspect_cmd import inspect_app


def test_snapshot_coverage_groups_before_applying_limit(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE market_snapshots (symbol TEXT, snapshot_at TEXT, price REAL)")
    db.executemany(
        "INSERT INTO market_snapshots (symbol, snapshot_at, price) VALUES (?, ?, ?)",
        [("A/USDT", f"2026-08-28T00:{minute // 60:02d}:{minute % 60:02d}+00:00", minute) for minute in range(501)]
        + [("B/USDT", "2026-08-27T00:00:00+00:00", 1)],
    )
    db.commit()
    monkeypatch.setattr(repositories, "get_db", lambda: db)

    latest = repositories.list_market_snapshots(limit=None, latest_per_symbol=True)
    assert [(row["symbol"], row["price"]) for row in latest] == [("A/USDT", 500), ("B/USDT", 1)]

    result = CliRunner().invoke(inspect_app, ["snapshots", "--limit", "1", "--json"])
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)) == 1
