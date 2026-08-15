"""Deterministic, immutable codec for Position-path archive segments."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
from typing import Any

import msgpack
import zstandard


SCHEMA_VERSION = "position_path_segment/v1"
CODEC = "msgpack+zstd"
CODEC_LEVEL = 3
RAW_CODEC = "msgpack"
RAW_CODEC_LEVEL = 0
CODEC_VERSION = "msgpack+zstd/v1"
RAW_CODEC_VERSION = "msgpack/v1"
SUPPORTED_CODECS = frozenset({CODEC, RAW_CODEC})
ENCODER_PROVENANCE = "aletheia.storage.position_path_archive:v1"
BUCKET_MS = 5 * 60 * 1000
MAX_BUCKETS_PER_SEGMENT = 2048
SEGMENT_MS = BUCKET_MS * MAX_BUCKETS_PER_SEGMENT
_PAYLOAD_VERSION = 1
_MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# This is the v26 SQLite contract in column order.  The payload never trusts
# SQLite's SELECT * order, so a schema change must deliberately create v2.
OBSERVATION_COLUMNS = (
    "position_id",
    "bucket_open_ms",
    "observed_at",
    "venue",
    "symbol",
    "market_type",
    "direction",
    "entry_price",
    "quantity",
    "bar_open",
    "bar_high",
    "bar_low",
    "bar_close",
    "bar_volume",
    "bar_provider",
    "mark_price",
    "unrealized_pnl",
    "entry_fee",
    "funding_cost",
    "funding_source",
    "stop_price",
    "take_profit_price",
    "exit_plan_version",
    "evidence_status",
    "evidence_reason",
    "producer_commit",
    "runtime_session_id",
    "capture_mode",
)
_COLUMN_INDEX = {name: index for index, name in enumerate(OBSERVATION_COLUMNS)}
_SPECIAL_COLUMNS = frozenset({"position_id", "bucket_open_ms", "observed_at"})
_COLUMN_INDICES = tuple(
    index for index, name in enumerate(OBSERVATION_COLUMNS) if name not in _SPECIAL_COLUMNS
)
_STRING_COLUMNS = frozenset(
    {
        "market_type",
        "direction",
        "venue",
        "symbol",
        "bar_provider",
        "funding_source",
        "evidence_reason",
        "producer_commit",
        "runtime_session_id",
    }
)
_FLOAT_COLUMNS = frozenset(
    {
        "entry_price",
        "quantity",
        "bar_open",
        "bar_high",
        "bar_low",
        "bar_close",
        "bar_volume",
        "mark_price",
        "unrealized_pnl",
        "entry_fee",
        "funding_cost",
        "stop_price",
        "take_profit_price",
    }
)
_INT_COLUMNS = frozenset({"exit_plan_version"})
_ENUM_VALUES = {
    "market_type": ("spot", "perp"),
    "direction": ("long", "short"),
    "evidence_status": ("available", "unavailable"),
    "capture_mode": ("open_snapshot", "post_close_final_snapshot"),
}
_ENUM_CODES = {
    field: {value: index for index, value in enumerate(values)}
    for field, values in _ENUM_VALUES.items()
}


class PositionPathArchiveError(ValueError):
    """Raised when an archive segment is malformed or fails validation."""


@dataclass(frozen=True)
class EncodedSegment:
    """The encoded payload and immutable metadata for one bounded segment."""

    position_id: str
    segment_number: int
    segment_start_ms: int
    segment_end_ms: int
    first_bucket_open_ms: int | None
    last_bucket_open_ms: int | None
    row_count: int
    available_count: int
    unavailable_count: int
    min_observed_at: str | None
    max_observed_at: str | None
    raw_bytes: int
    payload_bytes: int
    payload: bytes
    content_hash: str
    payload_hash: str
    codec: str
    codec_version: str
    codec_level: int
    dictionary_id: str | None
    encoder_provenance: str


def segment_start_for_bucket(bucket_open_ms: int) -> int:
    """Return the fixed 2048-bucket segment start containing one bucket."""

    bucket = _nonnegative_int(bucket_open_ms, "bucket_open_ms")
    if bucket % BUCKET_MS:
        raise PositionPathArchiveError("bucket_open_ms must align to five minutes")
    return (bucket // SEGMENT_MS) * SEGMENT_MS


def canonical_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic, msgpack-safe copy of one SQLite row."""

    if not isinstance(row, Mapping):
        raise PositionPathArchiveError("archive row must be a mapping")
    return {str(key): _canonical_value(row[key]) for key in sorted(row, key=str)}


