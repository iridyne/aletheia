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

    con, _ = _make_db()
    monkeypatch.setattr(repo, "get_db", lambda: con)

    # An explicit limit still bounds the final result (safety cap), applied to
    # the whole window in ascending order, not as an inner "most recent" cut.
    capped = repo.list_account_equity_snapshots(
        execution_mode="exchange_account", days=7, limit=10
    )
    assert len(capped) == 10
    times = [r["snapshot_at"] for r in capped]
    assert times == sorted(times)
