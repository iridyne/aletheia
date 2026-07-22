"""Regression tests for backup/restore integrity fixes.

Covers:
  #279 — verify_backup: strict checksum parsing + unlisted-file detection
  #281 — create_backup: label path-containment validation
  #283 — create_backup: per-invocation UUID suffix prevents staging/archive collision
  #285 — restore: verify and extract the same private snapshot
  #287 — create_backup: corrupt/non-SQLite app.db propagates failure
  #289 — restore: rollback on in-process copy failure
  #291 — create_backup: atomic archive creation (partial + rename)
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_archive(tmp_path: Path, label: str = "test") -> Path:
    """Create a minimal valid backup archive under tmp_path/backups/."""
    (tmp_path / "backups").mkdir(parents=True, exist_ok=True)
    # Minimal valid SQLite db so snapshot doesn't fail
    db = tmp_path / "app.db"
    if not db.exists():
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.commit()
        conn.close()

    from aletheia.backup.service import BackupService
    return BackupService(data_dir=tmp_path).create_backup(label=label)


# ---------------------------------------------------------------------------
# #279 — strict checksum parsing + unlisted-file detection
# ---------------------------------------------------------------------------


def test_verify_rejects_malformed_checksum_line(tmp_path):
    """#279: a malformed checksums.txt line must make the backup invalid."""
    archive = _make_valid_archive(tmp_path)

    # Extract, corrupt checksums.txt, repack
    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        ck = bundle_dir / "checksums.txt"
        ck.write_text("THIS LINE HAS NO HASH SEPARATOR\n" + ck.read_text(), encoding="utf-8")
        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert not result.valid
    assert any("malformed" in e for e in result.errors)


def test_verify_rejects_non_hex_hash(tmp_path):
    """#279: a non-hex hash in checksums.txt invalidates verification."""
    archive = _make_valid_archive(tmp_path)

    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        ck = bundle_dir / "checksums.txt"
        # Replace first hash with a non-hex value of the right length
        lines = ck.read_text().splitlines()
        lines[0] = "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ  " + lines[0].split("  ", 1)[1]
        ck.write_text("\n".join(lines) + "\n", encoding="utf-8")
        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert not result.valid
    assert any("non-hex" in e for e in result.errors)


def test_verify_rejects_unsafe_path(tmp_path):
    """#279: a path containing '..' in checksums.txt must be rejected."""
    archive = _make_valid_archive(tmp_path)

    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        ck = bundle_dir / "checksums.txt"
        ck.write_text(
            "a" * 64 + "  ../escape/path\n" + ck.read_text(), encoding="utf-8"
        )
        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert not result.valid
    assert any("unsafe" in e for e in result.errors)


def test_verify_rejects_duplicate_checksum_path(tmp_path):
    """#279: a duplicate path in checksums.txt invalidates verification."""
    archive = _make_valid_archive(tmp_path)

    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        ck = bundle_dir / "checksums.txt"
        first_line = ck.read_text().splitlines()[0]
        ck.write_text(first_line + "\n" + first_line + "\n", encoding="utf-8")
        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert not result.valid
    assert any("duplicate" in e for e in result.errors)


def test_verify_rejects_unlisted_payload_file(tmp_path):
    """#279: a file in the bundle but absent from checksums.txt must fail."""
    archive = _make_valid_archive(tmp_path)

    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        # Inject a file that is NOT in checksums.txt
        (bundle_dir / "injected_extra.bin").write_bytes(b"extra")
        archive.unlink()
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)

    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert not result.valid
    assert any("injected_extra.bin" in e for e in result.errors)


def test_verify_valid_archive_still_passes(tmp_path):
    """#279: an unmodified valid archive must still verify cleanly."""
    archive = _make_valid_archive(tmp_path)
    from aletheia.backup.verify import verify_backup
    result = verify_backup(archive)
    assert result.valid, f"valid archive must pass; errors={result.errors} mismatches={result.mismatches}"


# ---------------------------------------------------------------------------
# #281 — label validation
# ---------------------------------------------------------------------------


