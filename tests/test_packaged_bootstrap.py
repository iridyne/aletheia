from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from aletheia.update import bootstrap, packaged


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _payload(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True)
    executable = root / ("aletheia-core.exe" if os.name == "nt" else "aletheia-core")
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    if os.name != "nt":
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    internal = root / "_internal"
    internal.mkdir()
    (internal / "runtime.dat").write_bytes(b"runtime")
    git_sha = "a" * 40
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "packaged-runtime",
                "platform": packaged.platform_id(),
                "version": "0.5.0",
                "git_sha": git_sha,
                "runtime_entrypoint": executable.name,
                "runtime_data_contract": "per-user-dot-aletheia",
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return root, "0.5.0+aaaaaaaaaaaa"


def test_bootstrap_installs_immutable_payload_and_update_assets(tmp_path):
    source, release_id = _payload(tmp_path / "source")
    host = tmp_path / "host"
    public_key = tmp_path / "update-public-key.pem"
    public_key.write_text("public-key", encoding="utf-8")
    feed = tmp_path / "update-feed-url.txt"
    feed.write_text("https://updates.example.test/feed.json\n", encoding="utf-8")

    result = bootstrap.install_bootstrap_runtime(
        host_root=host,
        source_root=source,
        public_key=public_key,
        feed_url_file=feed,
    )

    assert result["installed"] is True
    assert result["release_id"] == release_id
    assert json.loads((host / "current.json").read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "current": release_id,
        "previous": None,
    }
    installed = host / "releases" / release_id
    assert (installed / "release-manifest.json").is_file()
    assert (installed / "_internal" / "runtime.dat").read_bytes() == b"runtime"
    assert (host / "update-public-key.pem").read_text(encoding="utf-8") == "public-key"
    assert (host / "update-feed-url.txt").read_text(encoding="utf-8") == (
        "https://updates.example.test/feed.json\n"
    )

    repeated = bootstrap.install_bootstrap_runtime(host_root=host, source_root=source)
    assert repeated == {
        "installed": False,
        "reason": "already_bootstrapped",
        "selection": {"schema_version": 1, "current": release_id, "previous": None},
    }


def test_bootstrap_refuses_corrupt_existing_selection(tmp_path):
    source, _ = _payload(tmp_path / "source")
    host = tmp_path / "host"
    host.mkdir()
    (host / "current.json").write_text(
        json.dumps({"schema_version": 1, "current": "../../checkout"}),
        encoding="utf-8",
    )

    with pytest.raises(packaged.PackagedUpdateError) as caught:
        bootstrap.install_bootstrap_runtime(host_root=host, source_root=source)
    assert caught.value.reason == "current_selection_invalid"


def test_bootstrap_rejects_source_symlinks(tmp_path):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")
    source, _ = _payload(tmp_path / "source")
    outside = tmp_path / "outside"
    outside.write_text("outside", encoding="utf-8")
    link = source / "linked-outside"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(packaged.PackagedUpdateError) as caught:
        bootstrap.install_bootstrap_runtime(host_root=tmp_path / "host", source_root=source)
    assert caught.value.reason in {"update_symlink_target_unsafe", "update_symlink_target_escape"}
