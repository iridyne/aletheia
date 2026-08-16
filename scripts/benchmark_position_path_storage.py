#!/usr/bin/env python3
"""Read-only Position-path storage baseline and codec benchmark.

Usage::

    uv run python scripts/benchmark_position_path_storage.py \
      --database /path/to/read-only-fedora-copy/app.db \
      --output position-path-storage-baseline.json

The SQLite footprint is taken from ``dbstat`` pages and cells.  The script
never opens the database in a writable mode and never estimates bytes from
Python object sizes.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import time
from typing import Any, Iterable

import msgpack
import zstandard


BUCKET_MS = 300_000
_OBSERVATION_TABLES = (
    "position_path_observations",
    "position_path_observation_deferred_buckets",
)


def _utc(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(len(ordered) - 1, lower + 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _weighted_percentile(values: list[tuple[float, int]], percentile: float) -> float | None:
    if not values:
        return None
    total = sum(weight for _, weight in values)
    if total <= 0:
        return None
    target = (total - 1) * percentile
    cursor = 0
    for value, weight in values:
        if cursor + weight > target:
            return float(value)
        cursor += weight
    return float(values[-1][0])


def _table_exists(db: sqlite3.Connection, name: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _dbstat(db: sqlite3.Connection, names: Iterable[str]) -> list[dict[str, int | str]]:
    names = tuple(sorted(set(names)))
    if not names:
        return []
    placeholders = ",".join("?" for _ in names)
    try:
        rows = db.execute(
            "SELECT name,pgsize,ncell,payload,unused FROM dbstat "
            f"WHERE aggregate=1 AND name IN ({placeholders})",
            names,
        ).fetchall()
    except sqlite3.Error:
        try:
            rows = db.execute(
                "SELECT name,SUM(pgsize),SUM(ncell),SUM(payload),SUM(unused) FROM dbstat "
                f"WHERE name IN ({placeholders}) GROUP BY name",
                names,
            ).fetchall()
        except sqlite3.Error:
            return []
    return [
        {
            "name": str(row[0]),
            "bytes": int(row[1]),
            "cells": int(row[2]),
            "payload_bytes": int(row[3]),
            "unused_bytes": int(row[4]),
        }
        for row in rows
    ]


def _objects_for_table(db: sqlite3.Connection, table: str) -> list[str]:
    return [
        table,
        *(
            str(row[0])
            for row in db.execute(
                "SELECT name FROM sqlite_schema WHERE type='index' AND tbl_name=?",
                (table,),
            ).fetchall()
        ),
    ]


def _physical_profile(db: sqlite3.Connection, table: str) -> dict[str, Any]:
    if not _table_exists(db, table):
        return {"available": False, "table": table}
    objects = _dbstat(db, _objects_for_table(db, table))
    total = sum(int(row["bytes"]) for row in objects)
    payload = sum(int(row["payload_bytes"]) for row in objects)
    unused = sum(int(row["unused_bytes"]) for row in objects)
    return {
        "available": True,
        "table": table,
        "rows": int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]),
        "objects": objects,
        "bytes": total,
        "payload_bytes": payload,
        "unused_bytes": unused,
        "index_bytes": sum(int(row["bytes"]) for row in objects if str(row["name"]) != table),
    }


def _bytes_per_bucket(db: sqlite3.Connection, table: str, row_count: int) -> dict[str, float | None | str]:
    if not row_count:
        return {"method": "dbstat_page_cell_allocation", "p50": None, "p95": None, "max": None}
    weighted: list[tuple[float, int]] = []
    for object_name in _objects_for_table(db, table):
        for row in _dbstat(db, (object_name,)):
            cells = int(row["cells"])
            if cells:
                weighted.append((int(row["bytes"]) / cells, cells))
    return {
        "method": "dbstat_page_cell_allocation",
        "p50": _weighted_percentile(weighted, 0.50),
        "p95": _weighted_percentile(weighted, 0.95),
        "max": max((value for value, _ in weighted), default=None),
    }


def _expected_bucket_count(position: dict[str, Any]) -> int | None:
    if str(position.get("status") or "").lower() != "closed":
        return None
    opened = _utc(position.get("opened_at"))
    closed = _utc(position.get("closed_at"))
    if opened is None or closed is None or closed <= opened:
        return None
    first = ((int(opened.timestamp() * 1000) + BUCKET_MS - 1) // BUCKET_MS) * BUCKET_MS
    last = (int(closed.timestamp() * 1000) // BUCKET_MS - 1) * BUCKET_MS
    return max(0, (last - first) // BUCKET_MS + 1)


def _logical_facts(db: sqlite3.Connection) -> dict[str, Any]:
    if not _table_exists(db, "position_path_observations"):
        return {"total_rows": 0, "eligible_positions": 0, "rows_per_position": {}}
    grouped = db.execute(
        "SELECT position_id,COUNT(*) AS count FROM position_path_observations "
        "GROUP BY position_id ORDER BY count"
    ).fetchall()
    rows_per_position = [int(row[1]) for row in grouped]
    positions = [dict(row) for row in db.execute("SELECT * FROM positions").fetchall()] if _table_exists(db, "positions") else []
    deferred = set()
    if _table_exists(db, "position_path_observation_deferred_buckets"):
        deferred = {
            str(row[0])
            for row in db.execute(
                "SELECT DISTINCT position_id FROM position_path_observation_deferred_buckets"
            ).fetchall()
        }
    eligible = 0
    for position in positions:
        expected = _expected_bucket_count(position)
        if expected is None or str(position.get("id")) in deferred:
            continue
        row = db.execute(
            "SELECT COUNT(*) AS count, "
            "SUM(evidence_status='available') AS available, "
            "SUM(evidence_status='unavailable') AS unavailable "
            "FROM position_path_observations WHERE position_id=?",
            (position.get("id"),),
        ).fetchone()
        if row is not None and int(row["count"] or 0) == expected and int(row["available"] or 0) + int(row["unavailable"] or 0) == expected:
            eligible += 1
    return {
        "total_rows": sum(rows_per_position),
        "total_positions_with_rows": len(rows_per_position),
        "eligible_positions": eligible,
        "rows_per_position": {
            "p50": _percentile(rows_per_position, 0.50),
            "p95": _percentile(rows_per_position, 0.95),
            "max": max(rows_per_position, default=0),
            "distribution": dict(Counter(rows_per_position)),
        },
    }


def _duplicate_bars(db: sqlite3.Connection) -> dict[str, int | float]:
    if not _table_exists(db, "position_path_observations"):
        return {"duplicate_groups": 0, "duplicate_rows": 0, "available_rows": 0, "duplicate_percentage": 0.0}
    available = int(db.execute(
        "SELECT COUNT(*) FROM position_path_observations WHERE evidence_status='available'"
    ).fetchone()[0])
    groups = db.execute(
        "SELECT venue,symbol,market_type,bucket_open_ms,bar_open,bar_high,bar_low,bar_close,"
        "bar_volume,bar_provider,COUNT(*) AS count "
        "FROM position_path_observations WHERE evidence_status='available' "
        "GROUP BY venue,symbol,market_type,bucket_open_ms,bar_open,bar_high,bar_low,bar_close,"
        "bar_volume,bar_provider HAVING COUNT(*) > 1"
    ).fetchall()
    duplicate_rows = sum(int(row[-1]) - 1 for row in groups)
    return {
        "duplicate_groups": len(groups),
        "duplicate_rows": duplicate_rows,
        "available_rows": available,
        "duplicate_percentage": round(duplicate_rows / max(1, available) * 100.0, 6),
    }


def _repeated_fields(db: sqlite3.Connection) -> dict[str, dict[str, int | float]]:
    if not _table_exists(db, "position_path_observations"):
        return {}
    columns = [str(row[1]) for row in db.execute("PRAGMA table_info(position_path_observations)").fetchall()]
    total = int(db.execute("SELECT COUNT(*) FROM position_path_observations").fetchone()[0])
    result: dict[str, dict[str, int | float]] = {}
    for column in columns:
        row = db.execute(
            f"SELECT COUNT(DISTINCT {column}),COUNT(*)-COUNT({column}) FROM position_path_observations"
        ).fetchone()
        distinct, nulls = int(row[0]), int(row[1])
        result[column] = {
            "distinct_non_null": distinct,
            "null_count": nulls,
            "repeated_non_null": max(0, total - nulls - distinct),
            "repeated_percentage": round(max(0, total - nulls - distinct) / max(1, total) * 100.0, 6),
        }
    return result


def _representative_rows(db: sqlite3.Connection) -> list[tuple[str, list[dict[str, Any]]]]:
    if not _table_exists(db, "position_path_observations"):
        return []
    ids = [str(row[0]) for row in db.execute(
        "SELECT position_id FROM position_path_observations GROUP BY position_id ORDER BY COUNT(*) ASC, position_id"
    ).fetchall()]
    grouped = {
        position_id: [dict(row) for row in db.execute(
            "SELECT * FROM position_path_observations WHERE position_id=? ORDER BY bucket_open_ms", (position_id,)
        ).fetchall()]
        for position_id in ids
    }
    chosen: list[tuple[str, list[dict[str, Any]]]] = []
    seen: set[str] = set()
    for position_id in ids:
        rows = grouped[position_id]
        if len(rows) <= 10 and "short" not in seen:
            chosen.append(("short_1_10", rows)); seen.add("short")
        if len(rows) == 142 and "acceptance_142" not in seen:
            chosen.append(("acceptance_142", rows)); seen.add("acceptance_142")
    if ids:
        median_id = ids[len(ids) // 2]
        chosen.append(("medium", grouped[median_id]))
        chosen.append(("longest", grouped[max(ids, key=lambda item: len(grouped[item]))]))
    return chosen


def _codec_benchmark(db: sqlite3.Connection) -> list[dict[str, Any]]:
    samples = _representative_rows(db)
    results: list[dict[str, Any]] = []
    for sample_name, rows in samples:
        fields = list(rows[0]) if rows else []
        candidates = {
            "json_arrays": json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"),
            "msgpack_row_arrays": msgpack.packb(
                [[row[field] for field in fields] for row in rows], use_bin_type=True, strict_types=True
            ),
            "msgpack_column_arrays": msgpack.packb(
                [[row[field] for row in rows] for field in fields], use_bin_type=True, strict_types=True
            ),
        }
        for name, raw in candidates.items():
            for level in (3, 6, 9):
                started = time.perf_counter()
                payload = zstandard.ZstdCompressor(level=level).compress(raw)
                encode_ms = (time.perf_counter() - started) * 1000.0
                started = time.perf_counter()
                decoded = zstandard.ZstdDecompressor().decompress(payload, max_output_size=len(raw))
                decode_ms = (time.perf_counter() - started) * 1000.0
                if decoded != raw:
                    raise RuntimeError(f"codec benchmark round-trip failed: {name}")
                results.append({
                    "sample": sample_name,
                    "bucket_count": len(rows),
                    "candidate": name,
                    "level": level,
                    "uncompressed_bytes": len(raw),
                    "compressed_bytes": len(payload),
                    "bytes_per_bucket": round(len(payload) / max(1, len(rows)), 6),
                    "compression_ratio": round(len(payload) / max(1, len(raw)), 6),
                    "encode_ms": round(encode_ms, 6),
                    "decode_ms": round(decode_ms, 6),
                    "deterministic": payload == zstandard.ZstdCompressor(level=level).compress(raw),
                })
    return results


def benchmark(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        facts = _logical_facts(connection)
        profile = _physical_profile(connection, "position_path_observations")
        deferred_profile = _physical_profile(connection, "position_path_observation_deferred_buckets")
        combined_bytes = int(profile.get("bytes") or 0) + int(deferred_profile.get("bytes") or 0)
        db_bytes = path.stat().st_size
        wal_bytes = path.with_name(path.name + "-wal").stat().st_size if path.with_name(path.name + "-wal").exists() else 0
        facts.update({
            "table_bytes": profile.get("bytes", 0),
            "index_bytes": profile.get("index_bytes", 0),
            "payload_bytes": profile.get("payload_bytes", 0),
            "unused_bytes": profile.get("unused_bytes", 0),
            "deferred_table": deferred_profile,
            "position_path_bytes_including_indexes": combined_bytes,
            "percentage_of_app_db": round(combined_bytes / max(1, db_bytes) * 100.0, 6),
            "bytes_per_bucket": _bytes_per_bucket(connection, "position_path_observations", int(facts.get("total_rows") or 0)),
            "database_bytes": db_bytes,
            "wal_bytes": wal_bytes,
            "total_database_and_wal_bytes": db_bytes + wal_bytes,
            "duplicate_exact_market_bars": _duplicate_bars(connection),
            "constant_repeated_field_cardinality": _repeated_fields(connection),
        })
        return {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "database_path": str(path),
            "read_only": True,
            "physical_measurement": "sqlite_dbstat",
            "current_storage": facts,
            "codec_benchmarks": _codec_benchmark(connection),
            "notes": [
                "Bytes are SQLite dbstat page allocations; per-bucket percentiles use weighted page-cell allocation.",
                "Codec candidates exclude future SQLite segment metadata until a shadow archive exists.",
                "A deferred bucket is recovery work, never a compression success or health signal.",
            ],
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = benchmark(args.database)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
