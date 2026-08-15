from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3

import msgpack
import pytest
import zstandard
from typer.testing import CliRunner
from aletheia.cli.inspect_cmd import inspect_app
from aletheia.storage import repositories as repo
from aletheia.storage import position_path_archive as archive_codec
from aletheia.storage.models import Position
from aletheia.storage.position_path_archive import (
    RAW_CODEC_VERSION,
    RAW_CODEC,
    RAW_CODEC_LEVEL,
    SEGMENT_MS,
    PositionPathArchiveError,
    decode_segment,
    encode_segment,
    segment_start_for_bucket,
)
from scripts.shadow_position_path_archive_fedora_stream import shadow_stream


_CLI_RUNNER = CliRunner()


def _row(
    position_id: str,
    bucket_open_ms: int,
    *,
    close: float = 100.0,
    evidence_status: str = "available",
    evidence_reason: str = "complete_exact_5m_bar",
    producer_commit: str = "commit-1",
    runtime_session_id: str = "session-1",
    exit_plan_version: int = 0,
) -> dict:
    result = {
        "position_id": position_id,
        "bucket_open_ms": bucket_open_ms,
        "observed_at": "2026-08-15T00:00:00+00:00",
        "venue": "bitget",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "direction": "long",
        "entry_price": 100.0,
        "quantity": 0.01,
        "bar_open": close - 1.0,
        "bar_high": close + 1.0,
        "bar_low": close - 2.0,
        "bar_close": close,
        "bar_volume": 12.0,
        "bar_provider": "test",
        "mark_price": close,
        "unrealized_pnl": 0.0,
        "entry_fee": 0.01,
        "funding_cost": 0.0,
        "funding_source": "local_paper_funding_v2",
        "stop_price": 95.0,
        "take_profit_price": 110.0,
        "exit_plan_version": exit_plan_version,
        "evidence_status": evidence_status,
        "evidence_reason": evidence_reason,
        "producer_commit": producer_commit,
        "runtime_session_id": runtime_session_id,
        "capture_mode": "open_snapshot",
    }
    if evidence_status == "unavailable":
        for field in (
            "bar_open",
            "bar_high",
            "bar_low",
            "bar_close",
            "bar_volume",
            "bar_provider",
        ):
            result[field] = None
    return result


def test_segment_encoding_is_deterministic_and_rejects_truncation():
    rows = [_row("position-archive", 300_000), _row("position-archive", 0)]
    first = encode_segment(
        rows,
        position_id="position-archive",
        segment_start_ms=0,
    )
    second = encode_segment(
        [dict(reversed(list(row.items()))) for row in reversed(rows)],
        position_id="position-archive",
        segment_start_ms=0,
    )

    assert first.payload == second.payload
    assert first.content_hash == second.content_hash
    decoded = decode_segment(first.payload)
    assert [row["bucket_open_ms"] for row in decoded["rows"]] == [0, 300_000]
    assert decoded["raw_bytes"] == first.raw_bytes
    with pytest.raises(PositionPathArchiveError):
        decode_segment(first.payload[:-1])
    with pytest.raises(PositionPathArchiveError):
        decode_segment(first.payload + b"trailing-corruption")
    malformed = msgpack.unpackb(
        zstandard.ZstdDecompressor().decompress(first.payload), raw=False
    )
    malformed[9][0] = 2**63 - 1
    with pytest.raises(PositionPathArchiveError):
        decode_segment(
            zstandard.ZstdCompressor().compress(
                msgpack.packb(malformed, use_bin_type=True)
            )
        )
    malformed = msgpack.unpackb(
        zstandard.ZstdDecompressor().decompress(first.payload), raw=False
    )
    malformed[8] = [0, 1]
    with pytest.raises(PositionPathArchiveError, match="contiguous"):
        decode_segment(
            zstandard.ZstdCompressor().compress(
                msgpack.packb(malformed, use_bin_type=True)
            )
        )