def encode_segment(
    rows: Sequence[Mapping[str, Any]],
    *,
    position_id: str,
    segment_start_ms: int,
    segment_end_ms: int | None = None,
) -> EncodedSegment:
    """Encode canonical fixed-schema columns into deterministic MessagePack+Zstd."""

    position = _required_text(position_id, "position_id")
    start = _aligned_nonnegative_int(segment_start_ms, "segment_start_ms")
    end = start + SEGMENT_MS if segment_end_ms is None else _aligned_nonnegative_int(
        segment_end_ms, "segment_end_ms"
    )
    _validate_bounds(start, end)
    normalized = _canonical_source_rows(rows, position)
    if any(
        int(row["bucket_open_ms"]) < start or int(row["bucket_open_ms"]) >= end
        for row in normalized
    ):
        raise PositionPathArchiveError("archive row falls outside segment bounds")
    envelope = _build_envelope(normalized, position, start, end)
    raw_payload = msgpack.packb(envelope, use_bin_type=True)
    compressed_payload = zstandard.ZstdCompressor(level=CODEC_LEVEL).compress(raw_payload)
    if len(compressed_payload) > len(raw_payload):
        codec = RAW_CODEC
        codec_version = RAW_CODEC_VERSION
        codec_level = RAW_CODEC_LEVEL
        payload = raw_payload
    else:
        codec = CODEC
        codec_version = CODEC_VERSION
        codec_level = CODEC_LEVEL
        payload = compressed_payload
    content_hash = _semantic_hash(normalized)
    payload_hash = sha256(payload).hexdigest()

    decoded = decode_segment(
        payload,
        codec=codec,
        codec_version=codec_version,
        codec_level=codec_level,
    )
    if decoded["rows"] != normalized:
        raise PositionPathArchiveError("archive payload failed source round-trip")
    expected_min_observed_at, expected_max_observed_at = _observed_at_bounds(normalized)
    expected_available = sum(
        row.get("evidence_status") == "available" for row in normalized
    )
    if (
        decoded["position_id"] != position
        or decoded["segment_start_ms"] != start
        or decoded["segment_end_ms"] != end
        or decoded["row_count"] != len(normalized)
        or decoded["first_bucket_open_ms"]
        != (normalized[0]["bucket_open_ms"] if normalized else None)
        or decoded["last_bucket_open_ms"]
        != (normalized[-1]["bucket_open_ms"] if normalized else None)
        or decoded["available_count"] != expected_available
        or decoded["unavailable_count"] != len(normalized) - expected_available
        or decoded["min_observed_at"] != expected_min_observed_at
        or decoded["max_observed_at"] != expected_max_observed_at
        or decoded["raw_bytes"] != len(raw_payload)
        or decoded["payload_bytes"] != len(payload)
        or decoded["content_hash"] != content_hash
        or decoded["payload_hash"] != payload_hash
        or decoded["codec"] != codec
        or decoded["codec_version"] != codec_version
        or decoded["codec_level"] != codec_level
    ):
        raise PositionPathArchiveError("archive payload failed metadata round-trip")
    return EncodedSegment(
        position_id=position,
        segment_number=start // SEGMENT_MS,
        segment_start_ms=start,
        segment_end_ms=end,
        first_bucket_open_ms=decoded["first_bucket_open_ms"],
        last_bucket_open_ms=decoded["last_bucket_open_ms"],
        row_count=len(normalized),
        available_count=decoded["available_count"],
        unavailable_count=decoded["unavailable_count"],
        min_observed_at=expected_min_observed_at,
        max_observed_at=expected_max_observed_at,
        raw_bytes=len(raw_payload),
        payload_bytes=len(payload),
        payload=payload,
        content_hash=content_hash,
        payload_hash=payload_hash,
        codec=codec,
        codec_version=codec_version,
        codec_level=codec_level,
        dictionary_id=None,
        encoder_provenance=ENCODER_PROVENANCE,
    )


