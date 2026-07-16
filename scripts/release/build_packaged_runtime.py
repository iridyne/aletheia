"""Build and package a self-contained Aletheia runtime payload.

The builder is intentionally separate from the existing portable-source bundle. It
produces a platform/architecture-specific PyInstaller one-folder payload for the stable
tray/CLI host. It never includes runtime state from ``~/.aletheia`` and preserves the
safe internal symbolic links used by PyInstaller 6 POSIX bundles.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from aletheia.update.package_integrity import (
    iter_payload_entries,
    manifest_entry,
    validate_filesystem_symlink,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_MIB = 350


@dataclass(frozen=True)
class BuildResult:
    payload_dir: Path
    archive: Path
    checksum: Path
    size_report: Path
    manifest: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return os.environ.get("GITHUB_SHA", "unknown")


def _version() -> str:
    namespace: dict[str, object] = {}
    init_file = ROOT / "aletheia" / "__init__.py"
    exec(compile(init_file.read_text(encoding="utf-8"), str(init_file), "exec"), namespace)
    return str(namespace.get("__version__", "0.0.0"))


def platform_id() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    aliases = {
        "darwin": "darwin",
        "windows": "windows",
        "linux": "linux",
        "arm64": "arm64",
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }
    return f"{aliases.get(system, system)}-{aliases.get(machine, machine)}"


def _runtime_entrypoint(payload_dir: Path) -> str:
    candidate = "aletheia-core.exe" if os.name == "nt" else "aletheia-core"
    path = payload_dir / candidate
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"PyInstaller output is missing regular runtime entrypoint: {path}")
    return candidate


def iter_payload_files(payload_dir: Path, *, exclude_manifest: bool = True) -> Iterable[Path]:
    for path in iter_payload_entries(payload_dir):
        if exclude_manifest and path.name == "release-manifest.json":
            continue
        yield path


def write_manifest(payload_dir: Path, *, git_sha: str, version: str, target: str) -> Path:
    entrypoint = _runtime_entrypoint(payload_dir)
    files = [manifest_entry(payload_dir, path) for path in iter_payload_files(payload_dir)]
    manifest = {
        "schema_version": 1,
        "target": "packaged-runtime",
        "platform": target,
        "version": version,
        "git_sha": git_sha,
        "runtime_entrypoint": entrypoint,
        "dashboard_url": "http://127.0.0.1:8787",
        "runtime_data_contract": "per-user-dot-aletheia",
        "files": files,
    }
    destination = payload_dir / "release-manifest.json"
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def _normalized_tar_info(payload_dir: Path, path: Path, relative: Path) -> tarfile.TarInfo:
    info = tarfile.TarInfo(relative.as_posix())
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if path.is_symlink():
        target = validate_filesystem_symlink(payload_dir, path)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        info.size = 0
        info.mode = 0o777
        return info
    stat_result = path.stat()
    info.size = stat_result.st_size
    info.mode = 0o755 if stat_result.st_mode & stat.S_IXUSR else 0o644
    return info


def create_deterministic_archive(payload_dir: Path, archive: Path) -> None:
    archive.parent.mkdir(parents=True, exist_ok=True)
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for path in iter_payload_files(payload_dir, exclude_manifest=False):
                    relative = Path(payload_dir.name) / path.relative_to(payload_dir)
                    info = _normalized_tar_info(payload_dir, path, relative)
                    if path.is_symlink():
                        tar.addfile(info)
                    else:
                        with path.open("rb") as source:
                            tar.addfile(info, source)


def _entry_uncompressed_size(payload_dir: Path, path: Path) -> int:
    if path.is_symlink():
        return len(validate_filesystem_symlink(payload_dir, path).encode("utf-8"))
    return path.stat().st_size


def package_existing_payload(
    payload_dir: Path,
    output_dir: Path,
    *,
    git_sha: str,
    version: str,
    target: str,
    max_mib: int = DEFAULT_MAX_MIB,
) -> BuildResult:
    if payload_dir.is_symlink():
        raise RuntimeError("packaged runtime root cannot be a symbolic link")
    payload_dir = payload_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = write_manifest(payload_dir, git_sha=git_sha, version=version, target=target)
    short_sha = git_sha[:12] if git_sha != "unknown" else "unknown"
    archive = output_dir / f"aletheia-runtime-{version}-{short_sha}-{target}.tar.gz"
    create_deterministic_archive(payload_dir, archive)
    compressed_bytes = archive.stat().st_size
    max_bytes = max_mib * 1024 * 1024
    if compressed_bytes > max_bytes:
        archive.unlink(missing_ok=True)
        raise RuntimeError(
            f"packaged runtime is {compressed_bytes / 1024 / 1024:.2f} MiB; "
            f"hard limit is {max_mib} MiB"
        )
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    checksum.write_text(f"{_sha256(archive)}  {archive.name}\n", encoding="utf-8")
    entries = list(iter_payload_files(payload_dir, exclude_manifest=False))
    uncompressed_bytes = sum(_entry_uncompressed_size(payload_dir, path) for path in entries)
    report = {
        "archive": archive.name,
        "target": target,
        "version": version,
        "git_sha": git_sha,
        "compressed_size_bytes": compressed_bytes,
        "compressed_size_mib": round(compressed_bytes / 1024 / 1024, 3),
        "uncompressed_size_bytes": uncompressed_bytes,
        "uncompressed_size_mib": round(uncompressed_bytes / 1024 / 1024, 3),
        "file_count": len(entries),
        "symlink_count": sum(1 for path in entries if path.is_symlink()),
        "hard_limit_mib": max_mib,
    }
    size_report = output_dir / "packaged-runtime-size.json"
    size_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return BuildResult(payload_dir, archive, checksum, size_report, manifest)


def run_pyinstaller(*, build_root: Path) -> Path:
    dist_path = build_root / "dist"
    work_path = build_root / "work"
    spec_path = ROOT / "packaging" / "pyinstaller" / "aletheia-core.spec"
    shutil.rmtree(build_root, ignore_errors=True)
    build_root.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist_path),
        "--workpath",
        str(work_path),
        str(spec_path),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    payload_dir = dist_path / "aletheia-core"
    if not payload_dir.is_dir():
        raise RuntimeError(f"PyInstaller did not create expected one-folder payload: {payload_dir}")
    return payload_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "packaged-runtime")
    parser.add_argument("--build-root", type=Path, default=ROOT / "build" / "pyinstaller")
    parser.add_argument("--max-mib", type=int, default=DEFAULT_MAX_MIB)
    parser.add_argument("--payload-dir", type=Path, help="Package an existing PyInstaller folder instead of building")
    args = parser.parse_args()

    payload_dir = args.payload_dir if args.payload_dir else run_pyinstaller(build_root=args.build_root)
    result = package_existing_payload(
        payload_dir,
        args.output_dir,
        git_sha=_git_sha(),
        version=_version(),
        target=platform_id(),
        max_mib=args.max_mib,
    )
    print(json.dumps({
        "payload_dir": str(result.payload_dir),
        "archive": str(result.archive),
        "checksum": str(result.checksum),
        "size_report": str(result.size_report),
        "manifest": str(result.manifest),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