def test_raw_msgpack_fallback_is_strictly_self_describing(monkeypatch):
    encoded = encode_segment(
        [_row("position-raw", 0), _row("position-raw", 300_000)],
        position_id="position-raw",
        segment_start_ms=0,
    )
    raw_payload = zstandard.ZstdDecompressor().decompress(encoded.payload)
    with pytest.raises(PositionPathArchiveError, match="zstd is larger"):
        decode_segment(
            raw_payload,
            codec=RAW_CODEC,
            codec_level=RAW_CODEC_LEVEL,
        )

    class LargerZstd:
        def __init__(self, *, level):
            self.level = level

        def compress(self, payload):
            return b"zstd-is-larger" + payload

    monkeypatch.setattr(archive_codec.zstandard, "ZstdCompressor", LargerZstd)
    decoded = decode_segment(
        raw_payload,
        codec=RAW_CODEC,
        codec_level=RAW_CODEC_LEVEL,
    )
    assert decoded["rows"]
    assert decoded["codec"] == RAW_CODEC
    assert decoded["codec_level"] == RAW_CODEC_LEVEL
    assert decoded["content_hash"] == encoded.content_hash
    with pytest.raises(PositionPathArchiveError, match="codec"):
        decode_segment(raw_payload, codec=RAW_CODEC, codec_level=3)
    with pytest.raises(PositionPathArchiveError, match="codec version"):
        decode_segment(encoded.payload, codec_version="msgpack+zstd/v0")


def test_decoder_rechecks_the_source_evidence_contract():
    encoded = encode_segment(
        [_row("position-contract-decode", 0)],
        position_id="position-contract-decode",
        segment_start_ms=0,
    )
    envelope = msgpack.unpackb(
        zstandard.ZstdDecompressor().decompress(encoded.payload), raw=False
    )
    evidence_reason = next(
        descriptor for descriptor in envelope[11] if descriptor[0] == 24
    )
    evidence_reason[2] = "corrupted_reason"
    corrupted = zstandard.ZstdCompressor().compress(
        msgpack.packb(envelope, use_bin_type=True)
    )
    with pytest.raises(PositionPathArchiveError, match="evidence_reason"):
        decode_segment(corrupted)


def test_segment_boundaries_are_fixed_bucket_windows_not_utc_days():
    assert segment_start_for_bucket(0) == 0
    assert segment_start_for_bucket(24 * 60 * 60 * 1000 - 300_000) == 0
    assert segment_start_for_bucket(SEGMENT_MS) == SEGMENT_MS
    with pytest.raises(PositionPathArchiveError, match="contiguous"):
        encode_segment(
            [_row("position-sparse", 0), _row("position-sparse", 600_000)],
            position_id="position-sparse",
            segment_start_ms=0,
        )


def test_long_path_splits_only_at_fixed_2048_bucket_boundary():
    rows = [
        _row("position-over-2048", bucket)
        for bucket in range(0, 2049 * 300_000, 300_000)
    ]
    first = encode_segment(
        rows[:2048],
        position_id="position-over-2048",
        segment_start_ms=0,
    )
    second = encode_segment(
        rows[2048:],
        position_id="position-over-2048",
        segment_start_ms=SEGMENT_MS,
    )

    assert first.row_count == 2048
    assert first.first_bucket_open_ms == 0
    assert first.last_bucket_open_ms == SEGMENT_MS - 300_000
    assert second.row_count == 1
    assert second.first_bucket_open_ms == SEGMENT_MS
    assert second.segment_number == 1


