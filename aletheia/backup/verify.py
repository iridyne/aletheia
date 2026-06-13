"""Backup verification — checks integrity of backup archives."""

import tarfile
import tempfile
from pathlib import Path

from aletheia.storage.hashing import sha256_file


class VerifyResult:
    """Result of backup verification."""

    def __init__(self):
        self.valid: bool = True
        self.file_count: int = 0
        self.checked_count: int = 0
        self.mismatches: list[str] = []
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "file_count": self.file_count,
            "checked_count": self.checked_count,
            "mismatches": self.mismatches,
            "errors": self.errors,
        }


def verify_backup(archive_path: Path) -> VerifyResult:
    """Verify a backup archive's integrity by checking all checksums."""
    result = VerifyResult()

    if not archive_path.exists():
        result.valid = False
        result.errors.append(f"Archive not found: {archive_path}")
        return result

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmp_path, filter="data")
        except Exception as e:
            result.valid = False
            result.errors.append(f"Failed to extract: {e}")
            return result

        # Find bundle root
        subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        if not subdirs:
            result.valid = False
            result.errors.append("Empty archive")
            return result
        bundle_dir = subdirs[0]

        # Read checksums
        checksums_file = bundle_dir / "checksums.txt"
        if not checksums_file.exists():
            result.valid = False
            result.errors.append("Missing checksums.txt")
            return result

        # Count files
        for f in bundle_dir.rglob("*"):
            if f.is_file() and f.name not in ("checksums.txt", "manifest.yaml"):
                result.file_count += 1

        # Verify each checksum
        for line in checksums_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                continue
            expected_hash, rel_path = parts
            file_path = bundle_dir / rel_path
            if not file_path.exists():
                result.mismatches.append(f"Missing: {rel_path}")
                result.valid = False
                continue
            actual = sha256_file(file_path)
            result.checked_count += 1
            if actual != expected_hash:
                result.mismatches.append(f"Mismatch: {rel_path}")
                result.valid = False

    return result