def decode_segment(
    payload: bytes | bytearray | memoryview,
    *,
    codec: str = CODEC,
    codec_version: str | None = None,
    codec_level: int = CODEC_LEVEL,
) -> dict[str, Any]:
    """Decode and validate one archive payload, returning canonical rows."""

    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise PositionPathArchiveError("archive payload must be bytes")
    if not isinstance(codec, str) or codec not in SUPPORTED_CODECS:
        raise PositionPathArchiveError("archive codec is unsupported")
    expected_codec_version = (
        CODEC_VERSION if codec == CODEC else RAW_CODEC_VERSION
    )
    if codec_version is None:
        codec_version = expected_codec_version
    if codec_version != expected_codec_version:
        raise PositionPathArchiveError("archive codec version mismatch")
    if not isinstance(codec_level, int) or isinstance(codec_level, bool):
        raise PositionPathArchiveError("archive codec level is invalid")
    if codec == CODEC and codec_level != CODEC_LEVEL:
        raise PositionPathArchiveError("archive zstd codec level mismatch")
    if codec == RAW_CODEC and codec_level != RAW_CODEC_LEVEL:
        raise PositionPathArchiveError("archive raw codec level mismatch")
    try:
        payload_bytes = bytes(payload)
        raw_payload = (
            zstandard.ZstdDecompressor().decompress(
                payload_bytes, max_output_size=_MAX_DECOMPRESSED_BYTES
            )
            if codec == CODEC
            else payload_bytes
        )
        if len(raw_payload) > _MAX_DECOMPRESSED_BYTES:
            raise PositionPathArchiveError("archive payload exceeds size limit")
        envelope = msgpack.unpackb(raw_payload, raw=False, strict_map_key=True)
    except (msgpack.UnpackException, TypeError, ValueError, zstandard.ZstdError) as exc:
        raise PositionPathArchiveError("archive payload is not valid msgpack+zstd") from exc
    if not isinstance(envelope, list) or len(envelope) != 12:
        raise PositionPathArchiveError("archive envelope must be the v1 array schema")

    (
        schema,
        payload_version,
        bucket_step_ms,
        position,
        start,
        end,
        first_bucket,
        row_count,
        contiguous_marker,
        observed_codec,
        dictionaries,
        columns,
    ) = envelope
    if (
        schema != SCHEMA_VERSION
        or not isinstance(payload_version, int)
        or isinstance(payload_version, bool)
        or payload_version != _PAYLOAD_VERSION
    ):
        raise PositionPathArchiveError("archive schema version mismatch")
    if bucket_step_ms != BUCKET_MS:
        raise PositionPathArchiveError("archive bucket step mismatch")
    position = _required_text(position, "position_id")
    start = _aligned_nonnegative_int(start, "segment_start_ms")
    end = _aligned_nonnegative_int(end, "segment_end_ms")
    _validate_bounds(start, end)
    row_count = _nonnegative_int(row_count, "row_count")
    if row_count > MAX_BUCKETS_PER_SEGMENT:
        raise PositionPathArchiveError("archive row_count exceeds segment maximum")
    if contiguous_marker is not None:
        raise PositionPathArchiveError("archive payload must derive contiguous buckets")
    first_bucket = _decode_first_bucket(first_bucket, row_count, start, end)
    buckets = _decode_buckets(first_bucket, contiguous_marker, row_count, start, end)
    observed_at = _decode_observed_at(observed_codec, row_count)
    dictionary_map = _decode_dictionaries(dictionaries)
    values_by_index = _decode_columns(columns, row_count, dictionary_map)

    rows: list[dict[str, Any]] = []
    for index in range(row_count):
        row = {
            "position_id": position,
            "bucket_open_ms": buckets[index],
            "observed_at": observed_at[index],
        }
        for column_index, values in values_by_index.items():
            row[OBSERVATION_COLUMNS[column_index]] = values[index]
        _validate_source_contract(row)
        rows.append(canonical_row(row))

    if _build_envelope(rows, position, start, end) != envelope:
        raise PositionPathArchiveError("archive payload is not canonical")
    canonical_payload = msgpack.packb(envelope, use_bin_type=True)
    if canonical_payload != raw_payload:
        raise PositionPathArchiveError("archive payload is not canonical")
    recompressed = zstandard.ZstdCompressor(level=CODEC_LEVEL).compress(raw_payload)
    if codec == RAW_CODEC and len(recompressed) <= len(raw_payload):
        raise PositionPathArchiveError(
            "raw codec is only permitted when zstd is larger"
        )
    canonical_payload = recompressed if codec == CODEC else raw_payload
    if canonical_payload != bytes(payload):
        raise PositionPathArchiveError("archive payload is not canonical for codec")
    min_observed_at, max_observed_at = _observed_at_bounds(rows)
    return {
        "schema": SCHEMA_VERSION,
        "position_id": position,
        "segment_number": start // SEGMENT_MS,
        "segment_start_ms": start,
        "segment_end_ms": end,
        "first_bucket_open_ms": first_bucket,
        "last_bucket_open_ms": buckets[-1] if buckets else None,
        "rows": rows,
        "row_count": row_count,
        "available_count": sum(
            row.get("evidence_status") == "available" for row in rows
        ),
        "unavailable_count": sum(
            row.get("evidence_status") != "available" for row in rows
        ),
        "min_observed_at": min_observed_at,
        "max_observed_at": max_observed_at,
        "raw_bytes": len(raw_payload),
        "payload_bytes": len(payload),
        "content_hash": _semantic_hash(rows),
        "payload_hash": sha256(bytes(payload)).hexdigest(),
        "codec": codec,
        "codec_version": codec_version,
        "codec_level": codec_level,
    }