def test_segment_supports_unavailable_rows_long_paths_and_wrong_versions():
    boundary = SEGMENT_MS
    rows = [
        _row("position-boundary", 0),
        _row(
            "position-boundary",
            300_000,
            evidence_status="unavailable",
            evidence_reason="market_data_fetch_failed",
            producer_commit="commit-2",
            runtime_session_id="session-2",
            exit_plan_version=1,
        ),
    ]
    rows[1]["market_type"] = "legacy_market"
    rows[1]["direction"] = "legacy_direction"
    encoded = encode_segment(
        rows,
        position_id="position-boundary",
        segment_start_ms=0,
        segment_end_ms=boundary,
    )
    decoded = decode_segment(encoded.payload)
    assert decoded["available_count"] == 1
    assert decoded["unavailable_count"] == 1
    assert decoded["rows"][1]["evidence_reason"] == "market_data_fetch_failed"
    assert decoded["rows"][1]["market_type"] == "legacy_market"
    assert decoded["rows"][1]["direction"] == "legacy_direction"
    assert decoded["rows"][1]["producer_commit"] == "commit-2"
    assert decoded["rows"][1]["runtime_session_id"] == "session-2"
    assert decoded["rows"][1]["exit_plan_version"] == 1
    tail = encode_segment(
        [_row("position-boundary", boundary - 300_000)],
        position_id="position-boundary",
        segment_start_ms=0,
        segment_end_ms=boundary,
    )
    assert decode_segment(tail.payload)["first_bucket_open_ms"] == boundary - 300_000
    with pytest.raises(PositionPathArchiveError):
        encode_segment(
            [_row("position-boundary", boundary)],
            position_id="position-boundary",
            segment_start_ms=0,
            segment_end_ms=boundary,
        )

    long_rows = [
        _row("position-long", bucket)
        for bucket in range(0, 24 * 60 * 60 * 1000, 300_000)
    ]
    long_segment = encode_segment(
        long_rows,
        position_id="position-long",
        segment_start_ms=0,
        segment_end_ms=boundary,
    )
    assert long_segment.row_count == 288
    max_segment = encode_segment(
        [_row("position-max", bucket) for bucket in range(0, 2048 * 300_000, 300_000)],
        position_id="position-max",
        segment_start_ms=0,
        segment_end_ms=2048 * 300_000,
    )
    assert max_segment.row_count == 2048
    wrong_version = zstandard.ZstdCompressor().compress(
        msgpack.packb({"schema": "position_path_segment/v0"}, use_bin_type=True)
    )
    with pytest.raises(PositionPathArchiveError):
        decode_segment(wrong_version)
    bool_version = msgpack.unpackb(
        zstandard.ZstdDecompressor().decompress(encoded.payload), raw=False
    )
    bool_version[1] = True
    with pytest.raises(PositionPathArchiveError):
        decode_segment(
            zstandard.ZstdCompressor().compress(
                msgpack.packb(bool_version, use_bin_type=True)
            )
        )


def test_codec_preserves_null_zero_and_float64_values():
    rows = [_row("position-null-zero", 0), _row("position-null-zero", 300_000)]
    rows[0]["funding_cost"] = None
    rows[1]["funding_cost"] = 0.0
    rows[1]["quantity"] = 0.005
    rows[1]["bar_close"] = 100.12345678901234

    encoded = encode_segment(
        rows,
        position_id="position-null-zero",
        segment_start_ms=0,
    )
    decoded = decode_segment(encoded.payload)["rows"]

    assert decoded[0]["funding_cost"] is None
    assert decoded[1]["funding_cost"] == 0.0
    assert decoded[1]["quantity"] == 0.005
    assert decoded[1]["bar_close"] == 100.12345678901234


def test_codec_preserves_quantity_changes_and_exit_plan_revisions():
    rows = [
        _row("position-partial-reduction", 0, exit_plan_version=1),
        _row("position-partial-reduction", 300_000, exit_plan_version=2),
    ]
    rows[1]["quantity"] = 0.005
    rows[1]["exit_plan_version"] = 2

    encoded = encode_segment(
        rows,
        position_id="position-partial-reduction",
        segment_start_ms=0,
    )

    assert decode_segment(encoded.payload)["rows"] == rows


def test_encoder_preserves_the_v26_available_unavailable_contract():
    invalid_available = _row("position-contract", 0)
    invalid_available["evidence_reason"] = "market_data_fetch_failed"
    with pytest.raises(PositionPathArchiveError, match="evidence_reason"):
        encode_segment(
            [invalid_available],
            position_id="position-contract",
            segment_start_ms=0,
        )

    invalid_unavailable = _row(
        "position-contract", 0, evidence_status="unavailable"
    )
    invalid_unavailable["bar_close"] = 100.0
    with pytest.raises(PositionPathArchiveError, match="bar values"):
        encode_segment(
            [invalid_unavailable],
            position_id="position-contract",
            segment_start_ms=0,
        )

    invalid_status = _row("position-contract", 0)
    invalid_status["evidence_status"] = "pending"
    with pytest.raises(PositionPathArchiveError, match="evidence_status"):
        encode_segment(
            [invalid_status],
            position_id="position-contract",
            segment_start_ms=0,
        )

    invalid_mark = _row("position-contract", 0)
    invalid_mark["mark_price"] = -1.0
    with pytest.raises(PositionPathArchiveError, match="mark_price"):
        encode_segment(
            [invalid_mark],
            position_id="position-contract",
            segment_start_ms=0,
        )

    invalid_numeric_type = _row("position-contract", 0)
    invalid_numeric_type["quantity"] = True
    with pytest.raises(PositionPathArchiveError, match="not boolean"):
        encode_segment(
            [invalid_numeric_type],
            position_id="position-contract",
            segment_start_ms=0,
        )


