"""Focused regressions for B7-io-honesty batch (#62/#162, #63, #64).

- #62 / #162 (duplicate): proposal-pack export raised on missing proposal_id
  but then dumped EVERY trade_proposal (and every serenity_theme / evidence /
  claim) into the bundle — the pack label lied. Now the pack contains exactly
  the named proposal and only the serenity rows in its opportunity/theme chain.
- #63: dry-run/merge import silently skipped files listed in checksums.txt
  that were missing on disk (the `if file_path.exists()` fell through with no
  error append). The bundle was accepted as clean when it wasn't.
- #64: an existing primary key with a DIVERGED payload was counted as
  `skipped_duplicates` instead of populating the `ImportConflict` class the
  file already defined — the operator never learned the bundle was out of sync.
"""

from __future__ import annotations

import json
import sqlite3
import tarfile
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


# ---------------------------------------------------------------------------
# #62 / #162 — proposal-pack filter
# ---------------------------------------------------------------------------


def _seed_two_proposals() -> tuple[str, str]:
    """Insert two distinct proposals so the filter is testable — a proposal-pack
    for the first must not include the second's row."""
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.repositories import insert_trade_proposal

    p1 = TradeProposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        order_type="market", quantity=1.0, price=100.0,
        execution_mode="bitget_demo", agent_rationale="p1 target",
        status="approved",
    )
    p2 = TradeProposal(
        symbol="ETH/USDT", market_type="usdt_futures", direction="short",
        order_type="market", quantity=2.0, price=200.0,
        execution_mode="bitget_demo", agent_rationale="p2 other",
        status="approved",
    )
    insert_trade_proposal(p1)
    insert_trade_proposal(p2)
    return p1.id, p2.id


def _read_bundle_jsonl(archive_path: Path, jsonl_name: str) -> list[dict]:
    """Extract the archive, find the bundle root, read the JSONL rows."""
    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(archive_path.parent / "unpacked", filter="data")
    unpacked = archive_path.parent / "unpacked"
    bundle_root = next(d for d in unpacked.iterdir() if d.is_dir())
    target = bundle_root / "db" / jsonl_name
    if not target.exists():
        return []
    return [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_proposal_pack_exports_only_the_named_proposal(tmp_path, monkeypatch):
    """#62/#162: proposal-pack now filters trade_proposals by id."""
    _setup_env(tmp_path, monkeypatch)
    p1_id, p2_id = _seed_two_proposals()

    from aletheia.sync.exporter import ExportService
    archive = ExportService(data_dir=tmp_path).export_bundle(
        mode="proposal-pack", proposal_id=p1_id,
    )

    exported = _read_bundle_jsonl(archive, "trade_proposals.jsonl")
    exported_ids = {row["id"] for row in exported}
    assert exported_ids == {p1_id}, (
        f"proposal-pack must contain only the named proposal; got {exported_ids}"
    )
    assert p2_id not in exported_ids


def test_proposal_pack_omits_serenity_when_proposal_has_no_opportunity(tmp_path, monkeypatch):
    """#62/#162 corollary: when the proposal has no opportunity_id, the pack
    should not include serenity_* tables at all — dumping every theme/evidence
    row is the same lie by another name."""
    _setup_env(tmp_path, monkeypatch)
    p1_id, _ = _seed_two_proposals()

    from aletheia.sync.exporter import ExportService
    archive = ExportService(data_dir=tmp_path).export_bundle(
        mode="proposal-pack", proposal_id=p1_id,
    )

    unpacked = archive.parent / "unpacked"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    bundle_root = next(d for d in unpacked.iterdir() if d.is_dir())
    db_dir = bundle_root / "db"
    # No serenity files should exist at all — not empty ones.
    assert not (db_dir / "serenity_themes.jsonl").exists()
    assert not (db_dir / "serenity_evidence.jsonl").exists()
    assert not (db_dir / "serenity_claims.jsonl").exists()


def test_proposal_pack_rejects_unknown_proposal(tmp_path, monkeypatch):
    """#62 corollary: a caller-supplied proposal_id that doesn't exist must
    fail loudly rather than emit an empty (or all-proposals) bundle."""
    _setup_env(tmp_path, monkeypatch)
    _seed_two_proposals()

    from aletheia.sync.exporter import ExportService
    with pytest.raises(ValueError, match="not found"):
        ExportService(data_dir=tmp_path).export_bundle(
            mode="proposal-pack", proposal_id="proposal-that-does-not-exist",
        )


# ---------------------------------------------------------------------------
# #63 — import: missing manifest files now recorded as errors
# ---------------------------------------------------------------------------


def _build_bundle_with_missing_file(tmp_path: Path) -> Path:
    """Emit a minimal bundle whose checksums.txt names a file the archive
    doesn't actually contain — the shape of the #63 bug."""
    staging = tmp_path / "staging_bundle"
    staging.mkdir()
    (staging / "manifest.json").write_text('{"mode":"test"}', encoding="utf-8")
    # Real file that IS in the archive
    good = staging / "good.txt"
    good.write_text("hello", encoding="utf-8")
    # checksums.txt lists TWO files but only good.txt exists.
    from aletheia.storage.hashing import sha256_file
    good_hash = sha256_file(good)
    (staging / "checksums.txt").write_text(
        f"{good_hash}  good.txt\n"
        f"0000000000000000000000000000000000000000000000000000000000000000  missing.txt\n",
        encoding="utf-8",
    )

    archive = tmp_path / "bundle_missing.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname="bundle_missing")
    return archive


def test_import_dry_run_flags_missing_files_from_checksums(tmp_path, monkeypatch):
    """#63: an entry in checksums.txt whose file isn't in the archive used to
    be silently skipped. Now it lands as a `result.errors` entry."""
    _setup_env(tmp_path, monkeypatch)
    archive = _build_bundle_with_missing_file(tmp_path)

    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).dry_run(archive)

    assert any("missing file" in e.lower() for e in result.errors), (
        f"missing-file error should be surfaced; got {result.errors}"
    )


