"""Benchmark Position-path archive encoding and unified-reader latency."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import msgpack
import sqlite3
import time
import tracemalloc
from pathlib import Path
from typing import Any

import zstandard

from aletheia.storage import repositories as repo
from aletheia.analytics.position_lifecycle_story import build_position_lifecycle_story
from aletheia.storage.position_path_archive import (
    BUCKET_MS,
    MAX_BUCKETS_PER_SEGMENT,
    SEGMENT_MS,
    decode_segment,
    encode_segment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--repeat", type=int, default=10)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    db = _open_readonly(args.database)
    segment_rows, total_segments, archived_segments, representative_paths = _sample_segments(
        db, max(1, min(int(args.samples), 10_000))
    )
    if not segment_rows:
        report = {
            "contract_version": "position_path_archive_benchmark/v1",
            "status": "insufficient_data",
            "reason": "no_archived_segments",
        }
        print(json.dumps(report, sort_keys=True, separators=(",", ":") if args.as_json else None))
        return 2
    candidate_names = [
        f"json+zstd:l{level}" for level in (3, 6, 9)
    ] + [
        f"msgpack_rows+zstd:l{level}" for level in (3, 6, 9)
    ] + [
        f"msgpack_columns+zstd:l{level}" for level in (3, 6, 9)
    ] + ["msgpack_columns:raw", "position_path_segment/v1"]
    candidate_stats: dict[str, dict[str, Any]] = {
        name: {
            "raw_bytes": 0,
            "payload_bytes": 0,
            "encode_ms": [],
            "ratios": [],
            "bytes_per_bucket": [],
        }
        for name in candidate_names
    }
    encode_samples: list[float] = []
    decode_samples: list[float] = []
    decode_2048_samples: list[float] = []
    read_samples: list[float] = []
    as_of_samples: list[float] = []
    raw_bytes = 0
    payload_bytes = 0
    verified_rows = 0
    deterministic = True
    as_of_cutoffs: list[datetime] = []
    sample_rows_by_position: dict[str, list[dict]] = {}
    repeat = max(1, min(int(args.repeat), 100))
    tracemalloc.start()
    for segment in segment_rows:
        position_id = str(segment["position_id"])
        start_ms = int(segment["segment_start_ms"])
        end_ms = int(segment["segment_end_ms"])
        if archived_segments:
            rows = repo.list_archived_position_path_observations(
                position_id,
                start_ms=start_ms,
                end_ms=end_ms,
                db=db,
            )
        else:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM position_path_observations "
                    "WHERE position_id=? AND bucket_open_ms>=? AND bucket_open_ms<? "
                    "ORDER BY bucket_open_ms",
                    (position_id, start_ms, end_ms),
                ).fetchall()
            ]
        candidates = _candidate_payloads(
            rows,
            position_id=position_id,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        for name, (candidate_raw, candidate_payload, candidate_ms) in candidates.items():
            stats = candidate_stats[name]
            stats["raw_bytes"] += candidate_raw
            stats["payload_bytes"] += len(candidate_payload)
            stats["encode_ms"].append(candidate_ms)
            stats["ratios"].append(len(candidate_payload) / candidate_raw if candidate_raw else 0.0)
            stats["bytes_per_bucket"].append(
                len(candidate_payload) / max(1, len(rows))
            )
            if name == "position_path_segment/v1":
                raw_bytes += candidate_raw
                payload_bytes += len(candidate_payload)
        verified_rows += len(rows)
        sample_rows_by_position.setdefault(position_id, []).extend(rows)
        encoded = encode_segment(
            rows,
            position_id=position_id,
            segment_start_ms=start_ms,
            segment_end_ms=end_ms,
        )
        encoded_payload = (
            bytes(segment["payload"])
            if archived_segments and segment["payload"] is not None
            else candidates["position_path_segment/v1"][1]
        )
        encoded_codec = str(segment["codec"]) if archived_segments else encoded.codec
        encoded_codec_version = (
            str(segment["codec_version"])
            if archived_segments
            else encoded.codec_version
        )
        encoded_codec_level = (
            int(segment["codec_level"]) if archived_segments else encoded.codec_level
        )
        for _ in range(repeat):
            started = time.perf_counter_ns()
            reencoded = encode_segment(
                rows,
                position_id=position_id,
                segment_start_ms=start_ms,
                segment_end_ms=end_ms,
            )
            deterministic = deterministic and reencoded.payload == candidates[
                "position_path_segment/v1"
            ][1]
            encode_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            started = time.perf_counter_ns()
            decoded = decode_segment(
                encoded_payload,
                codec=encoded_codec,
                codec_version=encoded_codec_version,
                codec_level=encoded_codec_level,
            )
            decode_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            if len(rows) == MAX_BUCKETS_PER_SEGMENT:
                decode_2048_samples.append(
                    (time.perf_counter_ns() - started) / 1_000_000
                )
            if decoded["rows"] != rows:
                raise RuntimeError(
                    f"codec decode mismatch for {position_id}:{start_ms}"
                )
            started = time.perf_counter_ns()
            unified = repo.list_position_path_observations_unified(
                position_id,
                start_ms=start_ms,
                end_ms=end_ms,
                db=db,
            )
            read_samples.append((time.perf_counter_ns() - started) / 1_000_000)
            if len(unified) != len(rows):
                raise RuntimeError(
                    f"unified reader row count mismatch for {position_id}:{start_ms}"
                )
            cutoff = _as_of_cutoff(rows)
            if cutoff is not None:
                as_of_cutoffs.append(cutoff)
                started = time.perf_counter_ns()
                repo.list_position_path_observations_unified(
                    position_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    as_of=cutoff,
                    db=db,
                )
                as_of_samples.append((time.perf_counter_ns() - started) / 1_000_000)
    synthetic_2048 = _benchmark_synthetic_2048(
        sample_rows_by_position,
        repeat=repeat,
    )
    if synthetic_2048 is not None:
        deterministic = deterministic and bool(synthetic_2048["deterministic"])
    _, peak_memory_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    replay_samples = _benchmark_replay(db, representative_paths, as_of_cutoffs)
    ratio = payload_bytes / raw_bytes if raw_bytes else None
    archive_db_bytes = _dbstat_object_bytes(db, "position_path_archive_segments")
    hot_db_bytes = _dbstat_object_bytes(db, "position_path_observations")
    hot_row_count = int(
        db.execute("SELECT COUNT(*) FROM position_path_observations").fetchone()[0]
    )
    storage_ratio = (
        archive_db_bytes / hot_db_bytes
        if archived_segments and hot_db_bytes
        else None
    )
    read_p95 = _percentile(read_samples, 0.95)
    decode_p95 = _percentile(decode_samples, 0.95)
    sample_coverage = _sample_coverage(sample_rows_by_position)
    required_coverage = (
        "short_1_to_10_buckets",
        "acceptance_142_buckets",
        "medium_or_long_path",
        "unavailable_observations",
        "multiple_runtime_sessions",
        "multiple_producer_commits",
        "exit_plan_revisions",
        "quantity_variation_scale_in_or_partial_signal",
        "late_recovered_observations",
    )
    representative_input_missing = [
        key for key in required_coverage if not bool(sample_coverage[key])
    ]
    representative_input_complete = not representative_input_missing
    acceptance_reasons = [
        "write_latency_not_measured",
        "deployed_fedora_archive_read_model_shadow_pending",
    ]
    if not archived_segments:
        acceptance_reasons.append("archive_storage_ratio_not_available")
    if not decode_2048_samples:
        acceptance_reasons.append("2048_bucket_decode_sample_unavailable")
    status = (
        "insufficient_data"
        if not segment_rows
        else "insufficient_representation"
        if not representative_input_complete
        else "passed"
        if (not archived_segments or (storage_ratio is not None and storage_ratio <= 0.40))
        and read_p95 is not None
        and read_p95 < 100
        and decode_p95 is not None
        and decode_p95 < 100
        else "failed"
    )
    report = {
        "contract_version": "position_path_archive_benchmark/v2",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "acceptance": {
            "status": "incomplete",
            "reasons": acceptance_reasons,
        },
        "sample_segments": len(segment_rows),
        "total_segments": total_segments,
        "source": "archive" if archived_segments else "hot_observations",
        "raw_bytes": raw_bytes,
        "payload_bytes": payload_bytes,
        "compressed_ratio": ratio,
        "deterministic_byte_identity": deterministic,
        "peak_memory_bytes": peak_memory_bytes,
        "verified_rows": verified_rows,
        "representative_path_count": len(representative_paths),
        "sample_coverage": sample_coverage,
        "representative_input_complete": representative_input_complete,
        "representative_input_missing": representative_input_missing,
        "storage": {
            "current_sqlite_row_layout": {
                "rows": hot_row_count,
                "table_index_bytes": hot_db_bytes,
                "average_bytes_per_bucket": (
                    hot_db_bytes / hot_row_count if hot_row_count else None
                ),
            },
            "hot_table_index_bytes": hot_db_bytes,
            "archive_table_index_bytes": archive_db_bytes,
            "archive_to_hot_ratio": storage_ratio,
        },
        "encode_ms": {
            "p50": _percentile(encode_samples, 0.50),
            "p95": _percentile(encode_samples, 0.95),
        },
        "unified_read_ms": {
            "p50": _percentile(read_samples, 0.50),
            "p95": read_p95,
        },
        "decode_ms": {
            "p50": _percentile(decode_samples, 0.50),
            "p95": decode_p95,
        },
        "decode_2048_ms": {
            "status": "available" if decode_2048_samples else "not_available",
            "p50": _percentile(decode_2048_samples, 0.50),
            "p95": _percentile(decode_2048_samples, 0.95),
            "synthetic_local": synthetic_2048,
        },
        "as_of_read_ms": {
            "p50": _percentile(as_of_samples, 0.50),
            "p95": _percentile(as_of_samples, 0.95),
        },
        "full_replay_ms": {
            "p50": _percentile(replay_samples, 0.50),
            "p95": _percentile(replay_samples, 0.95),
        },
        "codec_candidates": _candidate_report(candidate_stats),
        "codec_selection": {
            "production": "position_path_segment/v1",
            "status": "provisional_until_real_fedora_shadow",
            "parquet": {
                "status": "not_selected",
                "reason": "first phase requires fixed-schema MessagePack+Zstd; no material Parquet benefit is established here",
            },
            "raw_msgpack": {
                "status": "fallback",
                "reason": "selected only when the level-3 Zstandard payload is larger than raw MessagePack",
            },
            "shared_zstd_dictionary": {
                "status": "not_used",
                "reason": "v1 adds no shared dictionary without measured >=15% additional total savings across representative p50/p95 paths",
            },
        },
        "write_latency": {
            "status": "not_measured",
            "reason": "benchmark opens the database read-only; no scheduler or hot write path is changed",
        },
        "targets": {
            "archive_total_storage_lte_0_40_of_hot": (
                storage_ratio is not None and storage_ratio <= 0.40
            ),
            "unified_read_p95_ms_lt_100": read_p95 is not None and read_p95 < 100,
            "decode_p95_ms_lt_100": decode_p95 is not None and decode_p95 < 100,
            "decode_2048_p95_ms_lt_100": (
                bool(decode_2048_samples)
                and _percentile(decode_2048_samples, 0.95) is not None
                and _percentile(decode_2048_samples, 0.95) < 100
            ),
            "representative_input_complete": representative_input_complete,
        },
    }
    print(json.dumps(report, sort_keys=True, separators=(",", ":") if args.as_json else None))
    return (
        0
        if report["status"] == "passed"
        else 2
        if status in {"insufficient_data", "insufficient_representation"}
        else 1
    )


def _open_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=1.0)
    db.row_factory = sqlite3.Row
    db.execute("BEGIN")
    return db


def _sample_segments(
    db: sqlite3.Connection, limit: int
) -> tuple[list[sqlite3.Row], int, bool, list[str]]:
    archive_exists = db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='position_path_archive_segments'"
    ).fetchone() is not None
    if archive_exists:
        total = int(
            db.execute("SELECT COUNT(*) FROM position_path_archive_segments").fetchone()[0]
        )
        if total:
            catalog = db.execute(
                "SELECT position_id,segment_start_ms,segment_end_ms,row_count,"
                "raw_bytes,payload_bytes,codec,codec_version,codec_level,payload "
                "FROM position_path_archive_segments "
                "ORDER BY position_id,segment_start_ms"
            ).fetchall()
            paths = _representative_position_ids(
                db,
                [str(row["position_id"]) for row in catalog],
                archived=True,
            )
            return _bounded_segment_sample(catalog, total, limit, paths, True)
    hot_exists = db.execute(
        "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='position_path_observations'"
    ).fetchone() is not None
    if not hot_exists:
        return [], 0, False, []
    segments = db.execute(
        f"SELECT position_id, (bucket_open_ms / {SEGMENT_MS}) * {SEGMENT_MS} AS segment_start_ms, "
        f"((bucket_open_ms / {SEGMENT_MS}) + 1) * {SEGMENT_MS} AS segment_end_ms, "
        "COUNT(*) AS row_count, 0 AS raw_bytes, 0 AS payload_bytes "
        f"FROM position_path_observations GROUP BY position_id, bucket_open_ms / {SEGMENT_MS} "
        "ORDER BY position_id, segment_start_ms"
    ).fetchall()
    total = int(
        db.execute(
            f"SELECT COUNT(*) FROM (SELECT position_id, bucket_open_ms / {SEGMENT_MS} "
            f"FROM position_path_observations GROUP BY position_id, bucket_open_ms / {SEGMENT_MS})"
        ).fetchone()[0]
    )
    paths = _representative_position_ids(
        db,
        [str(row["position_id"]) for row in segments],
        archived=False,
    )
    return _bounded_segment_sample(segments, total, limit, paths, False)


def _bounded_segment_sample(
    catalog: list[sqlite3.Row],
    total: int,
    limit: int,
    paths: list[str],
    archived: bool,
) -> tuple[list[sqlite3.Row], int, bool, list[str]]:
    selected = [row for row in catalog if str(row["position_id"]) in set(paths)]
    if len(selected) > limit:
        indexes = sorted(
            {0, len(selected) - 1}
            | {
                round(index * (len(selected) - 1) / max(1, limit - 1))
                for index in range(limit)
            }
        )
        selected = [selected[index] for index in indexes[:limit]]
    return selected, total, archived, paths


def _representative_position_ids(
    db: sqlite3.Connection,
    position_ids: list[str],
    *,
    archived: bool,
) -> list[str]:
    if not position_ids:
        return []
    if archived:
        rows = db.execute(
            "SELECT position_id,SUM(row_count) AS count "
            "FROM position_path_archive_segments GROUP BY position_id"
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT position_id,COUNT(*) AS count "
            "FROM position_path_observations GROUP BY position_id"
        ).fetchall()
    counts = sorted(
        ((str(row["position_id"]), int(row["count"])) for row in rows),
        key=lambda item: (item[1], item[0]),
    )
    selected: list[str] = []
    for label, predicate in (
        ("short", lambda count: count <= 10),
        ("acceptance", lambda count: 130 <= count <= 155),
        ("any", lambda count: True),
    ):
        candidates = [position for position, count in counts if predicate(count)]
        if candidates:
            selected.append(candidates[len(candidates) // 2])
            if label != "any":
                selected.append(candidates[-1])
    if counts:
        selected.extend([counts[len(counts) // 2][0], counts[-1][0]])
    # Preserve deterministic order while excluding IDs absent from the catalog.
    return list(dict.fromkeys(position for position in selected if position in set(position_ids)))

def _dbstat_object_bytes(db, table_name: str) -> int:
    try:
        rows = db.execute(
            "SELECT name, SUM(pgsize) AS bytes FROM dbstat GROUP BY name"
        ).fetchall()
    except sqlite3.OperationalError:
        return 0
    explicit_index_prefix = {
        "position_path_observations": "idx_position_path_observations",
        "position_path_observation_deferred_buckets": "idx_position_path_deferred",
        "position_path_archive_segments": "idx_position_path_archive",
    }.get(table_name, f"idx_{table_name}")
    return sum(
        int(row["bytes"])
        for row in rows
        if str(row["name"]) == table_name
        or str(row["name"]).startswith(f"idx_{table_name}")
        or str(row["name"]).startswith(f"sqlite_autoindex_{table_name}")
        or str(row["name"]).startswith(explicit_index_prefix)
    )


def _candidate_payloads(
    rows: list[dict],
    *,
    position_id: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, tuple[int, bytes, float]]:
    columns = sorted({key for row in rows for key in row})
    row_values = [[row.get(column) for column in columns] for row in rows]
    row_envelope = {"columns": columns, "rows": row_values}
    column_envelope = {
        "columns": columns,
        "values": [list(column_values) for column_values in zip(*row_values)],
    }
    encoded: dict[str, tuple[int, bytes, float]] = {}
    for shape, envelope in (
        ("json", row_envelope),
        ("msgpack_rows", row_envelope),
        ("msgpack_columns", column_envelope),
    ):
        raw = (
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
            if shape == "json"
            else msgpack.packb(envelope, use_bin_type=True)
        )
        for level in (3, 6, 9):
            started = time.perf_counter_ns()
            payload = zstandard.ZstdCompressor(level=level).compress(raw)
            encoded[f"{shape}+zstd:l{level}"] = (
                len(raw),
                payload,
                (time.perf_counter_ns() - started) / 1_000_000,
            )
    raw_columns = msgpack.packb(column_envelope, use_bin_type=True)
    encoded["msgpack_columns:raw"] = (len(raw_columns), raw_columns, 0.0)
    started = time.perf_counter_ns()
    selected = encode_segment(
        rows,
        position_id=position_id,
        segment_start_ms=start_ms,
        segment_end_ms=end_ms,
    )
    encoded["position_path_segment/v1"] = (
        selected.raw_bytes,
        selected.payload,
        (time.perf_counter_ns() - started) / 1_000_000,
    )
    return encoded


def _candidate_report(stats: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    report = {}
    for name, item in stats.items():
        raw_bytes = int(item["raw_bytes"])
        payload_bytes = int(item["payload_bytes"])
        timings = [float(value) for value in item["encode_ms"]]
        report[name] = {
            "raw_bytes": raw_bytes,
            "payload_bytes": payload_bytes,
            "compressed_ratio": payload_bytes / raw_bytes if raw_bytes else None,
            "compressed_ratio_p50": _percentile(item["ratios"], 0.50),
            "compressed_ratio_p95": _percentile(item["ratios"], 0.95),
            "bytes_per_bucket_p50": _percentile(item["bytes_per_bucket"], 0.50),
            "bytes_per_bucket_p95": _percentile(item["bytes_per_bucket"], 0.95),
            "bytes_per_bucket_max": max(item["bytes_per_bucket"], default=None),
            "encode_ms": {
                "p50": _percentile(timings, 0.50),
                "p95": _percentile(timings, 0.95),
            },
        }
    return report


def _benchmark_synthetic_2048(
    groups: dict[str, list[dict]], *, repeat: int
) -> dict[str, Any] | None:
    """Measure codec scaling without presenting synthetic data as Fedora proof."""

    base = next(
        (
            row
            for position_rows in groups.values()
            for row in position_rows
            if row.get("evidence_status") == "available"
        ),
        None,
    )
    if base is None:
        return None
    try:
        observed_base = datetime.fromisoformat(
            str(base["observed_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return None
    if observed_base.tzinfo is None:
        observed_base = observed_base.replace(tzinfo=timezone.utc)
    position_id = "__position_path_benchmark_2048__"
    rows: list[dict] = []
    for index in range(MAX_BUCKETS_PER_SEGMENT):
        row = dict(base)
        row["position_id"] = position_id
        row["bucket_open_ms"] = index * BUCKET_MS
        row["observed_at"] = (
            observed_base + timedelta(minutes=index)
        ).isoformat()
        row["producer_commit"] = f"benchmark-commit-{index % 8}"
        row["runtime_session_id"] = f"benchmark-session-{index % 10}"
        row["exit_plan_version"] = index % 3
        row["quantity"] = float(base["quantity"]) * (1.0 + (index % 11) / 100.0)
        close = float(base["bar_close"]) + (index % 37) / 100.0
        row["bar_open"] = close - 0.1
        row["bar_high"] = close + 0.2
        row["bar_low"] = close - 0.2
        row["bar_close"] = close
        row["bar_volume"] = float(base["bar_volume"]) + index % 17
        row["mark_price"] = close
        if index % 97 == 0:
            row["evidence_status"] = "unavailable"
            row["evidence_reason"] = "benchmark_unavailable"
            for field in (
                "bar_open",
                "bar_high",
                "bar_low",
                "bar_close",
                "bar_volume",
                "bar_provider",
            ):
                row[field] = None
        else:
            row["evidence_status"] = "available"
            row["evidence_reason"] = "complete_exact_5m_bar"
        rows.append(row)
    encoded = encode_segment(rows, position_id=position_id, segment_start_ms=0)
    deterministic = encode_segment(
        list(reversed(rows)),
        position_id=position_id,
        segment_start_ms=0,
    ).payload == encoded.payload
    samples: list[float] = []
    for _ in range(repeat):
        started = time.perf_counter_ns()
        decoded = decode_segment(
            encoded.payload,
            codec=encoded.codec,
            codec_version=encoded.codec_version,
            codec_level=encoded.codec_level,
        )
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
        if decoded["rows"] != rows:
            raise RuntimeError("synthetic 2048-bucket codec round-trip mismatch")
    return {
        "status": "synthetic_local_only",
        "row_count": len(rows),
        "raw_bytes": encoded.raw_bytes,
        "payload_bytes": encoded.payload_bytes,
        "compressed_ratio": encoded.payload_bytes / encoded.raw_bytes,
        "deterministic": deterministic,
        "p50": _percentile(samples, 0.50),
        "p95": _percentile(samples, 0.95),
    }


def _as_of_cutoff(rows: list[dict]) -> datetime | None:
    timestamps: list[datetime] = []
    for row in rows:
        try:
            value = datetime.fromisoformat(
                str(row["observed_at"]).replace("Z", "+00:00")
            )
        except (KeyError, TypeError, ValueError):
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        timestamps.append(value.astimezone(timezone.utc))
    if not timestamps:
        return None
    timestamps.sort()
    return timestamps[len(timestamps) // 2]


def _sample_coverage(groups: dict[str, list[dict]]) -> dict[str, Any]:
    counts = [len(rows) for rows in groups.values()]
    return {
        "path_count": len(groups),
        "short_1_to_10_buckets": any(1 <= count <= 10 for count in counts),
        "acceptance_142_buckets": any(count == 142 for count in counts),
        "medium_or_long_path": any(count > 155 for count in counts),
        "unavailable_observations": any(
            row.get("evidence_status") == "unavailable"
            for rows in groups.values()
            for row in rows
        ),
        "multiple_runtime_sessions": any(
            len({row.get("runtime_session_id") for row in rows if row.get("runtime_session_id")})
            > 1
            for rows in groups.values()
        ),
        "multiple_producer_commits": any(
            len({row.get("producer_commit") for row in rows if row.get("producer_commit")})
            > 1
            for rows in groups.values()
        ),
        "exit_plan_revisions": any(
            len({int(row.get("exit_plan_version") or 0) for row in rows}) > 1
            for rows in groups.values()
        ),
        "quantity_variation_scale_in_or_partial_signal": any(
            len({float(row["quantity"]) for row in rows if row.get("quantity") is not None})
            > 1
            for rows in groups.values()
        ),
        "late_recovered_observations": any(
            _is_late_observation(row)
            for rows in groups.values()
            for row in rows
        ),
    }


def _is_late_observation(row: dict) -> bool:
    try:
        bucket = datetime.fromtimestamp(
            int(row["bucket_open_ms"]) / 1000,
            timezone.utc,
        )
        observed = datetime.fromisoformat(
            str(row["observed_at"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError, OSError, OverflowError):
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.astimezone(timezone.utc) > bucket + timedelta(minutes=5)


def _benchmark_replay(
    db: sqlite3.Connection,
    position_ids: list[str],
    cutoffs: list[datetime],
) -> list[float]:
    if not position_ids:
        return []
    cutoff = cutoffs[len(cutoffs) // 2] if cutoffs else datetime.now(timezone.utc)
    samples: list[float] = []
    for position_id in position_ids:
        started = time.perf_counter_ns()
        build_position_lifecycle_story(position_id, as_of_at=cutoff, db=db)
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    return samples



def _percentile(values: list[float], quantile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return round(ordered[index], 6)


if __name__ == "__main__":
    raise SystemExit(main())