def test_as_of_filters_rows_inside_one_archived_segment_and_preserves_timestamp_text():
    position = Position(
        id="position-archive-as-of-late",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    rows = [_row(position.id, bucket) for bucket in (0, 300_000, 600_000)]
    rows[0]["observed_at"] = "2026-08-15T00:00:00Z"
    rows[1]["observed_at"] = "2026-08-15T00:10:00+00:00"
    rows[2]["observed_at"] = "2026-08-15T00:03:00+00:00"
    for row in rows:
        assert repo.insert_position_path_observation(row)

    repo.archive_position_path_segments(position.id)

    visible = repo.list_position_path_observations_unified(
        position.id,
        as_of="2026-08-15T00:05:00+00:00",
    )
    assert [row["bucket_open_ms"] for row in visible] == [0, 600_000]
    assert visible[0]["observed_at"] == "2026-08-15T00:00:00Z"


def test_payload_uses_fixed_columns_deltas_dictionaries_and_null_bitmaps():
    rows = [_row("position-codec-shape", 0), _row("position-codec-shape", 300_000)]
    rows[0]["funding_cost"] = None
    rows[1]["funding_cost"] = 0.0
    rows[0]["producer_commit"] = "commit-a"
    rows[1]["producer_commit"] = "commit-b"

    encoded = encode_segment(
        rows,
        position_id="position-codec-shape",
        segment_start_ms=0,
    )
    envelope = msgpack.unpackb(
        zstandard.ZstdDecompressor().decompress(encoded.payload), raw=False
    )

    assert isinstance(envelope, list)
    assert envelope[0] == "position_path_segment/v1"
    assert envelope[2] == 300_000
    assert envelope[6] == 0
    assert envelope[8] is None
    assert len(envelope[11]) == 25
    assert envelope[10]
    assert any(descriptor[1] == 1 for descriptor in envelope[11])
    assert any(
        descriptor[1] == 3
        and isinstance(descriptor[2], bytes)
        for descriptor in envelope[11]
    )


def test_archive_is_idempotent_and_unified_reader_preserves_hot_rows():
    position = Position(
        id="position-archive-db",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        assert repo.insert_position_path_observation(_row(position.id, bucket))

    first = repo.archive_position_path_segments(position.id)
    second = repo.archive_position_path_segments(position.id)

    assert first["segments_created"] == 1
    assert second["segments_created"] == 0
    assert second["segments_existing"] == 1
    assert len(repo.list_archived_position_path_observations(position.id)) == 3
    assert len(repo.list_position_path_observations_unified(position.id)) == 3
    assert repo.count_position_path_observations_since(0) == {
        "available": 3,
        "unavailable": 0,
    }
    assert repo.count_position_path_observations_since(900_000) == {
        "available": 0,
        "unavailable": 0,
    }
    assert (
        repo.list_position_path_observations_unified(
            position.id, as_of="2026-08-14T23:59:59+00:00"
        )
        == []
    )
    assert len(
        repo.list_position_path_observations_unified(
            position.id, as_of="2026-08-16T00:00:00+00:00"
        )
    ) == 3
    assert len(repo.list_position_path_observations(position.id)) == 3
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_path_archive_segments WHERE position_id=?",
        (position.id,),
    ).fetchone()[0] == 1
    archive_row = repo.get_db().execute(
        "SELECT segment_number, first_bucket_open_ms, last_bucket_open_ms, "
        "available_count, unavailable_count, "
        "min_observed_at, max_observed_at, payload_hash, sealed_at "
        "FROM position_path_archive_segments WHERE position_id=?",
        (position.id,),
    ).fetchone()
    assert archive_row["segment_number"] == 0
    assert archive_row["first_bucket_open_ms"] == 0
    assert archive_row["last_bucket_open_ms"] == 600_000
    assert archive_row["available_count"] == 3
    assert archive_row["unavailable_count"] == 0
    assert archive_row["min_observed_at"] == archive_row["max_observed_at"]
    assert archive_row["payload_hash"]
    assert archive_row["sealed_at"]
    seal_event = next(
        event
        for event in repo.list_audit_events(limit=100)
        if event["event_type"] == "position_path_archive_sealed"
    )
    seal_message = json.loads(seal_event["message"])
    assert seal_message["row_count"] == 3
    assert seal_message["payload_hash"] == archive_row["payload_hash"]

    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.get_db().execute(
            "UPDATE position_path_archive_segments SET row_count=0 WHERE position_id=?",
            (position.id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        repo.get_db().execute(
            "DELETE FROM position_path_archive_segments WHERE position_id=?",
            (position.id,),
        )
    db = repo.get_db()
    db.execute("DROP TRIGGER trg_position_path_observations_no_delete")
    with pytest.raises(
        sqlite3.IntegrityError, match="deletion is disabled until exact archive compaction"
    ):
        db.execute(
            "DELETE FROM position_path_observations WHERE position_id=?",
            (position.id,),
        )
    db.rollback()
    assert len(repo.list_position_path_observations(position.id)) == 3
    db.execute("DROP TRIGGER trg_position_path_observation_no_unverified_delete")
    db.execute(
        "DELETE FROM position_path_observations WHERE position_id=?",
        (position.id,),
    )
    db.commit()
    assert repo.list_position_path_observations(position.id) == []
    assert len(repo.list_position_path_observations_unified(position.id)) == 3
    assert len(repo.list_position_path_observations_unified(position.id)) == 3


def test_persisted_raw_codec_is_read_with_recorded_metadata(monkeypatch):
    position = Position(
        id="position-archive-raw-persisted",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)

    db = repo.get_db()
    raw_payload = zstandard.ZstdDecompressor().decompress(
        db.execute(
            "SELECT payload FROM position_path_archive_segments WHERE position_id=?",
            (position.id,),
        ).fetchone()["payload"]
    )
    db.execute("DROP TRIGGER trg_position_path_archive_no_update")
    db.execute(
        "UPDATE position_path_archive_segments SET codec='msgpack', codec_version=?, codec_level=0, "
        "payload_bytes=?, payload_hash=?, payload=? WHERE position_id=?",
        (
            RAW_CODEC_VERSION,
            len(raw_payload),
            hashlib.sha256(raw_payload).hexdigest(),
            sqlite3.Binary(raw_payload),
            position.id,
        ),
    )
    db.commit()

    class LargerZstd:
        def __init__(self, *, level):
            self.level = level

        def compress(self, payload):
            return b"zstd-is-larger" + payload

    monkeypatch.setattr(archive_codec.zstandard, "ZstdCompressor", LargerZstd)
    rows = repo.list_archived_position_path_observations(position.id)
    assert [row["bucket_open_ms"] for row in rows] == [0, 300_000, 600_000]


def test_archived_reader_rejects_hash_mismatch():
    position = Position(
        id="position-archive-hash",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)

    db = repo.get_db()
    db.execute("DROP TRIGGER trg_position_path_archive_no_update")
    db.execute(
        "UPDATE position_path_archive_segments SET payload_hash=? "
        "WHERE position_id=?",
        ("0" * 64, position.id),
    )
    db.commit()
    with pytest.raises(ValueError, match="archive integrity mismatch"):
        repo.list_archived_position_path_observations(position.id)


def test_as_of_skip_still_rejects_truncated_payload():
    position = Position(
        id="position-archive-as-of-corrupt",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)

    db = repo.get_db()
    db.execute("DROP TRIGGER trg_position_path_archive_no_update")
    db.execute(
        "UPDATE position_path_archive_segments SET min_observed_at=? "
        "WHERE position_id=?",
        ("2099-01-01T00:00:00+00:00", position.id),
    )
    db.execute(
        "UPDATE position_path_archive_segments SET payload=? "
        "WHERE position_id=?",
        (sqlite3.Binary(b"truncated"), position.id),
    )
    db.commit()
    with pytest.raises(ValueError, match="archive integrity mismatch"):
        repo.list_archived_position_path_observations(
            position.id,
            as_of="2026-08-15T00:00:00+00:00",
        )


def test_as_of_skip_still_rejects_semantic_hash_mismatch():
    position = Position(
        id="position-archive-as-of-hash-corrupt",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)

    db = repo.get_db()
    db.execute("DROP TRIGGER trg_position_path_archive_no_update")
    db.execute(
        "UPDATE position_path_archive_segments "
        "SET min_observed_at=?, content_hash=? WHERE position_id=?",
        ("2099-01-01T00:00:00+00:00", "0" * 64, position.id),
    )
    db.commit()
    with pytest.raises(ValueError, match="archive integrity mismatch"):
        repo.list_archived_position_path_observations(
            position.id,
            as_of="2026-08-15T00:00:00+00:00",
        )


def test_archive_seal_rolls_back_on_audit_failure(monkeypatch):
    position = Position(
        id="position-archive-rollback",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))

    def fail_audit(_event):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(repo, "insert_audit_event", fail_audit)
    with pytest.raises(RuntimeError, match="audit write failed"):
        repo.archive_position_path_segments(position.id)
    assert (
        repo.get_db()
        .execute(
            "SELECT COUNT(*) FROM position_path_archive_segments WHERE position_id=?",
            (position.id,),
        )
        .fetchone()[0]
        == 0
    )


def test_hot_observation_delete_requires_verified_archive():
    position = Position(
        id="position-archive-delete-guard",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        funding_source="local_paper_funding_v2",
    )
    repo.insert_position(position)
    assert repo.insert_position_path_observation(_row(position.id, 0))

    with pytest.raises(
        sqlite3.IntegrityError, match="deletion is disabled until exact archive compaction"
    ):
        repo.get_db().execute(
            "DELETE FROM position_path_observations WHERE position_id=?",
            (position.id,),
        )
    with pytest.raises(ValueError, match="not prunable"):
        repo._delete_old_rows("position_path_observations", days=1)


def test_archive_skips_closed_paths_with_deferred_buckets():
    position = Position(
        id="position-archive-deferred",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        assert repo.insert_position_path_observation(_row(position.id, bucket))
    repo.get_db().execute(
        "INSERT INTO position_path_observation_deferred_buckets "
        "(position_id, bucket_open_ms, first_deferred_at, last_attempt_at, "
        "attempt_count, reason) VALUES (?,?,?,?,?,?)",
        (
            position.id,
            600_000,
            "2026-08-15T00:00:00+00:00",
            "2026-08-15T00:00:00+00:00",
            1,
            "market_data_fetch_failed",
        ),
    )
    repo.get_db().commit()

    result = repo.archive_position_path_segments(position.id)

    assert result["segments_created"] == 0
    assert result["positions_skipped"] == 1
    assert result["skip_reasons"] == {"deferred_buckets_remain": 1}


def test_bounded_shadow_selection_still_validates_and_seals_full_path():
    position = Position(
        id="position-archive-bounded",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))

    result = repo.archive_position_path_segments(
        position.id, start_ms=0, end_ms=300_000
    )

    assert result["segments_created"] == 1
    assert len(repo.list_archived_position_path_observations(position.id)) == 3


def test_archive_rejects_unexpected_buckets():
    position = Position(
        id="position-archive-extra",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000, 900_000):
        repo.insert_position_path_observation(_row(position.id, bucket))

    result = repo.archive_position_path_segments(position.id)

    assert result["segments_created"] == 0
    assert result["skip_reasons"] == {"unexpected_buckets": 1}


def test_bucket_state_uses_exact_archived_rows_not_whole_day(monkeypatch):
    position = Position(
        id="position-archive-state",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)
    monkeypatch.setattr(
        repo,
        "list_archived_position_path_observations",
        lambda *args, **kwargs: pytest.fail("observer state must use archive metadata"),
    )

    state = repo.get_position_path_bucket_state([position.id])[position.id]

    assert state["observed"] == {0, 300_000, 600_000}


def test_observed_position_lookup_uses_exact_archive_bucket_bounds(monkeypatch):
    position = Position(
        id="position-archive-exact-bucket",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, 0, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 20, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (300_000, 600_000, 900_000):
        assert repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)
    monkeypatch.setattr(
        repo,
        "list_archived_position_path_observations",
        lambda *args, **kwargs: pytest.fail("observer lookup must use archive metadata"),
    )

    assert repo.get_observed_position_ids_for_bucket(
        [position.id], bucket_open_ms=0
    ) == set()
    assert repo.get_observed_position_ids_for_bucket(
        [position.id], bucket_open_ms=600_000
    ) == {position.id}


def test_compaction_dry_run_plans_without_writing_or_deleting():
    position = Position(
        id="position-archive-dry-run",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))

    result = repo.archive_position_path_segments(
        position.id, dry_run=True, limit=1
    )

    assert result["dry_run"] is True
    assert result["segments_planned"] == 1
    assert result["hot_rows_covered"] == 3
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_path_archive_segments WHERE position_id=?",
        (position.id,),
    ).fetchone()[0] == 0
    assert len(repo.list_position_path_observations(position.id)) == 3