def _build_envelope(
    rows: Sequence[Mapping[str, Any]],
    position_id: str,
    segment_start_ms: int,
    segment_end_ms: int,
) -> list[Any]:
    buckets = [int(row["bucket_open_ms"]) for row in rows]
    first_bucket = buckets[0] if buckets else None
    if any(current != previous + BUCKET_MS for previous, current in zip(buckets, buckets[1:])):
        raise PositionPathArchiveError("archive buckets must be contiguous")
    contiguous_marker = None
    observed_codec = _encode_observed_at(rows)
    dictionaries: list[list[Any]] = []
    columns: list[list[Any]] = []
    for column_index in _COLUMN_INDICES:
        field = OBSERVATION_COLUMNS[column_index]
        source_values = [row[field] for row in rows]
        values = [_prepared_value(field, value) for value in source_values]
        if _all_same(values):
            columns.append([column_index, 0, values[0] if values else None])
            continue
        if field in _ENUM_CODES and all(
            value is None or value in _ENUM_CODES[field] for value in values
        ):
            encoded_values = [
                None if value is None else _ENUM_CODES[field][value]
                for value in values
            ]
            bitmap, non_null = _bitmap_and_values(encoded_values)
            columns.append([column_index, 2, bitmap, non_null])
            continue
        if field in _STRING_COLUMNS:
            dictionary = sorted({value for value in values if value is not None})
            dictionary_index = {value: index for index, value in enumerate(dictionary)}
            encoded_values = [
                None if value is None else dictionary_index[value] for value in values
            ]
            bitmap, non_null = _bitmap_and_values(encoded_values)
            dictionaries.append([column_index, dictionary])
            columns.append([column_index, 1, bitmap, non_null])
            continue
        bitmap, non_null = _bitmap_and_values(values)
        columns.append([column_index, 3, bitmap, non_null])
    return [
        SCHEMA_VERSION,
        _PAYLOAD_VERSION,
        BUCKET_MS,
        position_id,
        segment_start_ms,
        segment_end_ms,
        first_bucket,
        len(rows),
        contiguous_marker,
        observed_codec,
        dictionaries,
        columns,
    ]


