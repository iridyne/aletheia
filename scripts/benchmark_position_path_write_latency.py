"""Measure hot Position-path insert latency on disposable SQLite clones."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any

from aletheia.storage import migrations
from aletheia.storage.position_path_archive import BUCKET_MS, OBSERVATION_COLUMNS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=50)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    source = _open_readonly(args.database)
    try:
        base = source.execute(
            "SELECT * FROM position_path_observations "
            "ORDER BY position_id,bucket_open_ms LIMIT 1"
        ).fetchone()
        if base is None:
            report = {
                "contract_version": "position_path_write_latency/v1",
                "status": "insufficient_data",
                "reason": "no_position_path_observations",
            }
        else:
            repeat = max(5, min(int(args.repeat), 500))
            with tempfile.TemporaryDirectory(
                prefix="aletheia-position-path-write-bench-"
            ) as temp_dir:
                with_archive = _clone(source, Path(temp_dir) / "with-archive.db")
                without_archive = _clone(
                    source,
                    Path(temp_dir) / "without-archive.db",
                    strip_archive=True,
                )
                try:
                    with_archive_result = _measure(with_archive, base, repeat)
                    without_archive_result = _measure(
                        without_archive, base, repeat
                    )
                finally:
                    with_archive.close()
                    without_archive.close()
            report = _report(with_archive_result, without_archive_result)
    finally:
        source.close()

    report["captured_at"] = datetime.now(timezone.utc).isoformat()
    print(
        json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":") if args.as_json else None,
        )
    )
    return 0 if report["status"] == "measured" else 2


def _open_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=1.0)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    return db


def _clone(
    source: sqlite3.Connection,
    path: Path,
    *,
    strip_archive: bool = False,
) -> sqlite3.Connection:
    target = sqlite3.connect(path)
    target.execute("PRAGMA foreign_keys=OFF")
    target.executescript(
        "CREATE TABLE positions(id TEXT PRIMARY KEY);"
        + migrations._POSITION_PATH_OBSERVATIONS_SQL
        + ("" if strip_archive else migrations._POSITION_PATH_ARCHIVE_SQL)
    )
    position_ids = [
        (str(row[0]),)
        for row in source.execute(
            "SELECT DISTINCT position_id FROM position_path_observations"
        ).fetchall()
    ]
    target.executemany("INSERT INTO positions(id) VALUES (?)", position_ids)
    columns = tuple(OBSERVATION_COLUMNS)
    source_rows = source.execute(
        "SELECT * FROM position_path_observations ORDER BY position_id,bucket_open_ms"
    ).fetchall()
    target.executemany(
        "INSERT INTO position_path_observations ("
        + ",".join(columns)
        + ") VALUES ("
        + ",".join("?" for _ in columns)
        + ")",
        [tuple(row[column] for column in columns) for row in source_rows],
    )
    if not strip_archive and _table_exists(source, "position_path_archive_segments"):
        archive_rows = source.execute(
            "SELECT position_id,segment_number,segment_start_ms,segment_end_ms,"
            "first_bucket_open_ms,last_bucket_open_ms,row_count,available_count,"
            "unavailable_count,min_observed_at,max_observed_at,raw_bytes,payload_bytes,"
            "codec,codec_version,codec_level,dictionary_id,schema_version,encoder_provenance,"
            "content_hash,payload_hash,payload,sealed_at "
            "FROM position_path_archive_segments"
        ).fetchall()
        target.executemany(
            "INSERT INTO position_path_archive_segments ("
            "position_id,segment_number,segment_start_ms,segment_end_ms,"
            "first_bucket_open_ms,last_bucket_open_ms,row_count,available_count,"
            "unavailable_count,min_observed_at,max_observed_at,raw_bytes,payload_bytes,"
            "codec,codec_version,codec_level,dictionary_id,schema_version,encoder_provenance,"
            "content_hash,payload_hash,payload,sealed_at) VALUES ("
            + ",".join("?" for _ in range(23))
            + ")",
            [tuple(row) for row in archive_rows],
        )
    target.commit()
    target.row_factory = sqlite3.Row
    return target


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return (
        db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _measure(
    db: sqlite3.Connection,
    base: sqlite3.Row,
    repeat: int,
) -> dict[str, Any]:
    values = dict(base)
    position_id = str(values["position_id"])
    max_bucket = int(
        db.execute(
            "SELECT MAX(bucket_open_ms) FROM position_path_observations "
            "WHERE position_id=?",
            (position_id,),
        ).fetchone()[0]
    )
    columns = tuple(OBSERVATION_COLUMNS)
    sql = (
        "INSERT INTO position_path_observations ("
        + ",".join(columns)
        + ") VALUES ("
        + ",".join("?" for _ in columns)
        + ")"
    )
    durations: list[float] = []
    for index in range(repeat):
        row = dict(values)
        row["bucket_open_ms"] = max_bucket + (index + 1) * BUCKET_MS
        row["observed_at"] = _later_timestamp(str(values["observed_at"]), index)
        started = time.perf_counter_ns()
        db.execute("BEGIN IMMEDIATE")
        db.execute(sql, tuple(row[column] for column in columns))
        db.commit()
        durations.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "samples": len(durations),
        "p50_ms": _percentile(durations, 0.50),
        "p95_ms": _percentile(durations, 0.95),
        "max_ms": max(durations, default=None),
    }


def _later_timestamp(value: str, index: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=index + 1)).isoformat()


def _report(
    with_archive: dict[str, Any],
    without_archive: dict[str, Any],
) -> dict[str, Any]:
    control_p95 = without_archive["p95_ms"]
    archive_p95 = with_archive["p95_ms"]
    return {
        "contract_version": "position_path_write_latency/v1",
        "status": "measured",
        "method": "temporary_sqlite_clones_insert_begin_immediate_commit",
        "with_archive_schema": with_archive,
        "without_archive_schema": without_archive,
        "p95_ratio": (
            archive_p95 / control_p95
            if archive_p95 is not None and control_p95
            else None
        ),
        "acceptance": {
            "status": "incomplete",
            "reason": "isolated_clone_measurement_is_not_Fedora_observer_scheduler_proof",
        },
    }


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * quantile)))
    return round(ordered[index], 6)


if __name__ == "__main__":
    raise SystemExit(main())
