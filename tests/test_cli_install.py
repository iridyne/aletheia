from __future__ import annotations

import os
import subprocess

from typer.testing import CliRunner

from aletheia.cli import install as install_cli


runner = CliRunner()


def test_install_writes_uv_project_reroute_and_reports_ready(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")

    monkeypatch.setattr(install_cli.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin"]))

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 0
    assert calls == [["uv", "tool", "dir", "--bin"]]
    shim = install_cli._shim_path(bin_dir)
    assert shim.exists()
    assert f"--project {install_cli.project_root()}" in shim.read_text()
    assert "-- aletheia" in shim.read_text()
    assert "You can now run `aletheia`" in result.stdout


def test_install_requires_explicit_force_for_external_collision(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = install_cli._shim_path(bin_dir)
    shim.write_text("#!/bin/sh\necho external\n")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")

    monkeypatch.setattr(install_cli.subprocess, "run", fake_run)

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 2
    assert "Review it before retrying with --force" in result.stderr
    assert shim.read_text() == "#!/bin/sh\necho external\n"


def test_install_force_replaces_external_collision(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = install_cli._shim_path(bin_dir)
    shim.write_text("#!/bin/sh\necho external\n")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")

    monkeypatch.setattr(install_cli.subprocess, "run", fake_run)
    monkeypatch.setenv("PATH", str(bin_dir))

    result = runner.invoke(install_cli.app, ["--force"])

    assert result.exit_code == 0
    assert install_cli._MARKER in shim.read_text()
