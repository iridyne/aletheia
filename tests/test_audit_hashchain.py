"""Tests for audit JSONL hash chain integrity."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """Redirect audit writes to a temp dir and reset module state."""
    import aletheia.files.jsonl as jmod

    monkeypatch.setattr(jmod, "_last_audit_hash", None)
    monkeypatch.setattr(jmod, "_log_dir", lambda: tmp_path)
    yield


def test_first_record_has_genesis_prev_hash(tmp_path):
    from aletheia.files.jsonl import write_audit, _GENESIS_HASH

    write_audit({"action": "test", "timestamp": "2026-01-01T00:00:00"})

    path = tmp_path / "audit.jsonl"
    record = json.loads(path.read_text("utf-8").strip())
    assert record["_prev_hash"] == _GENESIS_HASH


def test_chain_links_correctly(tmp_path):
    from aletheia.files.jsonl import write_audit, _hash_record

    write_audit({"action": "first", "timestamp": "2026-01-01T00:00:00"})
    write_audit({"action": "second", "timestamp": "2026-01-01T00:01:00"})

    path = tmp_path / "audit.jsonl"
    lines = path.read_text("utf-8").strip().splitlines()
    assert len(lines) == 2

    first_hash = _hash_record(lines[0])
    second = json.loads(lines[1])
    assert second["_prev_hash"] == first_hash


def test_verify_valid_chain(tmp_path):
    from aletheia.files.jsonl import write_audit, verify_audit_chain

    for i in range(5):
        write_audit({"action": f"event_{i}", "timestamp": f"2026-01-01T00:0{i}:00"})

    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 5
    assert result["errors"] == []


def test_verify_detects_tamper(tmp_path):
    from aletheia.files.jsonl import write_audit, verify_audit_chain

    for i in range(3):
        write_audit({"action": f"event_{i}", "timestamp": f"2026-01-01T00:0{i}:00"})

    # Tamper: modify the first line
    path = tmp_path / "audit.jsonl"
    lines = path.read_text("utf-8").strip().splitlines()
    tampered = json.loads(lines[0])
    tampered["action"] = "TAMPERED"
    lines[0] = json.dumps(tampered, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = verify_audit_chain()
    assert result["valid"] is False
    assert len(result["errors"]) >= 1


def test_verify_handles_legacy_records(tmp_path):
    """Records without _prev_hash (pre-hash-chain) should not break verification."""
    from aletheia.files.jsonl import verify_audit_chain

    path = tmp_path / "audit.jsonl"
    legacy = json.dumps({"action": "old", "timestamp": "2025-01-01T00:00:00"})
    path.write_text(legacy + "\n", encoding="utf-8")

    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 1


def test_verify_empty_file(tmp_path):
    from aletheia.files.jsonl import verify_audit_chain

    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 0


def test_concurrent_audit_writes_preserve_single_hash_chain(tmp_path):
    """Concurrent writers must not reuse the same previous hash."""
    from aletheia.files.jsonl import write_audit, verify_audit_chain

    def _write(i: int) -> None:
        write_audit({"action": f"event_{i}", "timestamp": f"2026-01-01T00:00:{i:02d}"})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write, range(20)))

    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 20

    lines = (tmp_path / "audit.jsonl").read_text("utf-8").strip().splitlines()
    prev_hashes = [json.loads(line)["_prev_hash"] for line in lines]
    assert len(prev_hashes) == len(set(prev_hashes))


def test_verify_audit_chain_spans_rotated_files(tmp_path, monkeypatch):
    """The first current-file record may link to the last rotated record."""
    import aletheia.files.jsonl as jmod
    from aletheia.files.jsonl import write_audit, verify_audit_chain

    monkeypatch.setattr(jmod, "_max_bytes", lambda: 1)

    for i in range(4):
        write_audit({"action": f"event_{i}", "timestamp": f"2026-01-01T00:00:{i:02d}"})

    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl").exists()
    result = verify_audit_chain()
    assert result["valid"] is True
    assert result["records"] == 4
