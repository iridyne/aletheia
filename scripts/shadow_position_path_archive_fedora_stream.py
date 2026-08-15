"""Verify the Position-path codec against a read-only Fedora row stream.

The input is JSON on stdin with ``positions``, ``deferred`` (Position IDs),
and ``rows`` (the v26 observation rows).  The script never opens or mutates a
database; an SSH/read-only query can stream the source rows into it.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import sys
import time
from typing import Any, Iterable, Mapping

from scripts.benchmark_position_path_archive import (
    _candidate_payloads,
    _candidate_report,
    _percentile,
    _sample_coverage,
)
from aletheia.storage.position_path_archive import (
    BUCKET_MS,
    canonical_row,
    decode_segment,
    encode_segment,
    segment_start_for_bucket,
)


def shadow_stream(payload: Mapping[str, Any], *, as_of: str | None = None) -> dict[str, Any]:
    positions = payload.get("positions")
    deferred = payload.get("deferred")
    source_rows = payload.get("rows")
    if not isinstance(positions, list) or not isinstance(deferred, list) or not isinstance(source_rows, list):
        raise ValueError("stream payload must contain positions, deferred, and rows arrays")

    rows_by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_rows:
        if not isinstance(row, Mapping):
            raise ValueError("stream observation rows must be objects")
        item = dict(row)
        try:
            position_id = str(item["position_id"])
        except KeyError as exc:
            raise ValueError("stream observation row is missing position_id") from exc
        rows_by_position[position_id].append(item)
    deferred_ids = {str(value) for value in deferred}
    skips: dict[str, int] = defaultdict(int)
    eligible: list[dict[str, Any]] = []
    eligible_positions = 0
    for raw_position in positions:
        if not isinstance(raw_position, Mapping):
            raise ValueError("stream positions must be objects")
        position = dict(raw_position)
        position_id = str(position.get("id") or "")
        if str(position.get("status") or "").lower() != "closed":
            skips["position_not_closed"] += 1
            continue
        if position_id in deferred_ids:
            skips["deferred_buckets_remain"] += 1
            continue
        try:
            first = _first_bucket(position["opened_at"])
            closed_ms = _timestamp_ms(position["closed_at"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            skips["invalid_position_time_bounds"] += 1
            continue
        last = (closed_ms // BUCKET_MS - 1) * BUCKET_MS
        if last < first:
            skips["invalid_position_time_bounds"] += 1
            continue
        expected = set(range(first, last + BUCKET_MS, BUCKET_MS))
        position_rows = rows_by_position.get(position_id, [])
        buckets = [int(row["bucket_open_ms"]) for row in position_rows]
        if len(buckets) != len(set(buckets)):
            skips["duplicate_buckets"] += 1
            continue
        by_bucket = {int(row["bucket_open_ms"]): row for row in position_rows}
        if set(by_bucket) != expected:
            skips["incomplete_or_unexpected_buckets"] += 1
            continue
        if any(
            row.get("evidence_status") not in {"available", "unavailable"}
            for row in by_bucket.values()
        ):
            skips["nonterminal_evidence"] += 1
            continue
        eligible_positions += 1
        eligible.extend(by_bucket.values())

    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        key = (
            str(row["position_id"]),
            segment_start_for_bucket(int(row["bucket_open_ms"])),
        )
        groups[key].append(row)

    cutoff = _parse_as_of(as_of) if as_of is not None else None
    raw_bytes = 0
    payload_bytes = 0
    rows_verified = 0
    segments = 0
    deterministic = True
    as_of_parity = True
    available_count = 0
    unavailable_count = 0
    candidate_stats: dict[str, dict[str, Any]] = {}
    sample_coverage = _sample_coverage(rows_by_position)
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
    representative_input_complete = all(
        bool(sample_coverage[key]) for key in required_coverage
    )
    acceptance_reasons = [
        "deployed_fedora_archive_read_model_shadow_pending",
        "observer_write_latency_unmeasured",
        "2048_bucket_decode_sample_unavailable",
    ]
    if not representative_input_complete:
        acceptance_reasons.append("representative_input_incomplete")
    production_encode_ms: list[float] = []
    production_decode_ms: list[float] = []
    for (position_id, segment_start), rows in sorted(groups.items()):
        source = sorted(
            (canonical_row(row) for row in rows),
            key=lambda row: int(row["bucket_open_ms"]),
        )
        for name, (candidate_raw, candidate_payload, candidate_ms) in _candidate_payloads(
            source,
            position_id=position_id,
            start_ms=segment_start,
            end_ms=segment_start + 2048 * BUCKET_MS,
        ).items():
            stats = candidate_stats.setdefault(
                name,
                {
                    "raw_bytes": 0,
                    "payload_bytes": 0,
                    "encode_ms": [],
                    "ratios": [],
                    "bytes_per_bucket": [],
                },
            )
            stats["raw_bytes"] += candidate_raw
            stats["payload_bytes"] += len(candidate_payload)
            stats["encode_ms"].append(candidate_ms)
            stats["ratios"].append(
                len(candidate_payload) / candidate_raw if candidate_raw else 0.0
            )
            stats["bytes_per_bucket"].append(
                len(candidate_payload) / max(1, len(source))
            )
        started = time.perf_counter_ns()
        encoded = encode_segment(
            source,
            position_id=position_id,
            segment_start_ms=segment_start,
        )
        encode_ms = (time.perf_counter_ns() - started) / 1_000_000
        production_encode_ms.append(encode_ms)
        started = time.perf_counter_ns()
        decoded = decode_segment(
            encoded.payload,
            codec=encoded.codec,
            codec_version=encoded.codec_version,
            codec_level=encoded.codec_level,
        )
        decode_ms = (time.perf_counter_ns() - started) / 1_000_000
        production_decode_ms.append(decode_ms)
        if decoded["rows"] != source:
            raise ValueError(
                f"stream codec round-trip mismatch for {position_id}:{segment_start}"
            )
        repeated = encode_segment(
            list(reversed(rows)),
            position_id=position_id,
            segment_start_ms=segment_start,
        )
        deterministic = deterministic and repeated.payload == encoded.payload
        effective_cutoff = cutoff or _median_observed_at(source)
        expected_visible = [
            row for row in source if _timestamp(row["observed_at"]) <= effective_cutoff
        ]
        actual_visible = [
            row for row in decoded["rows"] if _timestamp(row["observed_at"]) <= effective_cutoff
        ]
        as_of_parity = as_of_parity and actual_visible == expected_visible
        raw_bytes += encoded.raw_bytes
        payload_bytes += encoded.payload_bytes
        rows_verified += len(rows)
        segments += 1
        available_count += encoded.available_count
        unavailable_count += encoded.unavailable_count

    status = (
        "insufficient_data"
        if not eligible
        else "passed"
        if rows_verified == len(eligible) and deterministic and as_of_parity
        else "failed"
    )
    return {
        "contract_version": "position_path_archive_fedora_stream/v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": "fedora_read_only_row_stream",
        "status": status,
        "archive_persisted": False,
        "acceptance": {
            "status": "incomplete",
            "reasons": acceptance_reasons,
        },
        "read_model_parity": "not_run",
        "positions_total": len(positions),
        "eligible_positions": eligible_positions,
        "source_rows": len(source_rows),
        "eligible_rows": len(eligible),
        "rows_verified": rows_verified,
        "segments": segments,
        "raw_bytes": raw_bytes,
        "payload_bytes": payload_bytes,
        "payload_ratio": payload_bytes / raw_bytes if raw_bytes else None,
        "available_count": available_count,
        "unavailable_count": unavailable_count,
        "deterministic_byte_identity": deterministic,
        "as_of_parity": as_of_parity,
        "as_of_mode": "median_per_segment" if as_of is None else "explicit",
        "as_of_cutoff": as_of,
        "production_codec_timing_ms": {
            "encode_p50": _percentile(production_encode_ms, 0.50),
            "encode_p95": _percentile(production_encode_ms, 0.95),
            "decode_p50": _percentile(production_decode_ms, 0.50),
            "decode_p95": _percentile(production_decode_ms, 0.95),
        },
        "candidate_benchmark": {
            "status": "available" if candidate_stats else "not_available",
            "source": "same_fedora_read_only_stream",
            "candidates": _candidate_report(candidate_stats),
        },
        "sample_coverage": sample_coverage,
        "representative_input_complete": representative_input_complete,
        "skips": dict(sorted(skips.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of", help="Optional fixed ISO cutoff for the parity check")
    parser.add_argument("--json", action="store_true", dest="compact")
    args = parser.parse_args()
    report = shadow_stream(json.load(sys.stdin), as_of=args.as_of)
    print(
        json.dumps(
            report,
            sort_keys=True,
            separators=(",", ":") if args.compact else None,
        )
    )
    return 0 if report["status"] == "passed" else 2 if report["status"] == "insufficient_data" else 1


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_ms(value: object) -> int:
    return int(_timestamp(value).timestamp() * 1000)


def _first_bucket(value: object) -> int:
    opened_ms = _timestamp_ms(value)
    return ((opened_ms + BUCKET_MS - 1) // BUCKET_MS) * BUCKET_MS


def _parse_as_of(value: str) -> datetime:
    return _timestamp(value)


def _median_observed_at(rows: Iterable[Mapping[str, Any]]) -> datetime:
    values = sorted(_timestamp(row["observed_at"]) for row in rows)
    return values[len(values) // 2]


if __name__ == "__main__":
    raise SystemExit(main())