def _decode_columns(
    columns: Any,
    row_count: int,
    dictionaries: dict[int, list[str]],
) -> dict[int, list[Any]]:
    if not isinstance(columns, list) or len(columns) != len(_COLUMN_INDICES):
        raise PositionPathArchiveError("archive columns are not fixed-schema")
    values_by_index: dict[int, list[Any]] = {}
    used_dictionaries: set[int] = set()
    for descriptor, expected_index in zip(columns, _COLUMN_INDICES):
        if not isinstance(descriptor, list) or len(descriptor) < 3:
            raise PositionPathArchiveError("archive column descriptor is invalid")
        column_index = _nonnegative_int(descriptor[0], "column index")
        mode = _nonnegative_int(descriptor[1], "column mode")
        if column_index != expected_index or column_index in values_by_index:
            raise PositionPathArchiveError("archive columns are not canonical")
        field = OBSERVATION_COLUMNS[column_index]
        if mode == 0:
            if len(descriptor) != 3:
                raise PositionPathArchiveError("constant archive column is invalid")
            values = [
                _prepared_value(field, descriptor[2]) for _ in range(row_count)
            ]
        elif mode in (1, 2, 3):
            if len(descriptor) != 4:
                raise PositionPathArchiveError("archive column payload is invalid")
            bitmap = _validate_bitmap(descriptor[2], row_count)
            non_null = descriptor[3]
            if not isinstance(non_null, list):
                raise PositionPathArchiveError("archive column values are invalid")
            expected_non_null = row_count - _null_count(bitmap, row_count)
            if len(non_null) != expected_non_null:
                raise PositionPathArchiveError("archive null bitmap is inconsistent")
            if mode == 1:
                if field not in _STRING_COLUMNS or column_index not in dictionaries:
                    raise PositionPathArchiveError("archive string dictionary is missing")
                dictionary = dictionaries[column_index]
                used_dictionaries.add(column_index)
                values = _decode_dictionary_column(bitmap, non_null, dictionary, row_count)
            elif mode == 2:
                if field not in _ENUM_CODES:
                    raise PositionPathArchiveError("archive enum column is invalid")
                enum_values = _ENUM_VALUES[field]
                values = _decode_enum_column(bitmap, non_null, enum_values, row_count)
            else:
                if field in _STRING_COLUMNS or field in _ENUM_CODES:
                    raise PositionPathArchiveError("archive numeric column is invalid")
                values = _decode_numeric_column(bitmap, non_null, field, row_count)
        else:
            raise PositionPathArchiveError("archive column mode is unknown")
        values_by_index[column_index] = values
    if set(dictionaries) - set(_COLUMN_INDICES):
        raise PositionPathArchiveError("archive dictionary column is unknown")
    if set(dictionaries) != used_dictionaries:
        raise PositionPathArchiveError("archive dictionary is unused")
    return values_by_index


def _decode_dictionary_column(
    bitmap: bytes, non_null: list[Any], dictionary: list[str], row_count: int
) -> list[str | None]:
    values: list[str | None] = []
    cursor = 0
    for index in range(row_count):
        if _bitmap_is_null(bitmap, index):
            values.append(None)
            continue
        code = _nonnegative_int(non_null[cursor], "dictionary code")
        cursor += 1
        if code >= len(dictionary):
            raise PositionPathArchiveError("archive dictionary code is invalid")
        values.append(dictionary[code])
    return values


def _decode_enum_column(
    bitmap: bytes,
    non_null: list[Any],
    enum_values: Sequence[str],
    row_count: int,
) -> list[str | None]:
    values: list[str | None] = []
    cursor = 0
    for index in range(row_count):
        if _bitmap_is_null(bitmap, index):
            values.append(None)
            continue
        code = _nonnegative_int(non_null[cursor], "enum code")
        cursor += 1
        if code >= len(enum_values):
            raise PositionPathArchiveError("archive enum code is invalid")
        values.append(enum_values[code])
    return values


def _decode_numeric_column(
    bitmap: bytes, non_null: list[Any], field: str, row_count: int
) -> list[Any]:
    values: list[Any] = []
    cursor = 0
    for index in range(row_count):
        if _bitmap_is_null(bitmap, index):
            values.append(None)
            continue
        value = non_null[cursor]
        cursor += 1
        values.append(_prepared_value(field, value))
    return values