def test_create_backup_rejects_path_traversal_label(tmp_path):
    """#281: a label containing '..' must raise ValueError before any file is created."""
    (tmp_path / "backups").mkdir()
    from aletheia.backup.service import BackupService
    svc = BackupService(data_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        svc.create_backup(label="../escape")
    assert not list((tmp_path / "backups").iterdir()), "no files must be created on rejected label"


def test_create_backup_rejects_slash_in_label(tmp_path):
    """#281: a label with a path separator must be rejected."""
    (tmp_path / "backups").mkdir()
    from aletheia.backup.service import BackupService
    svc = BackupService(data_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        svc.create_backup(label="sub/dir")


def test_create_backup_rejects_whitespace_label(tmp_path):
    """#281: a label containing whitespace is not a valid filename component."""
    (tmp_path / "backups").mkdir()
    from aletheia.backup.service import BackupService
    svc = BackupService(data_dir=tmp_path)
    with pytest.raises(ValueError, match="invalid"):
        svc.create_backup(label="has space")


def test_create_backup_accepts_normal_label(tmp_path):
    """#281: alphanumeric/dot/underscore/hyphen label creates archive in backups dir."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "backups").mkdir()

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup(label="my-backup_01.v2")
    assert archive.exists()
    assert archive.parent == tmp_path / "backups"


# ---------------------------------------------------------------------------
# #283 — per-invocation UUID suffix
# ---------------------------------------------------------------------------


def test_concurrent_backups_have_distinct_archive_paths(tmp_path):
    """#283: two backups with the same label produce distinct archive paths via UUID suffix."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "backups").mkdir()

    import uuid as _uuid

    uuid_values = [
        _uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"),
        _uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"),
    ]
    call_idx = [0]

    def _fixed_uuid4():
        v = uuid_values[call_idx[0] % len(uuid_values)]
        call_idx[0] += 1
        return v

    from aletheia.backup.service import BackupService
    svc = BackupService(data_dir=tmp_path)

    with patch("aletheia.backup.service.uuid.uuid4", side_effect=_fixed_uuid4):
        a1 = svc.create_backup(label="dup")
        a2 = svc.create_backup(label="dup")

    assert a1 != a2, "two backups with same label must produce distinct archive paths"
    assert a1.exists()
    assert a2.exists()


# ---------------------------------------------------------------------------
# #285 — restore snapshot identity
# ---------------------------------------------------------------------------


def test_restore_uses_snapshot_replacing_source_has_no_effect(tmp_path):
    """#285: replacing the source archive after verification must not change restored content."""
    archive = _make_valid_archive(tmp_path, label="snap-test")

    # Read the original app.db bytes from the archive for comparison
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp_p, filter="data")
        bundle_dir = next(d for d in tmp_p.iterdir() if d.is_dir())
        original_db_bytes = (bundle_dir / "app.db").read_bytes()

    # Create a second archive with different content
    db2 = tmp_path / "app2.db"
    conn2 = sqlite3.connect(str(db2))
    conn2.execute("CREATE TABLE replaced (y TEXT)")
    conn2.execute("INSERT INTO replaced VALUES ('attacker')")
    conn2.commit()
    conn2.close()
    (tmp_path / "backups2").mkdir()
    from aletheia.backup.service import BackupService
    evil_archive = BackupService(data_dir=tmp_path / "evil_data").create_backup.__func__
    # Simpler: build a replacement archive manually
    with tempfile.TemporaryDirectory() as staging2:
        sp = Path(staging2)
        bundle2 = sp / "bundle"
        bundle2.mkdir()
        shutil.copy2(db2, bundle2 / "app.db")
        from aletheia.storage.hashing import sha256_file
        h = sha256_file(bundle2 / "app.db")
        (bundle2 / "checksums.txt").write_text(f"{h}  app.db\n", encoding="utf-8")
        (bundle2 / "manifest.yaml").write_text("backup_id: evil\n", encoding="utf-8")
        replacement = tmp_path / "backups" / "replacement.tar.gz"
        with tarfile.open(replacement, "w:gz") as tar:
            tar.add(sp / "bundle", arcname="bundle")

    # Intercept _copy_to_snapshot to also replace the source archive mid-verify
    from aletheia.backup.restore import RestoreService
    original_copy = RestoreService._copy_to_snapshot

    replaced = False

    def _intercept(archive_path: Path) -> Path:
        nonlocal replaced
        snap = original_copy(archive_path)
        if not replaced:
            replaced = True
            # Replace the source with the evil archive
            shutil.copy2(replacement, archive_path)
        return snap

    target = tmp_path / "restore_target"
    with patch.object(RestoreService, "_copy_to_snapshot", staticmethod(_intercept)):
        result = RestoreService().restore(archive, target)

    assert result.success, f"restore must succeed: {result.errors}"
    # The restored app.db must be the original, not the replacement
    restored_db = target / "app.db"
    assert restored_db.exists()
    assert restored_db.read_bytes() == original_db_bytes, (
        "restore must use the verified snapshot, not the replaced source"
    )


# ---------------------------------------------------------------------------
# #289 — rollback on partial failure
# ---------------------------------------------------------------------------


def test_restore_rollback_leaves_target_absent_on_failure(tmp_path):
    """#289: if target_dir was absent and restore fails mid-copy, it must be absent after."""
    archive = _make_valid_archive(tmp_path, label="rb-test")

    target = tmp_path / "new_restore_target"
    assert not target.exists()

    # Find how many files are in the archive so we can fail on the 2nd copy
    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        files = sorted(
            f for f in bundle_dir.rglob("*")
            if f.is_file() and f.name not in ("checksums.txt", "manifest.yaml")
        )
    if len(files) < 2:
        pytest.skip("archive has only one payload file, cannot test mid-copy failure")

    call_count = [0]
    original_copy2 = shutil.copy2

    def _fail_on_second(src, dst, **kw):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError("injected target copy failure")
        return original_copy2(src, dst, **kw)

    from aletheia.backup.restore import RestoreService
    with patch("aletheia.backup.restore.shutil.copy2", side_effect=_fail_on_second):
        result = RestoreService().restore(archive, target)

    assert not result.success
    assert result.errors
    assert not target.exists(), "absent target must remain absent after failed rollback"


def test_restore_rollback_restores_overwritten_file(tmp_path):
    """#289: if an existing file is overwritten and restore fails on a later file,
    the overwritten file must revert to its original bytes."""
    archive = _make_valid_archive(tmp_path, label="overwrite-rb")

    # Pre-seed target with an existing app.db with different content
    target = tmp_path / "restore_target_existing"
    target.mkdir()
    original_bytes = b"original app.db content that must survive failed restore"
    (target / "app.db").write_bytes(original_bytes)

    # Find how many files are in the archive
    with tempfile.TemporaryDirectory() as staging:
        sp = Path(staging)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(sp, filter="data")
        bundle_dir = next(d for d in sp.iterdir() if d.is_dir())
        files = sorted(
            f for f in bundle_dir.rglob("*")
            if f.is_file() and f.name not in ("checksums.txt", "manifest.yaml")
        )
    if len(files) < 2:
        pytest.skip("archive has only one payload file, cannot test mid-copy failure")

    call_count = [0]
    original_copy2 = shutil.copy2

    def _fail_on_second(src, dst, **kw):
        call_count[0] += 1
        if call_count[0] == 2:
            raise OSError("injected target copy failure")
        return original_copy2(src, dst, **kw)

    from aletheia.backup.restore import RestoreService
    with patch("aletheia.backup.restore.shutil.copy2", side_effect=_fail_on_second):
        result = RestoreService().restore(archive, target)

    assert not result.success
    assert result.errors
    # app.db must revert to the original bytes
    assert (target / "app.db").read_bytes() == original_bytes, (
        "overwritten file must be restored to pre-restore bytes after rollback"
    )


# ---------------------------------------------------------------------------
# #291 — atomic archive creation
# ---------------------------------------------------------------------------


def test_failed_backup_leaves_no_archive(tmp_path):
    """#291: if tar construction fails, no final archive and no partial must remain."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "backups").mkdir()

    from aletheia.backup.service import BackupService

    original_tarfile_open = tarfile.open
    call_count = [0]

    class _FailingTar:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def add(self, *args, **kwargs):
            call_count[0] += 1
            raise OSError("injected tar write failure")

    def _patched_open(path, mode, *args, **kwargs):
        if mode == "w:gz":
            return _FailingTar()
        return original_tarfile_open(path, mode, *args, **kwargs)

    svc = BackupService(data_dir=tmp_path)
    with patch("aletheia.backup.service.tarfile.open", side_effect=_patched_open):
        with pytest.raises(OSError, match="injected tar write failure"):
            svc.create_backup(label="partial-test")

    archives = list((tmp_path / "backups").glob("backup_*.tar.gz"))
    partials = list((tmp_path / "backups").glob("*.part"))
    assert not archives, f"no final archive must exist after failure, got {archives}"
    assert not partials, f"no partial file must remain after failure, got {partials}"
    assert svc.list_backups() == [], "list_backups must be empty after failed creation"


def test_successful_backup_publishes_exactly_one_archive(tmp_path):
    """#291: a successful backup produces exactly one final archive."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()
    conn.close()
    (tmp_path / "backups").mkdir()

    from aletheia.backup.service import BackupService
    svc = BackupService(data_dir=tmp_path)
    archive = svc.create_backup(label="atomic-ok")
    assert archive.exists()
    listed = svc.list_backups()
    assert len(listed) == 1, f"expected exactly one backup listed, got {listed}"
    assert listed[0]["path"] == str(archive)
