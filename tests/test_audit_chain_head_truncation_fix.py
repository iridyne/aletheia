"""Focused regression for #43 — head truncation of the oldest audit file
must no longer read as a benign rotation reanchor.

The old verify_audit_chain treated ANY first-line-in-file _prev_hash
mismatch as "first record after file rotation boundary → benign". That
tolerated head truncation of the very first file — an attacker (or a
misuse of `head`, `truncate`, or a corrupted disk read) could chop the
oldest records off and the verifier would silently reanchor.

Now the reanchor is only allowed when the current file has a PRIOR file
in the chronological list. The oldest file's first record must chain
from _GENESIS_HASH.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None

    sq.reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _seed_chain(tmp_path: Path) -> Path:
    """Write three audit events to a single file so we have a real chain to
    verify. Uses the production write_audit path so hashes chain correctly."""
    from aletheia.files.jsonl import write_audit

    for i in range(3):
        write_audit({
            "actor": "system",
            "source": "test",
            "event_type": "test_event",
            "entity_type": "test",
            "entity_id": f"e{i}",
            "message": f"seeded event {i}",
        })
    return tmp_path / "logs" / "audit.jsonl"


def test_head_truncation_of_oldest_file_now_reads_as_error(tmp_path, monkeypatch):
    """#43: dropping the FIRST line of the oldest audit file used to be
    reported as a benign reanchor. Now it lands in errors."""
    _setup_env(tmp_path, monkeypatch)
    audit_path = _seed_chain(tmp_path)

    from aletheia.files.jsonl import verify_audit_chain

    # Baseline: full chain verifies clean.
    baseline = verify_audit_chain()
    assert baseline["valid"], f"baseline chain should verify clean; got {baseline}"
    assert baseline["records"] == 3
    assert baseline["reanchors"] == 0

    # Truncate the head — drop the first line.
    original_lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert len(original_lines) == 3
    audit_path.write_text("\n".join(original_lines[1:]) + "\n", encoding="utf-8")

    tampered = verify_audit_chain()
    assert not tampered["valid"], (
        "head-truncation of the oldest audit file must NOT verify clean; "
        f"got {tampered}"
    )
    assert tampered["errors"], "at least one error must be reported"
    # Not a benign reanchor — this is a real integrity failure.
    assert tampered["reanchors"] == 0


def test_legitimate_rotation_still_reanchors_benignly(tmp_path, monkeypatch):
    """#43 fix must NOT break the legitimate rotation-boundary reanchor path.

    Manually construct the shape: audit.jsonl.1 with a valid chain, and a
    fresh audit.jsonl whose first record's `_prev_hash` = GENESIS (i.e. it
    doesn't chain from the .1 tail — historically this happened when the
    in-memory hash cache was reset between rotation and the next write).
    That boundary mismatch MUST still be a benign reanchor because there
    IS a prior file in the chronological list."""
    _setup_env(tmp_path, monkeypatch)
    _seed_chain(tmp_path)

    audit_path = tmp_path / "logs" / "audit.jsonl"
    rotated_path = tmp_path / "logs" / "audit.jsonl.1"
    audit_path.rename(rotated_path)

    # Hand-write a fresh audit.jsonl chained from GENESIS (bypasses
    # write_audit's `use_cache=False` disk re-read).
    from aletheia.files.jsonl import _GENESIS_HASH
    fresh_record = {
        "actor": "system", "source": "test", "event_type": "test_event",
        "entity_type": "test", "entity_id": "post-rotation",
        "message": "after rotation",
        "_prev_hash": _GENESIS_HASH,
    }
    audit_path.write_text(json.dumps(fresh_record) + "\n", encoding="utf-8")

    from aletheia.files.jsonl import verify_audit_chain
    result = verify_audit_chain()

    assert result["records"] == 4
    # audit.jsonl.1 chains from GENESIS (oldest → no head-trunc error);
    # audit.jsonl's first record chains from GENESIS too but the running
    # prev_hash is now the .1 tail — that's the benign rotation reanchor.
    assert result["reanchors"] == 1, (
        f"legitimate rotation must produce exactly one benign reanchor; got {result}"
    )
    assert not result["errors"], (
        f"legitimate rotation must NOT produce errors; got {result['errors']}"
    )
