from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from scripts.release.build_bundle import BundleBuildError, BundleTooLarge, build_bundle


def _write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def _fake_repository(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """[project]
name = "aletheia-trading-os"
version = "9.9.9"
requires-python = ">=3.12"
""",
    )
    _write(root / "uv.lock", "version = 1\n")
    _write(root / "README.md", "release fixture\n")
    _write(root / "aletheia" / "__init__.py", "")
    _write(root / "aletheia" / "app.py", "VALUE = 1\n")
    _write(root / "aletheia" / "dashboard_static" / "index.html", "<html></html>\n")
    _write(root / "aletheia" / "dashboard_static" / "assets" / "app.js", "console.log('ok')\n")
    _write(root / "aletheia" / "dashboard_static" / "assets" / "app.js.map", "ignored\n")
    _write(root / "aletheia" / "__pycache__" / "app.pyc", b"ignored")
    _write(root / "scripts" / "runtime" / "start-release.ps1", "Write-Output ok\n")
    _write(root / "tests" / "ignored.py", "raise RuntimeError\n")
    _write(root / "frontend" / "src" / "ignored.tsx", "export default 1\n")
    _write(root / "data" / "app.db", b"runtime state must never ship")


def test_bundle_contains_only_runtime_whitelist_and_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "dist"
    _fake_repository(root)

    result = build_bundle(
        root=root,
        output_dir=output,
        git_sha="a" * 40,
        warn_mib=1,
        max_mib=2,
    )

    with zipfile.ZipFile(result.archive_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("release-manifest.json"))

    assert "aletheia/app.py" in names
    assert "aletheia/dashboard_static/index.html" in names
    assert "aletheia/dashboard_static/assets/app.js" in names
    assert "scripts/runtime/start-release.ps1" in names
    assert "pyproject.toml" in names
    assert "uv.lock" in names
    assert "VERSION" in names
    assert "release-manifest.json" in names

    assert "aletheia/dashboard_static/assets/app.js.map" not in names
    assert "aletheia/__pycache__/app.pyc" not in names
    assert "tests/ignored.py" not in names
    assert "frontend/src/ignored.tsx" not in names
    assert "data/app.db" not in names

    assert manifest["git_sha"] == "a" * 40
    assert manifest["version"] == "9.9.9"
    assert manifest["release_format"] == 2
    assert manifest["data_directory_contract"] == {
        "default": "~/.aletheia",
        "override": "ALETHEIA_DATA_DIR",
        "override_must_be_absolute": True,
    }
    assert result.compressed_size_bytes < 2 * 1024 * 1024
    assert result.sha256_path.is_file()
    assert result.summary_path.is_file()


def test_bundle_is_reproducible_for_same_inputs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _fake_repository(root)

    first = build_bundle(
        root=root,
        output_dir=tmp_path / "dist-a",
        git_sha="b" * 40,
        warn_mib=1,
        max_mib=2,
    )
    second = build_bundle(
        root=root,
        output_dir=tmp_path / "dist-b",
        git_sha="b" * 40,
        warn_mib=1,
        max_mib=2,
    )

    assert first.archive_path.read_bytes() == second.archive_path.read_bytes()
    assert first.sha256_path.read_text(encoding="utf-8").split()[0] == second.sha256_path.read_text(
        encoding="utf-8"
    ).split()[0]


def test_bundle_requires_prebuilt_dashboard(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _fake_repository(root)
    (root / "aletheia" / "dashboard_static" / "index.html").unlink()

    with pytest.raises(BundleBuildError, match="build the dashboard"):
        build_bundle(
            root=root,
            output_dir=tmp_path / "dist",
            git_sha="c" * 40,
            warn_mib=1,
            max_mib=2,
        )


def test_bundle_hard_limit_fails_closed_and_keeps_size_report(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "dist"
    _fake_repository(root)
    _write(root / "aletheia" / "random.bin", os.urandom(32 * 1024))

    with pytest.raises(BundleTooLarge, match="hard limit"):
        build_bundle(
            root=root,
            output_dir=output,
            git_sha="d" * 40,
            warn_mib=0.001,
            max_mib=0.002,
        )

    summary = json.loads((output / "bundle-size.json").read_text(encoding="utf-8"))
    assert summary["compressed_size_mib"] > summary["max_mib"]
