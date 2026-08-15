#!/usr/bin/env python3
"""Measure Position-path SQLite storage from a read-only database copy.

Example::

    uv run python scripts/position_path_storage_baseline.py \
      --database /var/tmp/fedora-copy/app.db \
      --output position-path-storage-baseline.json

The script uses SQLite ``dbstat`` page allocations. It never opens the source
database for writing and does not infer bytes from Python object sizes.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Iterable


BUCKET_MS = 300_000
_AGE_BUCKETS = (
    ("<1h", 0, 60 * 60),
    ("1-6h", 60 * 60, 6 * 60 * 60),
    ("6-24h", 6 * 60 * 60, 24 * 60 * 60),
    ("1-3d", 24 * 60 * 60, 3 * 24 * 60 * 60),
    (">3d", 3 * 24 * 60 * 60, None),
)
_BASELINE_TABLES = (
    "position_path_observations",
    "position_path_observation_deferred_buckets",
)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    # Freeze all dbstat and logical facts to one read-only snapshot.  Without an
    # explicit read transaction, a live observer can make row counts and page
    # allocations describe different moments of the source database.
    connection.execute("BEGIN")
    return connection


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _parse_timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 6)


def _weighted_percentile(values: list[tuple[float, int]], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted((float(value), int(weight)) for value, weight in values)
    total = sum(weight for _, weight in ordered)
    if total <= 0:
        return None
    target = (total - 1) * quantile

    def value_at(rank: int) -> float:
        cursor = 0
        for value, weight in ordered:
            if cursor + weight > rank:
                return value
            cursor += weight
        return ordered[-1][0]

    lower_rank = int(target)
    upper_rank = min(total - 1, lower_rank + 1)
    lower = value_at(lower_rank)
    upper = value_at(upper_rank)
    return round(lower + (upper - lower) * (target - lower_rank), 6)


def _dbstat(db: sqlite3.Connection, names: Iterable[str] | None = None) -> list[dict[str, int | str]]:
    try:
        if names is None:
            rows = db.execute(
                "SELECT name,SUM(pgsize) AS bytes,SUM(payload) AS payload_bytes,"
                "SUM(unused) AS unused_bytes,SUM(ncell) AS cells,COUNT(*) AS pages "
                "FROM dbstat GROUP BY name"
            ).fetchall()
        else:
            names = tuple(sorted(set(names)))
            if not names:
                return []
            placeholders = ",".join("?" for _ in names)
            rows = db.execute(
                "SELECT name,SUM(pgsize) AS bytes,SUM(payload) AS payload_bytes,"
                "SUM(unused) AS unused_bytes,SUM(ncell) AS cells,COUNT(*) AS pages "
                f"FROM dbstat WHERE name IN ({placeholders}) GROUP BY name",
                names,
            ).fetchall()
    except sqlite3.Error:
        return []
    return [
        {
            "name": str(row["name"]),
            "bytes": int(row["bytes"]),
            "payload_bytes": int(row["payload_bytes"]),
            "unused_bytes": int(row["unused_bytes"]),
            "cells": int(row["cells"]),
            "pages": int(row["pages"]),
        }
        for row in rows
    ]


def _objects_for_table(db: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(db, table):
        return []
    escaped_table = table.replace("'", "''")
    names = [table]
    names.extend(
        str(row[1])
        for row in db.execute(f"PRAGMA index_list('{escaped_table}')").fetchall()
        if row[1]
    )
    names.extend(
        str(row[0])
        for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name=?",
            (table,),
        ).fetchall()
        if row[0]
    )
    return list(dict.fromkeys(names))


def _profile(db: sqlite3.Connection, table: str) -> dict[str, Any]:
    objects = _dbstat(db, _objects_for_table(db, table))
    rows = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if _table_exists(db, table) else 0
    table_objects = [item for item in objects if str(item["name"]) == table]
    index_objects = [item for item in objects if str(item["name"]) != table]
    return {
        "available": bool(objects or _table_exists(db, table)),
        "rows": rows,
        "objects": objects,
        "bytes": sum(int(item["bytes"]) for item in objects),
        "table_bytes": sum(int(item["bytes"]) for item in table_objects),
        "index_bytes": sum(int(item["bytes"]) for item in index_objects),
        "table_payload_bytes": sum(int(item["payload_bytes"]) for item in table_objects),
        "index_payload_bytes": sum(int(item["payload_bytes"]) for item in index_objects),
        "table_unused_bytes": sum(int(item["unused_bytes"]) for item in table_objects),
        "index_unused_bytes": sum(int(item["unused_bytes"]) for item in index_objects),
    }


def _bytes_per_bucket(db: sqlite3.Connection, table: str, row_count: int) -> dict[str, Any]:
    weighted: list[tuple[float, int]] = []
    for item in _dbstat(db, _objects_for_table(db, table)):
        cells = int(item["cells"])
        if cells:
            weighted.append((int(item["bytes"]) / cells, cells))
    return {
        "method": "dbstat_page_cell_allocation",
        "row_count": row_count,
        "p50": _weighted_percentile(weighted, 0.50),
        "p95": _weighted_percentile(weighted, 0.95),
        "max": round(max((value for value, _ in weighted), default=0.0), 6) if weighted else None,
        "status": "available" if weighted else "not_available",
    }


def _first_full_bucket(opened_at: object) -> int | None:
    parsed = _parse_timestamp(opened_at)
    if parsed is None:
        return None
    opened_ms = math.ceil(parsed.timestamp() * 1000)
    return ((opened_ms + BUCKET_MS - 1) // BUCKET_MS) * BUCKET_MS


def _expected_bounds(position: sqlite3.Row) -> tuple[int | None, int | None]:
    if str(position["status"] or "").lower() != "closed":
        return None, None
    first = _first_full_bucket(position["opened_at"])
    closed = _parse_timestamp(position["closed_at"])
    if first is None or closed is None:
        return None, None
    last = (int(closed.timestamp() * 1000) // BUCKET_MS - 1) * BUCKET_MS
    return first, last


def _deferred_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    if not _table_exists(db, "position_path_observation_deferred_buckets"):
        return []
    return db.execute(
        "SELECT position_id,bucket_open_ms,first_deferred_at,last_attempt_at,"
        "attempt_count,reason FROM position_path_observation_deferred_buckets "
        "ORDER BY first_deferred_at,position_id,bucket_open_ms"
    ).fetchall()


def _recovery_backlog(
    deferred_rows: Iterable[sqlite3.Row], *, now: datetime
) -> dict[str, Any]:
    """Split recovery by bucket age, keeping queue age as a separate fact."""

    hot_count = 0
    cold_count = 0
    invalid_bucket_timestamps = 0
    oldest_hot_bucket: datetime | None = None
    oldest_cold_bucket: datetime | None = None
    bucket_age_counts = Counter()
    for row in deferred_rows:
        try:
            bucket = datetime.fromtimestamp(
                int(row["bucket_open_ms"]) / 1000,
                timezone.utc,
            )
        except (KeyError, TypeError, ValueError, OSError, OverflowError):
            invalid_bucket_timestamps += 1
            continue
        age = max(0.0, (now - bucket).total_seconds())
        for name, lower, upper in _AGE_BUCKETS:
            if age >= lower and (upper is None or age < upper):
                bucket_age_counts[name] += 1
                break
        if age <= 72 * 60 * 60:
            hot_count += 1
            oldest_hot_bucket = (
                bucket if oldest_hot_bucket is None else min(oldest_hot_bucket, bucket)
            )
        else:
            cold_count += 1
            oldest_cold_bucket = (
                bucket if oldest_cold_bucket is None else min(oldest_cold_bucket, bucket)
            )
    return {
        "hot_bucket_age_lte_72h": hot_count,
        "cold_bucket_age_gt_72h": cold_count,
        "oldest_hot_bucket_open_at": (
            oldest_hot_bucket.isoformat() if oldest_hot_bucket else None
        ),
        "oldest_cold_bucket_open_at": (
            oldest_cold_bucket.isoformat() if oldest_cold_bucket else None
        ),
        "bucket_age_buckets": {
            name: bucket_age_counts[name] for name, _, _ in _AGE_BUCKETS
        },
        "invalid_bucket_timestamps": invalid_bucket_timestamps,
    }


def _eligible_closed_positions(db: sqlite3.Connection) -> int:
    if not _table_exists(db, "positions") or not _table_exists(db, "position_path_observations"):
        return 0
    deferred = {str(row[0]) for row in db.execute(
        "SELECT DISTINCT position_id FROM position_path_observation_deferred_buckets"
    ).fetchall()} if _table_exists(db, "position_path_observation_deferred_buckets") else set()
    eligible = 0
    for position in db.execute(
        "SELECT id,status,opened_at,closed_at FROM positions WHERE lower(status)='closed'"
    ).fetchall():
        first, last = _expected_bounds(position)
        if first is None or last is None or last < first or str(position["id"]) in deferred:
            continue
        expected = (last - first) // BUCKET_MS + 1
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(bucket_open_ms>=? AND bucket_open_ms<=?) AS in_range, "
            "SUM(bucket_open_ms>=? AND bucket_open_ms<=? "
            "AND evidence_status IN ('available','unavailable')) AS terminal "
            "FROM position_path_observations WHERE position_id=?",
            (first, last, first, last, str(position["id"])),
        ).fetchone()
        if (
            row
            and int(row["total"] or 0) == expected
            and int(row["in_range"] or 0) == expected
            and int(row["terminal"] or 0) == expected
        ):
            eligible += 1
    return eligible


def _duplicate_bars(db: sqlite3.Connection) -> dict[str, int | float]:
    if not _table_exists(db, "position_path_observations"):
        return {"groups": 0, "rows": 0, "available_rows": 0, "percentage": 0.0}
    available = int(db.execute(
        "SELECT COUNT(*) FROM position_path_observations WHERE evidence_status='available'"
    ).fetchone()[0])
    groups = db.execute(
        "SELECT venue,symbol,market_type,bucket_open_ms,bar_open,bar_high,bar_low,bar_close,"
        "bar_volume,bar_provider,COUNT(*) AS count FROM position_path_observations "
        "WHERE evidence_status='available' GROUP BY venue,symbol,market_type,bucket_open_ms,"
        "bar_open,bar_high,bar_low,bar_close,bar_volume,bar_provider HAVING COUNT(*)>1"
    ).fetchall()
    duplicate_rows = sum(int(row["count"]) - 1 for row in groups)
    return {
        "groups": len(groups),
        "rows": duplicate_rows,
        "available_rows": available,
        "percentage": round(duplicate_rows / max(1, available) * 100.0, 6),
    }


def _field_cardinality(db: sqlite3.Connection) -> dict[str, dict[str, int | float]]:
    if not _table_exists(db, "position_path_observations"):
        return {}
    columns = [str(row[1]) for row in db.execute("PRAGMA table_info(position_path_observations)").fetchall()]
    total = int(db.execute("SELECT COUNT(*) FROM position_path_observations").fetchone()[0])
    result: dict[str, dict[str, int | float]] = {}
    for column in columns:
        row = db.execute(
            f"SELECT COUNT(DISTINCT {column}) AS distinct_count, "
            f"COUNT(*)-COUNT({column}) AS null_count FROM position_path_observations"
        ).fetchone()
        distinct_count = int(row["distinct_count"] or 0)
        null_count = int(row["null_count"] or 0)
        repeated = max(0, total - null_count - distinct_count)
        result[column] = {
            "distinct_non_null": distinct_count,
            "null_count": null_count,
            "repeated_non_null": repeated,
            "repeated_percentage": round(repeated / max(1, total) * 100.0, 6),
        }
    return result


def _database_files(path: Path) -> dict[str, int | str | None]:
    database_bytes = path.stat().st_size
    wal_path = Path(f"{path}-wal")
    shm_path = Path(f"{path}-shm")
    wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
    shm_bytes = shm_path.stat().st_size if shm_path.exists() else 0
    return {
        "database_path": str(path),
        "database_bytes": database_bytes,
        "wal_bytes": wal_bytes,
        "shm_bytes": shm_bytes,
        "database_plus_wal_bytes": database_bytes + wal_bytes,
    }


def benchmark(path: Path) -> dict[str, Any]:
    db = _connect_readonly(path)
    try:
        hot = _profile(db, "position_path_observations")
        deferred = _profile(db, "position_path_observation_deferred_buckets")
        archive = _profile(db, "position_path_archive_segments")
        database = _database_files(path)
        rows_per_position = [
            int(row["count"])
            for row in db.execute(
                "SELECT position_id,COUNT(*) AS count FROM position_path_observations GROUP BY position_id"
            ).fetchall()
        ] if _table_exists(db, "position_path_observations") else []
        deferred_rows = _deferred_rows(db)
        now = datetime.now(timezone.utc)
        age_counts = Counter()
        invalid_timestamps = 0
        reasons = Counter(str(row["reason"]) for row in deferred_rows)
        deferred_positions = Counter(str(row["position_id"]) for row in deferred_rows)
        queue_oldest_hot: datetime | None = None
        queue_oldest_cold: datetime | None = None
        for row in deferred_rows:
            first = _parse_timestamp(row["first_deferred_at"])
            if first is None:
                invalid_timestamps += 1
                continue
            age = max(0.0, (now - first).total_seconds())
            for name, lower, upper in _AGE_BUCKETS:
                if age >= lower and (upper is None or age < upper):
                    age_counts[name] += 1
                    break
            if age <= 72 * 60 * 60:
                queue_oldest_hot = (
                    first if queue_oldest_hot is None else min(queue_oldest_hot, first)
                )
            else:
                queue_oldest_cold = (
                    first if queue_oldest_cold is None else min(queue_oldest_cold, first)
                )
        recovery_backlog = _recovery_backlog(deferred_rows, now=now)
        recovery_backlog["queue_age_lte_72h"] = sum(
            count for name, count in age_counts.items()
            if name in {"<1h", "1-6h", "6-24h", "1-3d"}
        )
        recovery_backlog["queue_age_gt_72h"] = sum(
            count for name, count in age_counts.items()
            if name == ">3d"
        )
        recovery_backlog["oldest_queue_hot_deferred_at"] = (
            queue_oldest_hot.isoformat() if queue_oldest_hot else None
        )
        recovery_backlog["oldest_queue_cold_deferred_at"] = (
            queue_oldest_cold.isoformat() if queue_oldest_cold else None
        )
        hot_objects = hot["objects"] + deferred["objects"]
        hot_bytes = int(hot["bytes"]) + int(deferred["bytes"])
        report = {
            "contract_version": "position_path_storage_baseline/v3",
            "captured_at": now.isoformat(),
            "database": database,
            "read_only": True,
            "physical_measurement": "sqlite_dbstat",
            "sqlite": {
                "page_size": int(db.execute("PRAGMA page_size").fetchone()[0]),
                "page_count": int(db.execute("PRAGMA page_count").fetchone()[0]),
                "freelist_count": int(db.execute("PRAGMA freelist_count").fetchone()[0]),
                "allocated_bytes": int(db.execute("PRAGMA page_size").fetchone()[0]) * int(db.execute("PRAGMA page_count").fetchone()[0]),
                "free_bytes": int(db.execute("PRAGMA page_size").fetchone()[0]) * int(db.execute("PRAGMA freelist_count").fetchone()[0]),
            },
            "position_path": {
                "total_rows": int(hot["rows"]),
                "eligible_positions": _eligible_closed_positions(db),
                "rows_per_position": {
                    "count": len(rows_per_position),
                    "p50": _percentile(rows_per_position, 0.50),
                    "p95": _percentile(rows_per_position, 0.95),
                    "max": max(rows_per_position, default=0),
                    "distribution": dict(Counter(rows_per_position)),
                },
                "table_bytes": int(hot["table_bytes"]),
                "index_bytes": int(hot["index_bytes"]),
                "payload_bytes": int(hot["table_payload_bytes"]),
                "unused_bytes": int(hot["table_unused_bytes"]),
                "table_index_bytes": int(hot["bytes"]),
                "table_index_payload_bytes": int(
                    hot["table_payload_bytes"] + hot["index_payload_bytes"]
                ),
                "table_index_unused_bytes": int(
                    hot["table_unused_bytes"] + hot["index_unused_bytes"]
                ),
                "deferred_table": deferred,
                "archive_table": archive,
                "hot_table_index_bytes_including_deferred": hot_bytes,
                "percentage_of_app_db": round(hot_bytes / max(1, int(database["database_bytes"] or 0)) * 100.0, 6),
                "bytes_per_bucket": _bytes_per_bucket(db, "position_path_observations", int(hot["rows"])),
                "duplicate_exact_market_bars": _duplicate_bars(db),
                "constant_repeated_field_cardinality": _field_cardinality(db),
                "deferred_bucket_rows": len(deferred_rows),
                "deferred_positions": len(deferred_positions),
                "deferred_age_buckets": {name: age_counts[name] for name, _, _ in _AGE_BUCKETS},
                "deferred_reason_counts": dict(sorted(reasons.items())),
                "deferred_position_counts": dict(deferred_positions.most_common()),
                "invalid_first_deferred_timestamps": invalid_timestamps,
                "recovery_backlog": {
                    **recovery_backlog,
                },
                "dbstat_objects": hot_objects,
            },
            "notes": [
                "Per-bucket percentiles are weighted dbstat page-cell allocation; dbstat does not expose row-level page ownership.",
                "Archive bytes are reported separately and are not counted as the current hot baseline.",
                "Compression does not terminalize or clear deferred historical recovery work.",
            ],
        }
        return report
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", dest="compact")
    args = parser.parse_args()
    rendered = json.dumps(
        benchmark(args.database),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if args.compact else None,
        indent=None if args.compact else 2,
    ) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
