from datetime import datetime, timedelta, timezone

from scripts.position_path_storage_baseline import (
    _recovery_backlog,
    _weighted_percentile,
)


def test_weighted_percentile_sorts_dbstat_cells_before_accumulating():
    values = [(30.0, 1), (10.0, 1), (20.0, 1)]

    assert _weighted_percentile(values, 0.50) == 20.0
    assert _weighted_percentile(values, 0.95) == 29.0


def test_recovery_split_uses_bucket_age_not_discovery_queue_age():
    now = datetime(2026, 8, 15, 12, tzinfo=timezone.utc)
    rows = [
        {
            "bucket_open_ms": int(
                (now - timedelta(hours=1)).timestamp() * 1000
            ),
            "first_deferred_at": now.isoformat(),
        },
        {
            "bucket_open_ms": int(
                (now - timedelta(days=4)).timestamp() * 1000
            ),
            "first_deferred_at": now.isoformat(),
        },
    ]

    result = _recovery_backlog(rows, now=now)

    assert result["hot_bucket_age_lte_72h"] == 1
    assert result["cold_bucket_age_gt_72h"] == 1
    assert result["oldest_hot_bucket_open_at"] == (
        now - timedelta(hours=1)
    ).isoformat()
    assert result["oldest_cold_bucket_open_at"] == (
        now - timedelta(days=4)
    ).isoformat()