# ---------------------------------------------------------------------------
# #64 — import: diverged rows now become conflicts, not silent duplicates
# ---------------------------------------------------------------------------


def _build_bundle_with_row(tmp_path: Path, table: str, row: dict) -> Path:
    """Minimal bundle containing db/<table>.jsonl with one row + checksums."""
    from aletheia.storage.hashing import sha256_file

    staging = tmp_path / "staging_bundle_row"
    staging.mkdir()
    db_dir = staging / "db"
    db_dir.mkdir()
    jsonl_path = db_dir / f"{table}.jsonl"
    jsonl_path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")

    # Emit a checksums.txt so the verifier is exercised end-to-end.
    checksums = staging / "checksums.txt"
    checksums.write_text(f"{sha256_file(jsonl_path)}  db/{table}.jsonl\n", encoding="utf-8")

    archive = tmp_path / f"bundle_{table}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname="bundle_row")
    return archive


def test_import_diverged_row_recorded_as_conflict_not_skipped_duplicate(tmp_path, monkeypatch):
    """#64: a bundle row whose PK already exists locally BUT with different
    column values must land in `conflicts`, not `skipped_duplicates`.

    Uses ps_lessons — it has a well-known `lesson_id` pk (in ImportService._find_pk),
    a text `hypothesis` column that's easy to make diverge, and no FK constraints
    that would fight the seeded row."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "INSERT INTO ps_lessons "
        "(lesson_id, source_type, source_id, category, severity, status, summary, evidence_json, created_at) "
        "VALUES ('lesson-A', 'test', 'src-A', 'risk', 'medium', 'accepted', 'LOCAL summary', '{}', '2026-07-07T00:00:00Z')"
    )
    db.commit()

    remote_row = {
        "lesson_id": "lesson-A",
        "source_type": "test",
        "source_id": "src-A",
        "category": "risk",
        "severity": "medium",
        "status": "accepted",
        "summary": "REMOTE summary differs",  # diverges from local
        "evidence_json": "{}",
        "created_at": "2026-07-07T00:00:00Z",
    }
    archive = _build_bundle_with_row(tmp_path, "ps_lessons", remote_row)

    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).dry_run(archive)

    assert result.skipped_duplicates == 0, (
        "a diverged row should NOT be silently counted as a skipped duplicate"
    )
    assert len(result.conflicts) == 1, (
        f"a diverged row should surface ONE conflict; got {result.conflicts}"
    )
    conflict = result.conflicts[0]
    assert conflict.object_type == "ps_lessons"
    assert conflict.object_id == "lesson-A"
    assert conflict.local_hash != conflict.remote_hash
    assert "summary" in conflict.message


def test_import_identical_duplicate_still_counted_as_skipped(tmp_path, monkeypatch):
    """#64: identical rows keep the OLD `skipped_duplicates` behaviour — the
    new conflict path only activates on actual divergence."""
    _setup_env(tmp_path, monkeypatch)

    from aletheia.storage.sqlite import get_db
    db = get_db()
    db.execute(
        "INSERT INTO ps_lessons "
        "(lesson_id, source_type, source_id, category, severity, status, summary, evidence_json, created_at) "
        "VALUES ('lesson-B', 'test', 'src-B', 'risk', 'medium', 'accepted', 'same summary', '{}', '2026-07-07T00:00:00Z')"
    )
    db.commit()

    identical_row = {
        "lesson_id": "lesson-B",
        "source_type": "test",
        "source_id": "src-B",
        "category": "risk",
        "severity": "medium",
        "status": "accepted",
        "summary": "same summary",
        "evidence_json": "{}",
        "created_at": "2026-07-07T00:00:00Z",
    }
    archive = _build_bundle_with_row(tmp_path, "ps_lessons", identical_row)

    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).dry_run(archive)

    assert result.conflicts == []
    assert result.skipped_duplicates == 1


# ---------------------------------------------------------------------------
# #271 — import fail-closed: verification errors abort before any mutation
# ---------------------------------------------------------------------------


def _build_bundle_with_db_row_and_checksums(
    tmp_path: Path,
    archive_name: str,
    *,
    checksums_content: str | None = None,
    include_checksums: bool = True,
    extra_files: dict[str, str] | None = None,
) -> Path:
    """Build a minimal valid bundle with one ps_lessons row.

    ``checksums_content`` overrides the auto-generated checksums.txt.
    ``extra_files`` adds additional files (path-relative-to-bundle-root → content).
    ``include_checksums=False`` omits checksums.txt entirely.
    """
    from aletheia.storage.hashing import sha256_file

    staging = tmp_path / f"staging_{archive_name}"
    staging.mkdir(parents=True)
    db_dir = staging / "db"
    db_dir.mkdir()

    row = {
        "lesson_id": f"lesson-{archive_name}",
        "source_type": "test",
        "source_id": "s1",
        "category": "risk",
        "severity": "medium",
        "status": "accepted",
        "summary": "hello",
        "evidence_json": "{}",
        "created_at": "2026-01-01T00:00:00Z",
    }
    jsonl_path = db_dir / "ps_lessons.jsonl"
    jsonl_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    if extra_files:
        for rel, content in extra_files.items():
            p = staging / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")

    if include_checksums:
        if checksums_content is not None:
            (staging / "checksums.txt").write_text(checksums_content, encoding="utf-8")
        else:
            good_hash = sha256_file(jsonl_path)
            lines = [f"{good_hash}  db/ps_lessons.jsonl"]
            if extra_files:
                for rel in extra_files:
                    lines.append(f"{sha256_file(staging / rel)}  {rel}")
            (staging / "checksums.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    archive = tmp_path / f"{archive_name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=archive_name)
    return archive


def _count_lessons(tmp_path: Path, lesson_id: str) -> int:
    conn = sqlite3.connect(str(tmp_path / "test.db"))
    row = conn.execute(
        "SELECT COUNT(*) FROM ps_lessons WHERE lesson_id=?", (lesson_id,)
    ).fetchone()
    conn.close()
    return row[0]


def test_import_missing_checksums_aborts_without_mutation(tmp_path, monkeypatch):
    """#271: merge with no checksums.txt must report error and leave DB unchanged."""
    _setup_env(tmp_path, monkeypatch)
    archive = _build_bundle_with_db_row_and_checksums(
        tmp_path, "no_checksums", include_checksums=False
    )
    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert result.errors, "missing checksums.txt must report an error"
    assert "checksums" in result.errors[0].lower()
    assert _count_lessons(tmp_path, "lesson-no_checksums") == 0, (
        "no rows must be inserted when checksums.txt is missing"
    )


