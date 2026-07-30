"""Regression test for the Account Equity range window.

Issue: list_account_equity_snapshots wrapped the `days` window in a
sub-select ordered DESC with `LIMIT ?`, which truncated EVERY range
(7D/30D/90D/All) to the most-recent `limit` rows and made them all
render identically on the dashboard chart.

This test inserts more rows than the old default limit inside a 7-day
window and asserts the function returns the WHOLE window (the route now
calls it with limit=None), not a truncated tail, and that the `days`
selector actually narrows the range.
"""

import sqlite3
from datetime import datetime, timedelta, timezone


def _make_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE portfolio_snapshots (
            id INTEGER PRIMARY KEY,
            execution_mode TEXT,
            total_value_usdt REAL,
            account_equity_usdt REAL,
            snapshot_at TEXT
        )
        """
    )
    now = datetime.now(timezone.utc)
    rows = []
    rid = 1
    # 800 rows every 10 min -> spans ~5.5 days, all inside the 7-day window,
    # and more than the old default cap of 500.
    for i in range(800):
        ts = now - timedelta(minutes=10 * i)
        rows.append((rid, "exchange_account", 100000.0 + i, 100000.0 + i, ts.isoformat()))
        rid += 1
    # 200 older rows, 20 days ago, outside the 7-day window.
    for i in range(200):
        ts = now - timedelta(days=20, minutes=10 * i)
        rows.append((rid, "exchange_account", 90000.0 + i, 90000.0 + i, ts.isoformat()))
        rid += 1
    con.executemany(
        "INSERT INTO portfolio_snapshots "
        "(id, execution_mode, total_value_usdt, account_equity_usdt, snapshot_at) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    return con, now


def test_equity_range_window_not_truncated(monkeypatch):
    import aletheia.storage.repositories as repo

    con, now = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    # Route default is limit=None -> the full window must be returned, not
    # capped at the old 500-row inner truncation.
    seven = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=7, limit=None
    )
    assert len(seven) == 800, f"7D window truncated to {len(seven)} (expected 800)"

    # The window must start near 7 days back (oldest rows), proving the
    # previous "most-recent 500" inner truncation is gone.
    first = datetime.fromisoformat(seven[0]["snapshot_at"])
    assert (now - first).days >= 5, "7D window does not reach its oldest edge"

    # The `days` selector actually narrows the range.
    one = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=1, limit=None
    )
    assert 0 < len(one) < len(seven), "days selector did not narrow the range"

    # No window returns everything (1000 rows).
    all_rows = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=None, limit=None
    )
    assert len(all_rows) == 1000

    # Results are in ascending time order.
    times = [r["snapshot_at"] for r in seven]
    assert times == sorted(times)


def test_equity_range_explicit_limit_is_safety_cap(monkeypatch):
    import aletheia.storage.repositories as repo

    con, now = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    # An explicit limit is a safety cap on the NEWEST rows inside the window,
    # not the oldest.  The 800 in-window rows are inserted newest-first (i=0 is
    # most recent); with limit=10 we must get the 10 most-recent, not the 10
    # oldest (which would be ~5.5 days old).
    capped = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=7, limit=10
    )
    assert len(capped) == 10
    times = [r["snapshot_at"] for r in capped]
    assert times == sorted(times)

    # The 10 rows must be the NEWEST 10 in the window (within ~100 min of now),
    # not the oldest (which would be ~5.5 days old).
    newest_ts = datetime.fromisoformat(capped[-1]["snapshot_at"])
    assert (now - newest_ts).total_seconds() < 6200, (
        f"safety cap returned oldest rows instead of newest: last row is "
        f"{(now - newest_ts).total_seconds() / 3600:.1f}h old"
    )


def test_equity_range_selectors_are_distinct(monkeypatch):
    """7D and 30D selectors must not collapse to the same window."""
    import aletheia.storage.repositories as repo

    con, _ = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    seven = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=7, limit=None
    )
    thirty = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=30, limit=None
    )
    # 30D includes the 200 old rows that are outside the 7D window.
    assert len(thirty) > len(seven), (
        f"30D ({len(thirty)}) must include more rows than 7D ({len(seven)})"
    )
    assert thirty[0]["snapshot_at"] < seven[0]["snapshot_at"], (
        "30D window must start earlier than 7D window"
    )


def test_portfolio_snapshots_range_not_truncated(monkeypatch):
    """list_portfolio_snapshots: days selector must not collapse when rows > old default limit."""
    import aletheia.storage.repositories as repo

    con, now = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    seven = repo.list_portfolio_snapshots(days=7, limit=None)
    assert len(seven) == 800, f"7D window truncated to {len(seven)} (expected 800)"
    first = datetime.fromisoformat(seven[0]["snapshot_at"])
    assert (now - first).days >= 5, "7D window does not reach its oldest edge"

    thirty = repo.list_portfolio_snapshots(days=30, limit=None)
    assert len(thirty) == 1000, f"30D+older window returned {len(thirty)} (expected 1000)"

    # Range selectors are distinct.
    assert len(thirty) > len(seven)
    assert thirty[0]["snapshot_at"] < seven[0]["snapshot_at"]


def test_portfolio_snapshots_no_days_uses_cap(monkeypatch):
    """Without days, list_portfolio_snapshots must not return the whole table."""
    import aletheia.storage.repositories as repo

    con, _ = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    # Default (limit=None) falls back to internal cap of 100.
    rows = repo.list_portfolio_snapshots()
    assert len(rows) == 100

    # Explicit limit is respected.
    rows = repo.list_portfolio_snapshots(limit=50)
    assert len(rows) == 50

    times = [r["snapshot_at"] for r in rows]
    assert times == sorted(times)


def test_equity_history_cache_key_includes_limit(monkeypatch):
    """Different limit values must not share a cache slot."""
    import time
    import threading
    import aletheia.storage.repositories as repo
    import aletheia.server.routes.data as data_routes

    con, _ = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    class _FakeConfig:
        class exchange:
            paper_execution_mode = "exchange_account"

    monkeypatch.setattr(data_routes, "load_config", lambda: _FakeConfig())
    monkeypatch.setattr(data_routes, "_ensure_db", lambda: None)
    monkeypatch.setattr(repo, "list_capital_events_between", lambda *_: [])
    monkeypatch.setattr(data_routes, "repo", repo)

    # Clear the cache before the test.
    data_routes.invalidate_account_equity_history_cache()

    # First call: no limit → populates cache slot (days=7, None, "exchange_account").
    r_full = data_routes.get_account_equity_history(days=7)
    full_count = len(r_full["points"])

    # Second call: limit=10 → must NOT hit the previous cache slot.
    r_capped = data_routes.get_account_equity_history(days=7, limit=10)
    capped_count = len(r_capped["points"])

    assert capped_count == 10, (
        f"limit=10 call returned {capped_count} points — likely hit wrong cache slot"
    )
    assert full_count > capped_count, "no-limit call must return more rows than capped call"