def test_position_path_cli_json_and_human_use_unified_archive_reader():
    position = Position(
        id="position-archive-cli",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=100.0,
        execution_mode="paper_internal",
        opened_at=datetime(1970, 1, 1, tzinfo=timezone.utc).isoformat(),
        closed_at=datetime(1970, 1, 1, 0, 15, tzinfo=timezone.utc).isoformat(),
        status="closed",
    )
    repo.insert_position(position)
    for bucket in (0, 300_000, 600_000):
        repo.insert_position_path_observation(_row(position.id, bucket))
    repo.archive_position_path_segments(position.id)

    json_result = _CLI_RUNNER.invoke(
        inspect_app,
        ["position-path", "--id", position.id, "--json"],
    )
    assert json_result.exit_code == 0, json_result.stdout
    assert len(json.loads(json_result.stdout)["observations"]) == 3

    human_result = _CLI_RUNNER.invoke(
        inspect_app,
        ["position-path", "--id", position.id],
    )
    assert human_result.exit_code == 0, human_result.stdout
    assert position.id in human_result.stdout

    cutoff_result = _CLI_RUNNER.invoke(
        inspect_app,
        [
            "position-path",
            "--id",
            position.id,
            "--as-of",
            "2026-08-14T23:59:59+00:00",
            "--json",
        ],
    )
    assert cutoff_result.exit_code == 0, cutoff_result.stdout
    cutoff_report = json.loads(cutoff_result.stdout)
    assert cutoff_report["as_of_at"] == "2026-08-14T23:59:59+00:00"
    assert cutoff_report["observations"] == []