def test_import_checksum_mismatch_aborts_without_mutation(tmp_path, monkeypatch):
    """#271: merge with wrong hash must report error and leave DB unchanged."""
    _setup_env(tmp_path, monkeypatch)
    bad_checksums = "0" * 64 + "  db/ps_lessons.jsonl\n"
    archive = _build_bundle_with_db_row_and_checksums(
        tmp_path, "bad_hash", checksums_content=bad_checksums
    )
    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert any("mismatch" in e.lower() for e in result.errors), (
        f"checksum mismatch must be reported; got {result.errors}"
    )
    assert _count_lessons(tmp_path, "lesson-bad_hash") == 0, (
        "no rows must be inserted when checksum mismatch is detected"
    )


def test_import_unlisted_payload_aborts_without_mutation(tmp_path, monkeypatch):
    """#271: merge with an extra db file not in checksums must error and not insert."""
    _setup_env(tmp_path, monkeypatch)
    # Build a bundle where checksums only covers ps_lessons.jsonl, but there's
    # an extra unlisted db file that would otherwise be imported.
    from aletheia.storage.hashing import sha256_file

    staging = tmp_path / "staging_unlisted"
    staging.mkdir()
    db_dir = staging / "db"
    db_dir.mkdir()

    row = {
        "lesson_id": "lesson-unlisted",
        "source_type": "test", "source_id": "s1",
        "category": "risk", "severity": "medium", "status": "accepted",
        "summary": "hi", "evidence_json": "{}", "created_at": "2026-01-01T00:00:00Z",
    }
    jsonl_path = db_dir / "ps_lessons.jsonl"
    jsonl_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

    # An extra file that is NOT listed in checksums.txt
    extra_path = db_dir / "unknown_table.jsonl"
    extra_path.write_text('{"id":"x"}\n', encoding="utf-8")

    # Only list the main file, not the extra
    good_hash = sha256_file(jsonl_path)
    (staging / "checksums.txt").write_text(
        f"{good_hash}  db/ps_lessons.jsonl\n", encoding="utf-8"
    )
    archive = tmp_path / "unlisted.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname="unlisted")

    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert any("unlisted" in e.lower() for e in result.errors), (
        f"unlisted payload must be reported; got {result.errors}"
    )
    assert _count_lessons(tmp_path, "lesson-unlisted") == 0, (
        "no rows must be inserted when unlisted payload is detected"
    )


