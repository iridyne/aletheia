"""Backup verification — checks integrity of backup archives."""

import re
import tarfile
import tempfile
from pathlib import Path

from aletheia.storage.hashing import sha256_file

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# Files exempt from the "unlisted payload" check — the exporter writes manifest.yaml
# after checksums.txt, so it cannot include itself in the manifest.
_EXEMPT_NAMES = frozenset({"checksums.txt", "manifest.yaml"})


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
    """Verify a backup archive's integrity by checking all checksums.

    Strict manifest verification (#279):
    - Rejects malformed checksum lines, non-hex hashes, and unsafe paths.
    - Rejects duplicate path declarations within the manifest.
    - Errors if any listed file is missing or its SHA-256 does not match.
    - Errors if any payload file under the bundle is absent from the manifest
      (checksums.txt and manifest.yaml are exempt).
    """
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

        # Count payload files (excludes exempt names)
        for f in bundle_dir.rglob("*"):
            if f.is_file() and f.name not in _EXEMPT_NAMES:
                result.file_count += 1

        # ── Strict manifest parsing (#279) ────────────────────────────────────
        declared: dict[str, str] = {}  # rel_path → expected_hash
        for lineno, raw_line in enumerate(
            checksums_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                result.errors.append(
                    f"checksums.txt line {lineno}: malformed (expected '<hash>  <path>')"
                )
                result.valid = False
                continue
            expected_hash, rel_path = parts
            if not _HEX_RE.match(expected_hash):
                result.errors.append(
                    f"checksums.txt line {lineno}: non-hex SHA-256 hash for {rel_path!r}"
                )
                result.valid = False
                continue
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                result.errors.append(
                    f"checksums.txt line {lineno}: unsafe path {rel_path!r}"
                )
                result.valid = False
                continue
            if rel_path in declared:
                result.errors.append(
                    f"checksums.txt line {lineno}: duplicate path {rel_path!r}"
                )
                result.valid = False
                continue
            declared[rel_path] = expected_hash

        # ── Verify each declared file ─────────────────────────────────────────
        for rel_path, expected_hash in declared.items():
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

        # ── Reject payload files not declared in the manifest (#279) ─────────
        for f in bundle_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.name in _EXEMPT_NAMES:
                continue
            try:
                rel = f.relative_to(bundle_dir).as_posix()
            except ValueError:
                rel = str(f)
            if rel not in declared:
                result.errors.append(
                    f"Unlisted payload file not in checksum manifest: {rel}"
                )
                result.valid = False

    return result
