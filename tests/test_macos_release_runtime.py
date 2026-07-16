from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.release.build_bundle import build_bundle


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_repository(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """[project]
name = "aletheia-trading-os"
version = "1.2.3"
requires-python = ">=3.12"
""",
    )
    _write(root / "uv.lock", "version = 1\n")
    _write(root / "aletheia" / "__init__.py", "")
    _write(root / "aletheia" / "dashboard_static" / "index.html", "<html></html>\n")
    runtime_source = Path("scripts/runtime")
    for name in ("install-release.sh", "start-release.sh", "health-check.sh"):
        _write(root / runtime_source / name, (runtime_source / name).read_text(encoding="utf-8"))
    _write(root / runtime_source / "start-release.ps1", "Write-Output ok\n")


def test_installer_verifies_and_atomically_activates_release(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _fake_repository(root)
    result = build_bundle(
        root=root,
        output_dir=tmp_path / "dist",
        git_sha="e" * 40,
        warn_mib=1,
        max_mib=2,
    )

    releases_root = tmp_path / "Application Support" / "Aletheia" / "releases"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    completed = subprocess.run(
        [
            "bash",
            "scripts/runtime/install-release.sh",
            "--archive",
            str(result.archive_path),
            "--checksum",
            str(result.sha256_path),
            "--releases-root",
            str(releases_root),
            "--skip-sync",
        ],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    installed = releases_root / ("e" * 12)
    current = releases_root.parent / "current"
    assert installed.is_dir()
    assert current.is_symlink()
    assert current.resolve() == installed.resolve()
    assert (installed / "release-manifest.json").is_file()
    assert os.access(installed / "scripts/runtime/start-release.sh", os.X_OK)


def test_installer_fails_closed_on_checksum_mismatch(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _fake_repository(root)
    result = build_bundle(
        root=root,
        output_dir=tmp_path / "dist",
        git_sha="f" * 40,
        warn_mib=1,
        max_mib=2,
    )
    result.archive_path.write_bytes(result.archive_path.read_bytes() + b"tampered")

    completed = subprocess.run(
        [
            "bash",
            "scripts/runtime/install-release.sh",
            "--archive",
            str(result.archive_path),
            "--checksum",
            str(result.sha256_path),
            "--releases-root",
            str(tmp_path / "releases"),
            "--skip-sync",
        ],
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert "SHA-256 does not match" in completed.stderr


def test_stable_launcher_requires_manifest_and_absolute_data_override(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    completed = subprocess.run(
        ["bash", "scripts/runtime/start-release.sh", "--release-root", str(release), "--skip-sync"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "release-manifest.json is required" in completed.stderr


def test_workflow_runs_exact_artifact_as_clean_macos_runtime() -> None:
    workflow = Path(".github/workflows/build-dogfood-release.yml").read_text(encoding="utf-8")

    assert "macos-release-runtime:" in workflow
    assert "needs: build-bundle" in workflow
    assert "runs-on: macos-latest" in workflow
    assert "actions/download-artifact@v4" in workflow
    assert "install-release.sh" in workflow
    assert "start-release.sh" in workflow
    assert "health-check.sh" in workflow
    assert "aletheia gateway stop" in workflow
    assert "com.aletheia.trading-os.gateway.plist" in workflow
    assert "ALETHEIA_DATA_DIR" in workflow