def _encode_observed_at(rows: Sequence[Mapping[str, Any]]) -> list[Any]:
    timestamps: list[int] = []
    overrides: list[list[Any]] = []
    for index, row in enumerate(rows):
        raw = row.get("observed_at")
        if not isinstance(raw, str):
            raise PositionPathArchiveError("observed_at must be an ISO string")
        timestamp = _timestamp_to_microseconds(raw)
        timestamps.append(timestamp)
        if _format_microseconds(timestamp) != raw:
            overrides.append([index, raw])
    if not timestamps:
        return [None, [], overrides]
    deltas = [timestamps[0]]
    deltas.extend(current - previous for previous, current in zip(timestamps, timestamps[1:]))
    return [timestamps[0], deltas[1:], overrides]


def _decode_observed_at(codec: Any, row_count: int) -> list[str]:
    if (
        not isinstance(codec, list)
        or len(codec) != 3
        or (row_count == 0 and codec[0] is not None)
        or (
            row_count > 0
            and (not isinstance(codec[0], int) or isinstance(codec[0], bool))
        )
        or not isinstance(codec[1], list)
        or len(codec[1]) != max(0, row_count - 1)
        or not isinstance(codec[2], list)
    ):
        raise PositionPathArchiveError("archive observed-at delta codec is invalid")
    if row_count == 0:
        if codec[2]:
            raise PositionPathArchiveError("empty archive has observed-at overrides")
        return []
    timestamps = [int(codec[0])]
    for delta in codec[1]:
        if not isinstance(delta, int) or isinstance(delta, bool):
            raise PositionPathArchiveError("archive observed-at delta is invalid")
        timestamps.append(timestamps[-1] + delta)
    overrides: dict[int, str] = {}
    for item in codec[2]:
        if not isinstance(item, list) or len(item) != 2:
            raise PositionPathArchiveError("archive observed-at override is invalid")
        index = _nonnegative_int(item[0], "observed-at override index")
        if index >= row_count or index in overrides or not isinstance(item[1], str):
            raise PositionPathArchiveError("archive observed-at override is invalid")
        if _timestamp_to_microseconds(item[1]) != timestamps[index]:
            raise PositionPathArchiveError("archive observed-at override does not match delta")
        overrides[index] = item[1]
    try:
        return [
            overrides.get(index, _format_microseconds(timestamp))
            for index, timestamp in enumerate(timestamps)
        ]
    except (OverflowError, ValueError) as exc:
        raise PositionPathArchiveError(
            "archive observed-at timestamp is out of range"
        ) from exc


def _decode_dictionaries(value: Any) -> dict[int, list[str]]:
    if not isinstance(value, list):
        raise PositionPathArchiveError("archive dictionaries are invalid")
    result: dict[int, list[str]] = {}
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[1], list)
        ):
            raise PositionPathArchiveError("archive dictionary is invalid")
        index = _nonnegative_int(item[0], "dictionary column index")
        if index in result or index not in _COLUMN_INDICES:
            raise PositionPathArchiveError("archive dictionary column is invalid")
        dictionary = item[1]
        if not all(isinstance(value, str) for value in dictionary):
            raise PositionPathArchiveError("archive dictionary values are invalid")
        if dictionary != sorted(dictionary) or len(dictionary) != len(set(dictionary)):
            raise PositionPathArchiveError("archive dictionary is not canonical")
        result[index] = dictionary
    return result


def _decode_first_bucket(value: Any, row_count: int, start: int, end: int) -> int | None:
    if row_count == 0:
        if value is not None:
            raise PositionPathArchiveError("empty archive has a first bucket")
        return None
    first = _aligned_nonnegative_int(value, "first_bucket_open_ms")
    if first < start or first >= end:
        raise PositionPathArchiveError("first archive bucket is outside bounds")
    return first


def _decode_buckets(
    first: int | None,
    contiguous_marker: None,
    row_count: int,
    start: int,
    end: int,
) -> list[int]:
    if row_count == 0:
        return []
    if contiguous_marker is not None:
        raise PositionPathArchiveError("archive payload must derive contiguous buckets")
    if first is None:
        raise PositionPathArchiveError("non-empty archive has no first bucket")
    buckets = [first + index * BUCKET_MS for index in range(row_count)]
    if buckets[0] < start or buckets[-1] >= end:
        raise PositionPathArchiveError("archive bucket is outside bounds")
    return buckets


