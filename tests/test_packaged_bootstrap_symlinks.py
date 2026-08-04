from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from aletheia.update import bootstrap, packaged
from aletheia.package_integrity import manifest_entry, sha256_file


def test_bootstrap_preserves_safe_internal_symlink(tmp_path: Path):
    if os.name == "nt" or not hasattr(os, "symlink"):
        pytest.skip("POSIX symlink contract")

    source = tmp_path / "source"
    internal = source / "_internal"
    internal.mkdir(parents=True)
    executable = source / "aletheia-core"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    target = internal / "runtime.dat"
    target.write_bytes(b"runtime")
    link = internal / "runtime-link.dat"
    try:
        link.symlink_to("runtime.dat")
    except OSError:
        pytest.skip("symlink creation is not permitted")

    git_sha = "e" * 40
    files = [
        {
            "path": executable.relative_to(source).as_posix(),
            "type": "file",
            "size": executable.stat().st_size,
            "sha256": sha256_file(executable),
        },
        {
            "path": target.relative_to(source).as_posix(),
            "type": "file",
            "size": target.stat().st_size,
            "sha256": sha256_file(target),
        },
        manifest_entry(source, link),
    ]
    (source / "release-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target": "packaged-runtime",
                "platform": packaged.platform_id(),
                "version": "0.5.0",
                "git_sha": git_sha,
                "runtime_entrypoint": "aletheia-core",
                "runtime_data_contract": "per-user-dot-aletheia",
                "files": files,
            }
        ),
        encoding="utf-8",
    )

    host = tmp_path / "host"
    result = bootstrap.install_bootstrap_runtime(host_root=host, source_root=source)
    installed_link = host / "releases" / result["release_id"] / "_internal" / "runtime-link.dat"
    assert installed_link.is_symlink()
    assert os.readlink(installed_link) == "runtime.dat"
    assert installed_link.read_bytes() == b"runtime"