def test_import_malformed_checksum_line_aborts_without_mutation(tmp_path, monkeypatch):
    """#271: malformed checksums.txt lines must error and prevent import."""
    _setup_env(tmp_path, monkeypatch)
    archive = _build_bundle_with_db_row_and_checksums(
        tmp_path, "malformed", checksums_content="not-a-valid-checksum-line\n"
    )
    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert result.errors, "malformed checksum line must produce an error"
    assert _count_lessons(tmp_path, "lesson-malformed") == 0, (
        "no rows must be inserted when checksums.txt is malformed"
    )


# ---------------------------------------------------------------------------
# #273 — import table allowlist and identifier quoting
# ---------------------------------------------------------------------------


def _build_bundle_with_arbitrary_table(
    tmp_path: Path, archive_name: str, table_name: str, row: dict
) -> Path:
    """Bundle containing db/<table_name>.jsonl with one row, checksums correct."""
    from aletheia.storage.hashing import sha256_file

    staging = tmp_path / f"staging_{archive_name}"
    staging.mkdir(parents=True)
    db_dir = staging / "db"
    db_dir.mkdir()
    jsonl_path = db_dir / f"{table_name}.jsonl"
    jsonl_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    (staging / "checksums.txt").write_text(
        f"{sha256_file(jsonl_path)}  db/{table_name}.jsonl\n", encoding="utf-8"
    )
    archive = tmp_path / f"{archive_name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname=archive_name)
    return archive


