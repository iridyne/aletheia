"""Immutable, shadow-only Position path segment encoding.

The module deliberately owns both the wire contract and its verification.  A
segment is only useful when decoding it produces the exact canonical source
rows, so callers should treat :class:`PositionPathSegmentError` as a fail-closed
data-integrity signal rather than falling back to an unverified representation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

import msgpack
import zstandard


FORMAT_VERSION = "position_path_segment/v1"
BUCKET_STEP_MS = 300_000
MAX_BUCKETS_PER_SEGMENT = 2_048
DEFAULT_CODEC_LEVEL = 6

OBSERVATION_COLUMNS: tuple[str, ...] = (
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

_PAYLOAD_COLUMNS = tuple(
    column
    for column in OBSERVATION_COLUMNS
    if column not in {"position_id", "bucket_open_ms", "observed_at"}
)
_STRING_COLUMNS = {
    "venue",
    "symbol",
    "market_type",
    "direction",
    "bar_provider",
    "funding_source",
    "evidence_status",
    "evidence_reason",
    "producer_commit",
    "runtime_session_id",
    "capture_mode",
}
_INTEGER_COLUMNS = {"exit_plan_version"}


class PositionPathSegmentError(ValueError):
    """Raised when a segment cannot be proven semantically intact."""


@dataclass(frozen=True)
class EncodedPositionPathSegment:
    """Encoded segment plus the SQLite metadata required to verify it."""

    position_id: str
    segment_number: int
    first_bucket_open_ms: int
    last_bucket_open_ms: int
    bucket_count: int
    available_count: int
    unavailable_count: int
    min_observed_at: str
    max_observed_at: str
    format_version: str
    codec: str
    codec_level: int | None
    dictionary_id: str | None
    uncompressed_bytes: int
    compressed_bytes: int
    semantic_sha256: str
    payload_sha256: str
    payload: bytes

    def as_sql_values(self, *, sealed_at: str, encoder_provenance: str) -> tuple[Any, ...]:
        return (
            self.position_id,
            self.segment_number,
            self.first_bucket_open_ms,
            self.last_bucket_open_ms,
            self.bucket_count,
            self.available_count,
            self.unavailable_count,
            self.min_observed_at,
            self.max_observed_at,
            self.format_version,
            self.codec,
            self.codec_level,
            self.dictionary_id,
            self.uncompressed_bytes,
            self.compressed_bytes,
            self.semantic_sha256,
            self.payload_sha256,
            sealed_at,
            encoder_provenance,
            self.payload,
        )


def _observed_at(value: object) -> tuple[int, str]:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PositionPathSegmentError(f"invalid observed_at: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc)
    micros = int(parsed.timestamp() * 1_000_000)
    return micros, parsed.isoformat()


def _canonical_scalar(column: str, value: object) -> object:
    if value is None:
        return None
    if column in _STRING_COLUMNS:
        return str(value)
    if column in _INTEGER_COLUMNS:
        try:
            return int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PositionPathSegmentError(f"{column} must be an integer") from exc
    if isinstance(value, bool):
        return int(value)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PositionPathSegmentError(f"{column} must be numeric") from exc
    if not math.isfinite(number):
        raise PositionPathSegmentError(f"{column} must be finite")
    return number


def canonicalize_rows(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return sorted, schema-complete rows used for hashing and parity checks."""

    normalized: list[dict[str, object]] = []
    for source in rows:
        missing = [column for column in OBSERVATION_COLUMNS if column not in source]
        if missing:
            raise PositionPathSegmentError("source row is missing: " + ", ".join(missing))
        row = {column: source[column] for column in OBSERVATION_COLUMNS}
        try:
            bucket = int(row["bucket_open_ms"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise PositionPathSegmentError("bucket_open_ms must be an integer") from exc
        if bucket < 0 or bucket % BUCKET_STEP_MS:
            raise PositionPathSegmentError("bucket_open_ms is not a five-minute boundary")
        row["bucket_open_ms"] = bucket
        observed_micros, observed_text = _observed_at(row["observed_at"])
        row["observed_at"] = observed_text
        row["_observed_at_micros"] = observed_micros
        for column in _PAYLOAD_COLUMNS:
            row[column] = _canonical_scalar(column, row[column])
        row["position_id"] = str(row["position_id"])
        normalized.append(row)
    normalized.sort(key=lambda item: int(item["bucket_open_ms"]))
    if not normalized:
        raise PositionPathSegmentError("cannot encode an empty segment")
    position_ids = {str(row["position_id"]) for row in normalized}
    if len(position_ids) != 1:
        raise PositionPathSegmentError("a segment must contain one position_id")
    buckets = [int(row["bucket_open_ms"]) for row in normalized]
    if len(set(buckets)) != len(buckets):
        raise PositionPathSegmentError("duplicate bucket_open_ms in segment")
    if len(buckets) > MAX_BUCKETS_PER_SEGMENT:
        raise PositionPathSegmentError("segment exceeds 2048 buckets")
    if any(right - left != BUCKET_STEP_MS for left, right in zip(buckets, buckets[1:])):
        raise PositionPathSegmentError("segment buckets must be contiguous")
    return normalized


def semantic_sha256(rows: Sequence[Mapping[str, object]]) -> str:
    """Hash the canonical logical rows independently of the selected codec."""

    normalized = canonicalize_rows(rows)
    payload = [
        [row[column] for column in OBSERVATION_COLUMNS]
        for row in normalized
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _null_bitmap(values: Sequence[object]) -> bytes:
    bitmap = bytearray((len(values) + 7) // 8)
    for index, value in enumerate(values):
        if value is None:
            bitmap[index // 8] |= 1 << (index % 8)
    return bytes(bitmap)


def _is_null(bitmap: bytes, index: int) -> bool:
    return bool(bitmap[index // 8] & (1 << (index % 8)))


def _column_payload(column: str, values: Sequence[object]) -> list[object]:
    """Encode one fixed-schema column as [constant, dictionary, values, nulls]."""

    nulls = _null_bitmap(values)
    non_null = [value for value in values if value is not None]
    constant = bool(values) and all(value == values[0] for value in values)
    if constant:
        # Keep the null flag even for an all-NULL constant column.
        return [1, None, [values[0]], nulls]
    dictionary: list[str] | None = None
    encoded: list[object]
    if column in _STRING_COLUMNS:
        dictionary = []
        positions: dict[str, int] = {}
        encoded = []
        for value in values:
            if value is None:
                encoded.append(0)
                continue
            text = str(value)
            index = positions.get(text)
            if index is None:
                index = len(dictionary)
                positions[text] = index
                dictionary.append(text)
            encoded.append(index)
    else:
        encoded = [0 if value is None else value for value in values]
    del non_null
    return [0, dictionary, encoded, nulls]


def _encode_uncompressed(rows: Sequence[Mapping[str, object]]) -> tuple[bytes, dict[str, object]]:
    normalized = canonicalize_rows(rows)
    first_bucket = int(normalized[0]["bucket_open_ms"])
    observed = [int(row["_observed_at_micros"]) for row in normalized]
    columns = [
        _column_payload(column, [row[column] for row in normalized])
        for column in _PAYLOAD_COLUMNS
    ]
    wire = [
        FORMAT_VERSION,
        BUCKET_STEP_MS,
        first_bucket,
        len(normalized),
        observed[0],
        [value - observed[index - 1] if index else 0 for index, value in enumerate(observed)],
        columns,
    ]
    raw = msgpack.packb(wire, use_bin_type=True, strict_types=True)
    metadata = {
        "position_id": str(normalized[0]["position_id"]),
        "first_bucket_open_ms": first_bucket,
        "last_bucket_open_ms": int(normalized[-1]["bucket_open_ms"]),
        "bucket_count": len(normalized),
        "available_count": sum(row["evidence_status"] == "available" for row in normalized),
        "unavailable_count": sum(row["evidence_status"] == "unavailable" for row in normalized),
        "min_observed_at": min(row["observed_at"] for row in normalized),
        "max_observed_at": max(row["observed_at"] for row in normalized),
        "semantic_sha256": semantic_sha256(normalized),
    }
    return raw, metadata


def encode_segment(
    rows: Sequence[Mapping[str, object]],
    *,
    position_id: str | None = None,
    segment_number: int = 0,
    codec_level: int = DEFAULT_CODEC_LEVEL,
    dictionary_id: str | None = None,
) -> EncodedPositionPathSegment:
    """Canonicalize, encode, compress, and hash one segment."""

    raw, metadata = _encode_uncompressed(rows)
    resolved_position_id = str(position_id or metadata["position_id"])
    if resolved_position_id != metadata["position_id"]:
        raise PositionPathSegmentError("position_id argument does not match source rows")
    level = max(1, min(int(codec_level), 22))
    compressed = zstandard.ZstdCompressor(level=level).compress(raw)
    if len(compressed) < len(raw):
        codec = "zstd"
        payload = compressed
        selected_level: int | None = level
    else:
        codec = "raw"
        payload = raw
        selected_level = None
    return EncodedPositionPathSegment(
        position_id=resolved_position_id,
        segment_number=int(segment_number),
        first_bucket_open_ms=int(metadata["first_bucket_open_ms"]),
        last_bucket_open_ms=int(metadata["last_bucket_open_ms"]),
        bucket_count=int(metadata["bucket_count"]),
        available_count=int(metadata["available_count"]),
        unavailable_count=int(metadata["unavailable_count"]),
        min_observed_at=str(metadata["min_observed_at"]),
        max_observed_at=str(metadata["max_observed_at"]),
        format_version=FORMAT_VERSION,
        codec=codec,
        codec_level=selected_level,
        dictionary_id=dictionary_id,
        uncompressed_bytes=len(raw),
        compressed_bytes=len(payload),
        semantic_sha256=str(metadata["semantic_sha256"]),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload=payload,
    )


def encode_segments(
    rows: Sequence[Mapping[str, object]],
    *,
    position_id: str | None = None,
    codec_level: int = DEFAULT_CODEC_LEVEL,
    dictionary_id: str | None = None,
) -> list[EncodedPositionPathSegment]:
    """Encode deterministic 2048-bucket chunks from one contiguous path."""

    normalized = canonicalize_rows(rows)
    resolved_position_id = str(position_id or normalized[0]["position_id"])
    return [
        encode_segment(
            normalized[offset : offset + MAX_BUCKETS_PER_SEGMENT],
            position_id=resolved_position_id,
            segment_number=offset // MAX_BUCKETS_PER_SEGMENT,
            codec_level=codec_level,
            dictionary_id=dictionary_id,
        )
        for offset in range(0, len(normalized), MAX_BUCKETS_PER_SEGMENT)
    ]


def _decompress(payload: bytes, *, codec: str, expected_bytes: int) -> bytes:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise PositionPathSegmentError("segment payload is not binary")
    stored = bytes(payload)
    if codec == "raw":
        raw = stored
    elif codec == "zstd":
        try:
            raw = zstandard.ZstdDecompressor().decompress(stored, max_output_size=max(1, int(expected_bytes)))
        except Exception as exc:
            raise PositionPathSegmentError("zstd payload cannot be decompressed") from exc
    else:
        raise PositionPathSegmentError(f"unsupported segment codec: {codec!r}")
    if len(raw) != int(expected_bytes):
        raise PositionPathSegmentError("segment uncompressed byte count mismatch")
    return raw


def decode_segment(
    segment: Mapping[str, object],
    *,
    expected_position_id: str | None = None,
) -> list[dict[str, object]]:
    """Verify and decode one SQLite segment row, failing closed on any mismatch."""

    format_version = str(segment.get("format_version") or "")
    if format_version != FORMAT_VERSION:
        raise PositionPathSegmentError(f"unsupported segment format: {format_version!r}")
    payload = segment.get("payload")
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise PositionPathSegmentError("segment payload is missing")
    payload_bytes = bytes(payload)
    if hashlib.sha256(payload_bytes).hexdigest() != str(segment.get("payload_sha256") or ""):
        raise PositionPathSegmentError("segment compressed-payload hash mismatch")
    raw = _decompress(
        payload_bytes,
        codec=str(segment.get("codec") or ""),
        expected_bytes=int(segment.get("uncompressed_bytes") or 0),
    )
    try:
        wire = msgpack.unpackb(raw, raw=False, strict_map_key=False)
    except Exception as exc:
        raise PositionPathSegmentError("segment MessagePack payload is invalid") from exc
    if not isinstance(wire, list) or len(wire) != 7:
        raise PositionPathSegmentError("segment payload shape is invalid")
    version, step, first_bucket, count, first_observed, observed_deltas, columns = wire
    if version != FORMAT_VERSION or int(step) != BUCKET_STEP_MS:
        raise PositionPathSegmentError("segment wire version or bucket step mismatch")
    count = int(count)
    if count <= 0 or count > MAX_BUCKETS_PER_SEGMENT or not isinstance(columns, list) or len(columns) != len(_PAYLOAD_COLUMNS):
        raise PositionPathSegmentError("segment row count or column count is invalid")
    position_id = str(segment.get("position_id") or "")
    if not position_id or (expected_position_id is not None and position_id != str(expected_position_id)):
        raise PositionPathSegmentError("segment position identity mismatch")
    if int(segment.get("bucket_count") or 0) != count:
        raise PositionPathSegmentError("segment bucket count mismatch")
    if not isinstance(observed_deltas, list) or len(observed_deltas) != count:
        raise PositionPathSegmentError("observed_at delta count mismatch")
    observed_micros: list[int] = []
    previous = int(first_observed)
    for index, delta in enumerate(observed_deltas):
        if index:
            previous += int(delta)
        observed_micros.append(previous)
    rows: list[dict[str, object]] = []
    for row_index in range(count):
        row: dict[str, object] = {
            "position_id": position_id,
            "bucket_open_ms": int(first_bucket) + row_index * BUCKET_STEP_MS,
            "observed_at": datetime.fromtimestamp(observed_micros[row_index] / 1_000_000, tz=timezone.utc).isoformat(),
        }
        for column, encoded_column in zip(_PAYLOAD_COLUMNS, columns):
            if not isinstance(encoded_column, list) or len(encoded_column) != 4:
                raise PositionPathSegmentError("segment column shape is invalid")
            is_constant, dictionary, values, nulls = encoded_column
            if not isinstance(nulls, (bytes, bytearray, memoryview)) or len(bytes(nulls)) != (count + 7) // 8:
                raise PositionPathSegmentError("segment null bitmap is invalid")
            bitmap = bytes(nulls)
            if int(is_constant):
                if not isinstance(values, list) or len(values) != 1:
                    raise PositionPathSegmentError("constant column value is invalid")
                decoded_values = [values[0]] * count
            else:
                if not isinstance(values, list) or len(values) != count:
                    raise PositionPathSegmentError("column value count mismatch")
                decoded_values = list(values)
            if column in _STRING_COLUMNS and dictionary is not None:
                if not isinstance(dictionary, list):
                    raise PositionPathSegmentError("string dictionary is invalid")
                converted: list[object] = []
                for value in decoded_values:
                    try:
                        index = int(value)
                        converted.append(dictionary[index])
                    except (IndexError, TypeError, ValueError) as exc:
                        raise PositionPathSegmentError("string dictionary index is invalid") from exc
                decoded_values = converted
            for index, value in enumerate(decoded_values):
                row_value = None if _is_null(bitmap, index) else value
                row[column] = _canonical_scalar(column, row_value)
        rows.append(row)
    if int(segment.get("first_bucket_open_ms") or -1) != rows[0]["bucket_open_ms"]:
        raise PositionPathSegmentError("segment first bucket mismatch")
    if int(segment.get("last_bucket_open_ms") or -1) != rows[-1]["bucket_open_ms"]:
        raise PositionPathSegmentError("segment last bucket mismatch")
    available = sum(row["evidence_status"] == "available" for row in rows)
    unavailable = sum(row["evidence_status"] == "unavailable" for row in rows)
    if available != int(segment.get("available_count") or 0) or unavailable != int(segment.get("unavailable_count") or 0):
        raise PositionPathSegmentError("segment availability totals mismatch")
    if semantic_sha256(rows) != str(segment.get("semantic_sha256") or ""):
        raise PositionPathSegmentError("segment semantic hash mismatch")
    if min(row["observed_at"] for row in rows) != str(segment.get("min_observed_at") or ""):
        raise PositionPathSegmentError("segment minimum observed_at mismatch")
    if max(row["observed_at"] for row in rows) != str(segment.get("max_observed_at") or ""):
        raise PositionPathSegmentError("segment maximum observed_at mismatch")
    return rows


def semantically_equal(
    left: Sequence[Mapping[str, object]], right: Sequence[Mapping[str, object]]
) -> bool:
    """Compare source and decoded paths under the frozen logical contract."""

    try:
        return semantic_sha256(left) == semantic_sha256(right) and canonicalize_rows(left) == canonicalize_rows(right)
    except PositionPathSegmentError:
        return False