def test_read_only_fedora_stream_shadow_roundtrips_without_database_writes():
    position_id = "position-archive-stream"
    report = shadow_stream(
        {
            "positions": [
                {
                    "id": position_id,
                    "status": "closed",
                    "opened_at": "1970-01-01T00:00:00+00:00",
                    "closed_at": "1970-01-01T00:15:00+00:00",
                }
            ],
            "deferred": [],
            "rows": [_row(position_id, bucket) for bucket in (0, 300_000, 600_000)],
        }
    )

    assert report["status"] == "passed"
    assert report["archive_persisted"] is False
    assert report["rows_verified"] == 3
    assert report["deterministic_byte_identity"] is True
    assert report["as_of_parity"] is True
    assert report["candidate_benchmark"]["status"] == "available"
    assert "position_path_segment/v1" in report["candidate_benchmark"]["candidates"]
    assert report["production_codec_timing_ms"]["decode_p95"] is not None
    assert report["acceptance"]["status"] == "incomplete"
    assert "sample_coverage" in report
    assert report["sample_coverage"]["short_1_to_10_buckets"] is True
    assert report["representative_input_complete"] is False
    assert "quantity_variation_scale_in_or_partial_signal" in report[
        "representative_input_missing"
    ]
    assert shadow_stream({"positions": [], "deferred": [], "rows": []})["status"] == "insufficient_data"


def test_read_only_fedora_stream_rejects_duplicate_buckets():
    position_id = "position-archive-stream-duplicate"
    row = _row(position_id, 0)
    report = shadow_stream(
        {
            "positions": [
                {
                    "id": position_id,
                    "status": "closed",
                    "opened_at": "1970-01-01T00:00:00+00:00",
                    "closed_at": "1970-01-01T00:05:00+00:00",
                }
            ],
            "deferred": [],
            "rows": [row, dict(row)],
        }
    )

    assert report["status"] == "insufficient_data"
    assert report["skips"] == {"duplicate_buckets": 1}