def test_import_rejects_unauthorized_table(tmp_path, monkeypatch):
    """#273: a checksum-valid bundle targeting an unlisted table must be rejected."""
    _setup_env(tmp_path, monkeypatch)
    archive = _build_bundle_with_arbitrary_table(
        tmp_path, "unauth", "risk_decisions",
        {"id": "rd-1", "decision": "kill", "created_at": "2026-01-01T00:00:00Z"},
    )
    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert any("authorized" in e.lower() or "rejected" in e.lower() for e in result.errors), (
        f"unauthorized table must be rejected; got {result.errors}"
    )
    from aletheia.storage.sqlite import get_db
    db = get_db()
    count = db.execute("SELECT COUNT(*) FROM risk_decisions WHERE id='rd-1'").fetchone()[0]
    assert count == 0, "unauthorized table row must not be inserted"


def test_import_mixed_bundle_writes_nothing_on_unauthorized_table(tmp_path, monkeypatch):
    """#273: a bundle with one valid + one unauthorized table must write neither."""
    from aletheia.storage.hashing import sha256_file

    _setup_env(tmp_path, monkeypatch)

    staging = tmp_path / "staging_mixed"
    staging.mkdir()
    db_dir = staging / "db"
    db_dir.mkdir()

    good_row = {
        "lesson_id": "lesson-mixed-ok",
        "source_type": "test", "source_id": "s1",
        "category": "risk", "severity": "medium", "status": "accepted",
        "summary": "good", "evidence_json": "{}", "created_at": "2026-01-01T00:00:00Z",
    }
    bad_row = {"id": "settings-poison", "key": "admin", "value": "evil"}

    good_file = db_dir / "ps_lessons.jsonl"
    bad_file = db_dir / "settings.jsonl"
    good_file.write_text(json.dumps(good_row) + "\n", encoding="utf-8")
    bad_file.write_text(json.dumps(bad_row) + "\n", encoding="utf-8")

    lines = [
        f"{sha256_file(good_file)}  db/ps_lessons.jsonl",
        f"{sha256_file(bad_file)}  db/settings.jsonl",
    ]
    (staging / "checksums.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    archive = tmp_path / "mixed.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname="mixed")

    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert result.errors, "mixed bundle with unauthorized table must report errors"
    assert _count_lessons(tmp_path, "lesson-mixed-ok") == 0, (
        "valid-table rows must not be inserted when a sibling table is unauthorized"
    )


def test_import_rejects_row_with_unknown_column(tmp_path, monkeypatch):
    """#273: a row in an allowed table with an unknown column is rejected."""
    _setup_env(tmp_path, monkeypatch)

    row_with_extra = {
        "lesson_id": "lesson-badcol",
        "source_type": "test", "source_id": "s1",
        "category": "risk", "severity": "medium", "status": "accepted",
        "summary": "ok", "evidence_json": "{}", "created_at": "2026-01-01T00:00:00Z",
        "injected_column": "DROP TABLE ps_lessons",
    }
    archive = _build_bundle_with_arbitrary_table(
        tmp_path, "badcol", "ps_lessons", row_with_extra
    )
    from aletheia.sync.importer import ImportService
    result = ImportService(data_dir=tmp_path).merge(archive)

    assert any("schema" in e.lower() or "column" in e.lower() for e in result.errors), (
        f"unknown column must be rejected; got {result.errors}"
    )
    assert _count_lessons(tmp_path, "lesson-badcol") == 0, (
        "no rows must be inserted when an unknown column is present"
    )