def _canonical_source_rows(
    rows: Sequence[Mapping[str, Any]], position_id: str
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_buckets: set[int] = set()
    for row in rows:
        item = canonical_row(row)
        missing = set(OBSERVATION_COLUMNS) - set(item)
        unknown = set(item) - set(OBSERVATION_COLUMNS)
        if missing or unknown:
            raise PositionPathArchiveError(
                "archive row schema mismatch"
                + (f"; missing={sorted(missing)}" if missing else "")
                + (f"; unknown={sorted(unknown)}" if unknown else "")
            )
        if item["position_id"] != position_id:
            raise PositionPathArchiveError("archive row position_id mismatch")
        bucket = _aligned_nonnegative_int(item["bucket_open_ms"], "bucket_open_ms")
        item["position_id"] = _required_text(item["position_id"], "position_id")
        item["bucket_open_ms"] = bucket
        if not isinstance(item["observed_at"], str):
            raise PositionPathArchiveError("observed_at must be an ISO string")
        _timestamp_to_microseconds(item["observed_at"])
        for field in _COLUMN_INDICES:
            name = OBSERVATION_COLUMNS[field]
            item[name] = _prepared_value(name, item[name])
        _validate_source_contract(item)
        if bucket in seen_buckets:
            raise PositionPathArchiveError("archive segment contains duplicate buckets")
        seen_buckets.add(bucket)
        normalized.append(item)
    normalized.sort(key=lambda item: int(item["bucket_open_ms"]))
    return normalized


def _prepared_value(field: str, value: Any) -> Any:
    if value is None:
        return None
    if field in _STRING_COLUMNS or field in _ENUM_CODES:
        if not isinstance(value, str):
            raise PositionPathArchiveError(f"{field} must be a string or NULL")
        if field in {"evidence_status", "capture_mode"} and value not in _ENUM_CODES[field]:
            raise PositionPathArchiveError(f"unsupported enum value in {field}")
        return value
    if field in _FLOAT_COLUMNS:
        if isinstance(value, bool):
            raise PositionPathArchiveError(f"{field} must be numeric, not boolean")
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PositionPathArchiveError(f"{field} must be numeric") from exc
        if not math.isfinite(parsed):
            raise PositionPathArchiveError(f"{field} must be finite")
        return parsed
    if field in _INT_COLUMNS:
        return _nonnegative_int(value, field)
    return _canonical_value(value)


def _validate_source_contract(row: Mapping[str, Any]) -> None:
    """Apply the v26 SQLite evidence contract before encoding."""

    for field in (
        "symbol",
        "funding_source",
        "evidence_status",
        "evidence_reason",
        "exit_plan_version",
        "capture_mode",
    ):
        if row.get(field) is None:
            raise PositionPathArchiveError(f"{field} cannot be NULL")
    if row["evidence_status"] not in _ENUM_CODES["evidence_status"]:
        raise PositionPathArchiveError("evidence_status is invalid")
    if row["evidence_status"] == "available":
        for field in (
            "venue",
            "market_type",
            "direction",
            "entry_price",
            "quantity",
            "bar_open",
            "bar_high",
            "bar_low",
            "bar_close",
            "bar_volume",
            "bar_provider",
        ):
            if row.get(field) is None:
                raise PositionPathArchiveError(
                    f"available evidence requires {field}"
                )
        if row["market_type"] not in _ENUM_CODES["market_type"]:
            raise PositionPathArchiveError("available market_type is invalid")
        if row["direction"] not in _ENUM_CODES["direction"]:
            raise PositionPathArchiveError("available direction is invalid")
        if row["evidence_reason"] != "complete_exact_5m_bar":
            raise PositionPathArchiveError(
                "available evidence_reason is invalid"
            )
        if float(row["entry_price"]) <= 0 or float(row["quantity"]) <= 0:
            raise PositionPathArchiveError(
                "available entry_price and quantity must be positive"
            )
        if row.get("mark_price") is not None and float(row["mark_price"]) <= 0:
            raise PositionPathArchiveError("available mark_price must be positive")
        if any(
            float(row[field]) <= 0
            for field in ("bar_open", "bar_high", "bar_low", "bar_close")
        ) or float(row["bar_volume"]) < 0:
            raise PositionPathArchiveError("available bar values are invalid")
    elif row["evidence_status"] == "unavailable":
        for field in (
            "bar_open",
            "bar_high",
            "bar_low",
            "bar_close",
            "bar_volume",
            "bar_provider",
        ):
            if row.get(field) is not None:
                raise PositionPathArchiveError(
                    "unavailable evidence cannot contain bar values"
                )
        if not str(row["evidence_reason"]):
            raise PositionPathArchiveError(
                "unavailable evidence_reason cannot be empty"
            )


def _bitmap_and_values(values: Sequence[Any]) -> tuple[bytes, list[Any]]:
    bitmap = bytearray((len(values) + 7) // 8)
    non_null: list[Any] = []
    for index, value in enumerate(values):
        if value is None:
            bitmap[index // 8] |= 1 << (index % 8)
        else:
            non_null.append(value)
    return bytes(bitmap), non_null


def _validate_bitmap(value: Any, row_count: int) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise PositionPathArchiveError("archive null bitmap is invalid")
    bitmap = bytes(value)
    if len(bitmap) != (row_count + 7) // 8:
        raise PositionPathArchiveError("archive null bitmap length is invalid")
    if row_count and row_count % 8:
        unused_mask = 0xFF << (row_count % 8)
        if bitmap[-1] & unused_mask:
            raise PositionPathArchiveError("archive null bitmap has trailing bits")
    return bitmap


def _bitmap_is_null(bitmap: bytes, index: int) -> bool:
    return bool(bitmap[index // 8] & (1 << (index % 8)))


def _null_count(bitmap: bytes, row_count: int) -> int:
    return sum(_bitmap_is_null(bitmap, index) for index in range(row_count))


def _all_same(values: Sequence[Any]) -> bool:
    if not values:
        return True
    first = _value_fingerprint(values[0])
    return all(_value_fingerprint(value) == first for value in values[1:])


def _value_fingerprint(value: Any) -> bytes:
    return msgpack.packb(_canonical_value(value), use_bin_type=True)


def _timestamp_to_microseconds(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise PositionPathArchiveError("observed_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = parsed.astimezone(timezone.utc) - _EPOCH
    return (
        delta.days * 86_400 * 1_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _format_microseconds(value: int) -> str:
    return (_EPOCH + timedelta(microseconds=value)).isoformat()


def _observed_at_bounds(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[str | None, str | None]:
    timestamps: list[datetime] = []
    for row in rows:
        raw = row.get("observed_at")
        if raw is None:
            continue
        timestamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamps.append(timestamp.astimezone(timezone.utc))
    if not timestamps:
        return None, None
    return min(timestamps).isoformat(), max(timestamps).isoformat()


def _semantic_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash canonical logical fields independently of the wire envelope/codec."""

    logical_rows = [
        [row[column] for column in OBSERVATION_COLUMNS]
        for row in rows
    ]
    return sha256(
        msgpack.packb(logical_rows, use_bin_type=True, strict_types=True)
    ).hexdigest()


def _validate_bounds(start: int, end: int) -> None:
    if start % SEGMENT_MS or end - start != SEGMENT_MS:
        raise PositionPathArchiveError("archive segment bounds must cover 2048 buckets")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PositionPathArchiveError(f"{field} is required")
    return value.strip()


def _nonnegative_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise PositionPathArchiveError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PositionPathArchiveError(f"{field} must be an integer") from exc
    if isinstance(value, float) and value != parsed:
        raise PositionPathArchiveError(f"{field} must be an integer")
    if parsed < 0:
        raise PositionPathArchiveError(f"{field} must be non-negative")
    return parsed


def _aligned_nonnegative_int(value: Any, field: str) -> int:
    parsed = _nonnegative_int(value, field)
    if parsed % BUCKET_MS:
        raise PositionPathArchiveError(f"{field} must align to five minutes")
    return parsed


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, float) and not math.isfinite(value):
            raise PositionPathArchiveError("archive values must be finite")
        return value
    raise PositionPathArchiveError(f"archive value type is unsupported: {type(value).__name__}")
