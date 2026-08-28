from __future__ import annotations

import json
import sqlite3

import aletheia.storage.repositories as repositories
from aletheia.cli.inspect_cmd import liquidations


def test_liquidations_searches_all_active_members(monkeypatch, capsys):
    from aletheia.storage import repositories as repo

    member_limits: list[int | None] = []
    monkeypatch.setattr(
        repo,
        "get_active_universe_members",
        lambda limit=20: member_limits.append(limit) or [{"symbol": "LOW/USDT"}],
    )
    monkeypatch.setattr(
        repo,
        "recent_liquidations",
        lambda symbol, hours=4: [{"symbol": symbol, "event_time": "2026-08-28T00:00:00+00:00"}],
    )

    liquidations(symbol=None, hours=4, limit=30, json_output=True)

    assert member_limits == [None]
    assert json.loads(capsys.readouterr().out)[0]["symbol"] == "LOW/USDT"


def test_list_universe_members_none_returns_the_full_snapshot(monkeypatch):
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE universe_members (snapshot_id TEXT, symbol TEXT, rank INTEGER)")
    db.executemany(
        "INSERT INTO universe_members (snapshot_id, symbol, rank) VALUES (?, ?, ?)",
        [("snapshot-1", f"S{rank}/USDT", rank) for rank in range(1, 22)],
    )
    db.commit()
    monkeypatch.setattr(repositories, "get_db", lambda: db)

    rows = repositories.list_universe_members("snapshot-1", limit=None)

    assert len(rows) == 21
    assert rows[-1]["rank"] == 21
