from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts.release.build_bundle import build_bundle


_UNIX_ONLY = pytest.mark.skipif(os.name == "nt", reason="requires Unix shell and symlink semantics")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fake_repository(root: Path) -> None:
    _write(
        root / "pyproject.toml",
        """[project]
name = "aletheia-trading-os"
dynamic = ["version"]
requires-python = ">=3.12"
""",
    )
    _write(root / "uv.lock", "version = 1\n")
    _write(root / "aletheia" / "__init__.py", '__version__ = "1.2.3"\n')
    _write(root / "extensions" / "__init__.py", "")
    _write(root / "aletheia" / "dashboard_static" / "index.html", "<html></html>\n")
    runtime_source = Path("scripts/runtime")
    for name in ("install-release.sh", "start-release.sh", "health-check.sh"):
        _write(root / runtime_source / name, (runtime_source / name).read_text(encoding="utf-8"))
    _write(root / runtime_source / "start-release.ps1", "Write-Output ok\n")


def _run_health_check(tmp_path: Path, ready: object, health: object) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "url = sys.argv[-1]\n"
        f"print({json.dumps(ready)!r} if url.endswith('/readyz') else {json.dumps(health)!r})\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            "bash",
            "scripts/runtime/health-check.sh",
            "--base-url",
            "http://example.test",
            "--timeout-seconds",
            "0",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_health_check_sequence(
    tmp_path: Path, ready: object, health_rows: list[object]
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    state_file = tmp_path / "health-count"
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        "url = sys.argv[-1]\n"
        f"ready = {json.dumps(ready)!r}\n"
        f"health_rows = json.loads({json.dumps(health_rows)!r})\n"
        f"state_file = pathlib.Path({str(state_file)!r})\n"
        "if url.endswith('/readyz'):\n"
        "    print(ready)\n"
        "else:\n"
        "    index = int(state_file.read_text() or '0') if state_file.exists() else 0\n"
        "    print(json.dumps(health_rows[min(index, len(health_rows) - 1)]))\n"
        "    state_file.write_text(str(index + 1))\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [
            "bash",
            "scripts/runtime/health-check.sh",
            "--base-url",
            "http://example.test",
            "--timeout-seconds",
            "2",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@_UNIX_ONLY
def test_health_check_requires_healthy_readiness_and_health_payloads(tmp_path: Path) -> None:
    healthy = _run_health_check(
        tmp_path / "healthy",
        {"ok": True},
        {"status": "healthy", "issues": []},
    )
    assert healthy.returncode == 0
    assert json.loads(healthy.stdout)["ok"] is True

    not_ready = _run_health_check(
        tmp_path / "not-ready",
        {"ok": False},
        {"status": "healthy", "issues": []},
    )
    assert not_ready.returncode != 0
    not_ready_payload = json.loads(not_ready.stdout)
    assert not_ready_payload["ok"] is False
    assert not_ready_payload["ready"]["ok"] is False

    degraded = _run_health_check(
        tmp_path / "degraded",
        {"ok": True},
        {"status": "degraded", "issues": ["market_data_stale:45m"]},
    )
    assert degraded.returncode != 0
    degraded_payload = json.loads(degraded.stdout)
    assert degraded_payload["ok"] is False
    assert degraded_payload["health"]["status"] == "degraded"

    invalid = _run_health_check(tmp_path / "invalid", {"ok": True}, "not-an-object")
    assert invalid.returncode != 0
    invalid_payload = json.loads(invalid.stdout)
    assert invalid_payload["error"] == "readiness or health payload invalid"


@_UNIX_ONLY
def test_health_check_retries_initializing_until_healthy(tmp_path: Path) -> None:
    result = _run_health_check_sequence(
        tmp_path / "initializing",
        {"ok": True},
        [
            {"status": "initializing", "issues": ["market_data_initializing"]},
            {"status": "healthy", "issues": []},
        ],
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["health"]["status"] == "healthy"


@_UNIX_ONLY
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


@_UNIX_ONLY
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


@_UNIX_ONLY
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
