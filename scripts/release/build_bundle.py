from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_WARN_MIB = 60.0
DEFAULT_MAX_MIB = 100.0
MIB = 1024 * 1024
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

_REQUIRED_ROOT_FILES = (Path("pyproject.toml"), Path("uv.lock"))
_OPTIONAL_ROOT_FILES = (Path("README.md"), Path("LICENSE"), Path("LICENSE.md"))
_REQUIRED_TREES = (Path("aletheia"), Path("scripts/runtime"))
_EXCLUDED_DIR_NAMES = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".map"}
_EXECUTABLE_SUFFIXES = {".sh"}


class BundleBuildError(RuntimeError):
    """Release bundle cannot be built safely."""


class BundleTooLarge(BundleBuildError):
    """Compressed release bundle exceeded the configured hard limit."""


@dataclass(frozen=True)
class BundleResult:
    archive_path: Path
    sha256_path: Path
    summary_path: Path
    compressed_size_bytes: int
    uncompressed_size_bytes: int
    file_count: int
    git_sha: str


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_git_sha(root: Path, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    github_sha = os.environ.get("GITHUB_SHA", "").strip()
    if github_sha:
        return github_sha
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise BundleBuildError("git SHA is required outside a Git checkout") from exc
    return result.stdout.strip()


def _project_metadata(root: Path) -> tuple[str, str]:
    pyproject = root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data["project"]
        return str(project["version"]), str(project["requires-python"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise BundleBuildError("pyproject.toml is missing release metadata") from exc


def _is_excluded(relative_path: Path) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES for part in relative_path.parts):
        return True
    return relative_path.suffix.lower() in _EXCLUDED_SUFFIXES


def collect_bundle_files(root: Path) -> list[Path]:
    """Return the source whitelist for a release bundle.

    Runtime state, tests, documentation sources, frontend sources and VCS metadata
    are absent by construction because only the application tree, runtime scripts
    and a few root lock/metadata files are considered.
    """
    root = root.resolve()
    dashboard_index = root / "aletheia" / "dashboard_static" / "index.html"
    if not dashboard_index.is_file():
        raise BundleBuildError(
            "aletheia/dashboard_static/index.html is missing; build the dashboard before the bundle"
        )

    files: set[Path] = set()
    for relative in _REQUIRED_ROOT_FILES:
        path = root / relative
        if not path.is_file():
            raise BundleBuildError(f"required release file is missing: {relative.as_posix()}")
        files.add(relative)

    for relative in _OPTIONAL_ROOT_FILES:
        if (root / relative).is_file():
            files.add(relative)

    for relative_tree in _REQUIRED_TREES:
        tree = root / relative_tree
        if not tree.is_dir():
            raise BundleBuildError(f"required release tree is missing: {relative_tree.as_posix()}")
        for path in tree.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if _is_excluded(relative):
                continue
            if path.is_symlink():
                raise BundleBuildError(f"release bundle refuses symbolic link: {relative.as_posix()}")
            files.add(relative)

    return sorted(files, key=lambda item: item.as_posix())


def _copy_sources(root: Path, staging: Path, source_files: Iterable[Path]) -> list[dict[str, object]]:
    manifest_files: list[dict[str, object]] = []
    for relative in source_files:
        source = root / relative
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = source.read_bytes()
        destination.write_bytes(payload)
        manifest_files.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
        )
    return manifest_files


def _zip_mode(relative: Path) -> int:
    """Return a stable Unix file mode stored in the cross-platform ZIP."""
    if relative.suffix.lower() in _EXECUTABLE_SUFFIXES:
        return 0o100755
    return 0o100644


def _write_deterministic_zip(staging: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        archive_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(staging.rglob("*"), key=lambda item: item.relative_to(staging).as_posix()):
            if not path.is_file():
                continue
            relative_path = path.relative_to(staging)
            relative = relative_path.as_posix()
            info = zipfile.ZipInfo(relative, date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = _zip_mode(relative_path) << 16
            info.create_system = 3
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _safe_short_sha(git_sha: str) -> str:
    safe = "".join(character for character in git_sha if character.isalnum()).lower()
    if len(safe) < 7:
        raise BundleBuildError("git SHA must contain at least seven alphanumeric characters")
    return safe[:12]


def build_bundle(
    *,
    root: Path,
    output_dir: Path,
    git_sha: str | None = None,
    warn_mib: float = DEFAULT_WARN_MIB,
    max_mib: float = DEFAULT_MAX_MIB,
) -> BundleResult:
    root = root.resolve()
    output_dir = output_dir.resolve()
    if warn_mib <= 0 or max_mib <= 0 or warn_mib > max_mib:
        raise BundleBuildError("bundle limits must satisfy 0 < warn_mib <= max_mib")

    resolved_sha = _resolve_git_sha(root, git_sha)
    short_sha = _safe_short_sha(resolved_sha)
    version, requires_python = _project_metadata(root)
    source_files = collect_bundle_files(root)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"aletheia-dogfood-{short_sha}.zip"
    sha256_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    summary_path = output_dir / "bundle-size.json"

    with tempfile.TemporaryDirectory(prefix="aletheia-release-") as temporary:
        staging = Path(temporary) / "bundle"
        staging.mkdir(parents=True)
        manifest_files = _copy_sources(root, staging, source_files)
        (staging / "VERSION").write_text(f"{version}\n", encoding="utf-8", newline="\n")
        version_payload = (staging / "VERSION").read_bytes()
        manifest_files.append(
            {
                "path": "VERSION",
                "size_bytes": len(version_payload),
                "sha256": _sha256_bytes(version_payload),
            }
        )
        manifest_files.sort(key=lambda item: str(item["path"]))
        manifest = {
            "release_format": 3,
            "application": "aletheia",
            "version": version,
            "git_sha": resolved_sha,
            "target": "portable-source",
            "supported_platforms": ["linux", "macos", "windows"],
            "requires_python": requires_python,
            "runtime_entrypoints": {
                "windows": "scripts/runtime/start-release.ps1",
                "unix": "scripts/runtime/start-release.sh",
            },
            "data_directory_contract": {
                "default": "~/.aletheia",
                "override": "ALETHEIA_DATA_DIR",
                "override_must_be_absolute": True,
            },
            "files": manifest_files,
        }
        manifest_payload = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        (staging / "release-manifest.json").write_bytes(manifest_payload)
        _write_deterministic_zip(staging, archive_path)

    compressed_size = archive_path.stat().st_size
    uncompressed_size = sum(int(row["size_bytes"]) for row in manifest_files) + len(manifest_payload)
    bundle_sha = _sha256_file(archive_path)
    sha256_path.write_text(f"{bundle_sha}  {archive_path.name}\n", encoding="utf-8", newline="\n")
    summary = {
        "archive": archive_path.name,
        "target": "portable-source",
        "compressed_size_bytes": compressed_size,
        "compressed_size_mib": round(compressed_size / MIB, 3),
        "uncompressed_size_bytes": uncompressed_size,
        "uncompressed_size_mib": round(uncompressed_size / MIB, 3),
        "file_count": len(manifest_files) + 1,
        "warn_mib": warn_mib,
        "max_mib": max_mib,
        "sha256": bundle_sha,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if compressed_size > max_mib * MIB:
        raise BundleTooLarge(
            f"release bundle is {compressed_size / MIB:.2f} MiB; hard limit is {max_mib:.2f} MiB"
        )
    if compressed_size > warn_mib * MIB:
        message = (
            f"release bundle is {compressed_size / MIB:.2f} MiB; "
            f"soft limit is {warn_mib:.2f} MiB"
        )
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::warning::{message}")
        else:
            print(f"WARNING: {message}", file=sys.stderr)

    return BundleResult(
        archive_path=archive_path,
        sha256_path=sha256_path,
        summary_path=summary_path,
        compressed_size_bytes=compressed_size,
        uncompressed_size_bytes=uncompressed_size,
        file_count=len(manifest_files) + 1,
        git_sha=resolved_sha,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an immutable Aletheia dogfood release bundle")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--git-sha")
    parser.add_argument("--warn-mib", type=float, default=DEFAULT_WARN_MIB)
    parser.add_argument("--max-mib", type=float, default=DEFAULT_MAX_MIB)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_bundle(
            root=args.root,
            output_dir=args.output_dir,
            git_sha=args.git_sha,
            warn_mib=args.warn_mib,
            max_mib=args.max_mib,
        )
    except BundleBuildError as exc:
        print(f"release bundle failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "archive": str(result.archive_path),
                "compressed_size_mib": round(result.compressed_size_bytes / MIB, 3),
                "file_count": result.file_count,
                "git_sha": result.git_sha,
                "sha256_file": str(result.sha256_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
