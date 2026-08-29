"""Regression tests for bounded prediction probability history."""

from aletheia.storage import repositories as repo


def test_probability_history_uses_latest_bounded_window_in_chronological_order():
    db = repo.get_db()
    db.executemany(
        "INSERT INTO prediction_probability_snapshots "
        "(id, market_id, ts, yes_probability) VALUES (?, ?, ?, ?)",
        [
            ("snapshot-old", "market-1", "2024-01-01T00:00:00+00:00", 0.1),
            ("snapshot-middle", "market-1", "2024-01-02T00:00:00+00:00", 0.2),
            ("snapshot-new", "market-1", "2024-01-03T00:00:00+00:00", 0.3),
        ],
    )
    db.commit()

    snapshots = repo.list_probability_snapshots("market-1", limit=2)

    assert [snapshot["id"] for snapshot in snapshots] == ["snapshot-middle", "snapshot-new"]
    assert [snapshot["ts"] for snapshot in snapshots] == [
        "2024-01-02T00:00:00+00:00",
        "2024-01-03T00:00:00+00:00",
    ]
