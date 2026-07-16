"""Install the immutable runtime bundled inside the first desktop package.

The macOS application contains one expanded PyInstaller payload under Resources. On the
first Tray or CLI launch that payload runs this private entrypoint, verifies its own
manifest, copies itself into the per-user release root, and creates ``current.json``.
Existing selections are never overwritten; normal signed updates own all later changes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4

from aletheia.update import packaged

_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid4().hex[:8]}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    if os.name != "nt":
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def install_bootstrap_runtime(
    *,
    host_root: Path,
    source_root: Path | None = None,
    public_key: Path | None = None,
    feed_url_file: Path | None = None,
) -> dict:
    host_root = host_root.expanduser()
    if not host_root.is_absolute():
        raise packaged.PackagedUpdateError("host_root_not_absolute")
    paths = {
        "releases": host_root / "releases",
        "current": host_root / "current.json",
        "public_key": host_root / "update-public-key.pem",
        "feed_url": host_root / "update-feed-url.txt",
    }
    if paths["current"].exists():
        selection = packaged._read_json(paths["current"])  # noqa: SLF001
        return {"installed": False, "reason": "already_bootstrapped", "selection": selection}

    source_root = (source_root or Path(sys.executable).resolve().parent).resolve()
    manifest = packaged._read_json(source_root / "release-manifest.json")  # noqa: SLF001
    version = str(manifest.get("version") or "").strip()
    git_sha = str(manifest.get("git_sha") or "").strip()
    release_id = f"{version}+{git_sha[:12]}"
    if not _RELEASE_ID.fullmatch(release_id):
        raise packaged.PackagedUpdateError("bootstrap_release_id_invalid", release_id)
    packaged._validate_release_dir(  # noqa: SLF001
        source_root,
        expected_release_id=release_id,
        expected_sha=git_sha,
    )

    paths["releases"].mkdir(parents=True, exist_ok=True)
    destination = paths["releases"] / release_id
    if destination.is_symlink():
        raise packaged.PackagedUpdateError("bootstrap_destination_symlink")
    if destination.exists():
        packaged._validate_release_dir(  # noqa: SLF001
            destination,
            expected_release_id=release_id,
            expected_sha=git_sha,
        )
    else:
        staging = paths["releases"] / f".bootstrap-{uuid4().hex}"
        shutil.copytree(source_root, staging, symlinks=False)
        packaged._validate_release_dir(  # noqa: SLF001
            staging,
            expected_release_id=release_id,
            expected_sha=git_sha,
        )
        os.replace(staging, destination)

    if public_key is not None:
        if not public_key.is_absolute() or not public_key.is_file():
            raise packaged.PackagedUpdateError("bootstrap_public_key_missing")
        paths["public_key"].write_bytes(public_key.read_bytes())
        os.chmod(paths["public_key"], 0o600)
    if feed_url_file is not None:
        if not feed_url_file.is_absolute() or not feed_url_file.is_file():
            raise packaged.PackagedUpdateError("bootstrap_feed_url_missing")
        feed_url = feed_url_file.read_text(encoding="utf-8").strip()
        if not feed_url:
            raise packaged.PackagedUpdateError("bootstrap_feed_url_empty")
        paths["feed_url"].write_text(feed_url + "\n", encoding="utf-8")

    selection = {"schema_version": 1, "current": release_id, "previous": None}
    _atomic_json(paths["current"], selection)
    return {
        "installed": True,
        "release_id": release_id,
        "release_root": str(destination),
        "selection": selection,
    }
