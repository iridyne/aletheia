"""Backup verification — checks integrity of backup archives."""

import re
import tarfile
import tempfile
from pathlib import Path

from aletheia.storage.hashing import sha256_file


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


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

        actual_paths = {
            path.relative_to(bundle_dir).as_posix()
            for path in bundle_dir.rglob("*")
            if path.is_file()
            and path.relative_to(bundle_dir).as_posix()
            not in {"checksums.txt", "manifest.yaml"}
        }
        result.file_count = len(actual_paths)
        declared_paths: set[str] = set()
        bundle_root = bundle_dir.resolve()

        # Verify each checksum
        for line_number, line in enumerate(
            checksums_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                result.errors.append(
                    f"Malformed checksum entry on line {line_number}: "
                    "expected '<sha256>  <path>'"
                )
                result.valid = False
                continue

            expected_hash, raw_path = parts
            normalized_path = raw_path.replace("\\", "/")
            path_parts = normalized_path.split("/")
            if not _SHA256_RE.fullmatch(expected_hash):
                result.errors.append(f"Malformed SHA-256 on checksum line {line_number}")
                result.valid = False
                continue
            if (
                normalized_path.startswith("/")
                or not normalized_path
                or any(part in {"", ".", ".."} for part in path_parts)
                or ":" in path_parts[0]
            ):
                result.errors.append(
                    f"Unsafe checksum path on line {line_number}: {raw_path}"
                )
                result.valid = False
                continue

            canonical_path = "/".join(path_parts)
            if canonical_path in declared_paths:
                result.errors.append(f"Duplicate checksum path: {canonical_path}")
                result.valid = False
                continue
            declared_paths.add(canonical_path)

            file_path = bundle_dir.joinpath(*path_parts)
            try:
                file_path.resolve().relative_to(bundle_root)
            except ValueError:
                result.errors.append(
                    f"Unsafe checksum path on line {line_number}: {raw_path}"
                )
                result.valid = False
                continue

            if not file_path.is_file() or file_path.is_symlink():
                result.mismatches.append(f"Missing: {canonical_path}")
                result.valid = False
                continue
            actual = sha256_file(file_path)
            result.checked_count += 1
            if actual.lower() != expected_hash.lower():
                result.mismatches.append(f"Mismatch: {canonical_path}")
                result.valid = False

        for unlisted_path in sorted(actual_paths - declared_paths):
            result.mismatches.append(f"Unlisted: {unlisted_path}")
            result.valid = False

    return result
