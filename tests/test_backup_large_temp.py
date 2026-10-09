"""Backup verify/restore scratch space must avoid a quota-limited /tmp (#3719)."""

from __future__ import annotations

import tarfile

from aletheia.backup import verify as verify_module
from ops.backup import restore as restore_module


def _tiny_archive(tmp_path):
    bundle = tmp_path / "src" / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "manifest.yaml").write_text("{}\n")
    archive = tmp_path / "b.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname="bundle")
    return archive


def test_verify_and_restore_scratch_lands_in_large_temp(tmp_path, monkeypatch):
    large = tmp_path / "large"
    large.mkdir()
    monkeypatch.setattr(verify_module, "large_temp_dir", lambda: str(large), raising=False)
    monkeypatch.setattr(restore_module, "large_temp_dir", lambda: str(large), raising=False)
    seen: list[str | None] = []
    real_tmpdir = verify_module.tempfile.TemporaryDirectory
    real_mkstemp = restore_module.tempfile.mkstemp

    def spy_tmpdir(*args, dir=None, **kwargs):
        seen.append(dir)
        return real_tmpdir(*args, dir=dir, **kwargs)

    def spy_mkstemp(*args, dir=None, **kwargs):
        seen.append(dir)
        return real_mkstemp(*args, dir=dir, **kwargs)

    monkeypatch.setattr(verify_module.tempfile, "TemporaryDirectory", spy_tmpdir)
    monkeypatch.setattr(restore_module.tempfile, "mkstemp", spy_mkstemp)

    archive = _tiny_archive(tmp_path)
    restore_module.RestoreService().restore(archive, tmp_path / "target")

    # snapshot copy (mkstemp) + verify extraction (TemporaryDirectory), at least
    assert len(seen) >= 2
    assert set(seen) == {str(large)}
