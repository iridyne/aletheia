"""Manifest and archive integrity for immutable packaged runtimes.

PyInstaller 6 POSIX one-folder bundles intentionally use symbolic links.  Aletheia
preserves only relative links whose final target remains inside the immutable release
root.  Absolute, escaping, dangling, cyclic, hard-link, and symlink-ancestor archive
layouts fail closed.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Any


class PackageIntegrityError(RuntimeError):
    def __init__(self, reason: str, detail: str | None = None):
        self.reason = reason
        super().__init__(detail or reason)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def safe_relative_path(value: str, *, reason: str = "update_archive_path_unsafe") -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise PackageIntegrityError(reason, value)
    return path


def _normalized_relative(parts: tuple[str, ...], *, reason: str, detail: str) -> PurePosixPath:
    stack: list[str] = []
    for part in parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not stack:
                raise PackageIntegrityError(reason, detail)
            stack.pop()
        else:
            stack.append(part)
    if not stack:
        raise PackageIntegrityError(reason, detail)
    return PurePosixPath(*stack)


def safe_link_target(link_path: PurePosixPath, target: str) -> PurePosixPath:
    raw = PurePosixPath(target)
    if raw.is_absolute() or not target:
        raise PackageIntegrityError("update_symlink_target_unsafe", f"{link_path} -> {target}")
    return _normalized_relative(
        (*link_path.parent.parts, *raw.parts),
        reason="update_symlink_target_unsafe",
        detail=f"{link_path} -> {target}",
    )


def validate_filesystem_symlink(root: Path, path: Path) -> str:
    if not path.is_symlink():
        raise PackageIntegrityError("update_manifest_symlink_missing", str(path))
    target = os.readlink(path)
    relative = PurePosixPath(path.relative_to(root).as_posix())
    safe_link_target(relative, target)
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PackageIntegrityError("update_symlink_target_invalid", f"{relative}: {exc}") from exc
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PackageIntegrityError("update_symlink_target_escape", f"{relative} -> {target}")
    return target


def iter_payload_entries(root: Path):
    """Yield regular files and symbolic links without following directory links."""

    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink() or path.is_file():
            yield path


def manifest_entry(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    if path.is_symlink():
        target = validate_filesystem_symlink(root, path)
        payload = target.encode("utf-8")
        return {
            "path": relative,
            "type": "symlink",
            "target": target,
            "size": len(payload),
            "sha256": sha256_bytes(payload),
        }
    return {
        "path": relative,
        "type": "file",
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _manifest_error(reason: str, detail: str | None = None):
    # Import lazily to avoid an import cycle while packaged.py is initialising.
    from aletheia.update.packaged import PackagedUpdateError

    raise PackagedUpdateError(reason, detail)


def validate_release_dir(
    release_root: Path,
    *,
    expected_release_id: str,
    expected_sha: str,
) -> dict[str, Any]:
    from aletheia.update.packaged import _read_json, platform_id

    try:
        root_resolved = release_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        _manifest_error("update_release_root_invalid", str(exc))
    if not root_resolved.is_dir():
        _manifest_error("update_release_root_invalid", str(release_root))

    manifest_path = root_resolved / "release-manifest.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != 1 or manifest.get("target") != "packaged-runtime":
        _manifest_error("update_manifest_target_invalid")
    if manifest.get("platform") != platform_id():
        _manifest_error("update_manifest_platform_mismatch")
    if expected_sha and manifest.get("git_sha") != expected_sha:
        _manifest_error("update_manifest_git_sha_mismatch")

    entrypoint = manifest.get("runtime_entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        _manifest_error("update_manifest_entrypoint_missing")
    try:
        entry_relative = safe_relative_path(entrypoint, reason="update_manifest_entrypoint_unsafe")
        entrypoint_path = root_resolved.joinpath(*entry_relative.parts).resolve(strict=True)
    except (PackageIntegrityError, OSError, RuntimeError) as exc:
        _manifest_error("update_manifest_entrypoint_unsafe", str(exc))
    if (
        (entrypoint_path != root_resolved and root_resolved not in entrypoint_path.parents)
        or not entrypoint_path.is_file()
    ):
        _manifest_error("update_manifest_entrypoint_unsafe")

    files = manifest.get("files")
    if not isinstance(files, list):
        _manifest_error("update_manifest_files_missing")
    listed: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            _manifest_error("update_manifest_file_invalid")
        relative_value = item.get("path")
        if not isinstance(relative_value, str):
            _manifest_error("update_manifest_file_invalid")
        try:
            relative = safe_relative_path(relative_value)
        except PackageIntegrityError as exc:
            _manifest_error(exc.reason, str(exc))
        path = root_resolved.joinpath(*relative.parts)
        kind = item.get("type", "file")
        if kind == "symlink":
            try:
                target = validate_filesystem_symlink(root_resolved, path)
            except PackageIntegrityError as exc:
                _manifest_error(exc.reason, str(exc))
            expected_target = item.get("target")
            payload = target.encode("utf-8")
            if target != expected_target:
                _manifest_error("update_manifest_symlink_target_mismatch", relative_value)
            if len(payload) != int(item.get("size", -1)):
                _manifest_error("update_manifest_file_size_mismatch", relative_value)
            if sha256_bytes(payload) != item.get("sha256"):
                _manifest_error("update_manifest_file_hash_mismatch", relative_value)
        elif kind == "file":
            if path.is_symlink() or not path.is_file():
                _manifest_error("update_manifest_file_missing", relative_value)
            if path.stat().st_size != int(item.get("size", -1)):
                _manifest_error("update_manifest_file_size_mismatch", relative_value)
            if sha256_file(path) != item.get("sha256"):
                _manifest_error("update_manifest_file_hash_mismatch", relative_value)
        else:
            _manifest_error("update_manifest_file_type_unsupported", str(kind))
        listed.add(relative_value)

    actual = {
        path.relative_to(root_resolved).as_posix()
        for path in iter_payload_entries(root_resolved)
        if path.name != "release-manifest.json"
    }
    if actual != listed:
        _manifest_error(
            "update_manifest_file_set_mismatch",
            f"extra={sorted(actual - listed)[:5]} missing={sorted(listed - actual)[:5]}",
        )
    manifest["release_id"] = expected_release_id
    manifest["entrypoint_path"] = str(entrypoint_path)
    return manifest


def _member_relative(member: tarfile.TarInfo) -> PurePosixPath:
    return safe_relative_path(member.name)


def extract_archive(archive: Path, staging: Path) -> Path:
    """Extract a signed runtime archive while preserving safe internal symlinks."""

    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            symlink_paths: set[PurePosixPath] = set()
            for member in members:
                relative = _member_relative(member)
                if member.issym():
                    safe_link_target(relative, member.linkname)
                    symlink_paths.add(relative)
                elif member.islnk() or not (member.isfile() or member.isdir()):
                    raise PackageIntegrityError(
                        "update_archive_member_unsupported", member.name
                    )
            for member in members:
                relative = _member_relative(member)
                for parent in relative.parents:
                    if parent in symlink_paths:
                        raise PackageIntegrityError(
                            "update_archive_symlink_ancestor", member.name
                        )
            bundle.extractall(staging, filter="data")
    except PackageIntegrityError as exc:
        from aletheia.update.packaged import PackagedUpdateError

        raise PackagedUpdateError(exc.reason, str(exc)) from exc
    except (tarfile.TarError, OSError) as exc:
        from aletheia.update.packaged import PackagedUpdateError

        raise PackagedUpdateError("update_archive_extract_failed", str(exc)) from exc

    roots = [path for path in staging.iterdir() if path.is_dir() and not path.is_symlink()]
    if len(roots) != 1:
        from aletheia.update.packaged import PackagedUpdateError

        raise PackagedUpdateError("update_archive_root_invalid")
    # Resolve every extracted link after the full tree exists; catches dangling/cyclic links.
    try:
        for path in roots[0].rglob("*"):
            if path.is_symlink():
                validate_filesystem_symlink(roots[0], path)
    except PackageIntegrityError as exc:
        from aletheia.update.packaged import PackagedUpdateError

        raise PackagedUpdateError(exc.reason, str(exc)) from exc
    return roots[0]
