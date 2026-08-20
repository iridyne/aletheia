from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.cli import install as install_cli


runner = CliRunner()


# ---------------------------------------------------------------------------
# Pure unit helpers — no subprocess, no file I/O
# ---------------------------------------------------------------------------

@pytest.mark.tier_unit
def test_unix_shim_content():
    root = Path("/home/alice/aletheia")
    content = install_cli._shim_content(root, platform="posix")
    assert content.startswith("#!/bin/sh")
    assert "# " + install_cli._MARKER in content
    assert "--project" in content
    assert str(root) in content
    assert "-- aletheia" in content


@pytest.mark.tier_unit
def test_windows_cmd_shim_content():
    root = Path("C:/Users/alice/aletheia")
    content = install_cli._shim_content(root, platform="nt")
    assert content.startswith(f"@rem {install_cli._MARKER}")
    assert "@echo off" in content
    assert f'--project "{root}"' in content or '--project "C:/Users/alice/aletheia"' in content
    assert "-- aletheia %*" in content
    assert "\r\n" in content


@pytest.mark.tier_unit
def test_shim_content_path_with_spaces_unix():
    root = Path("/home/alice/Source Projects/aletheia")
    content = install_cli._shim_content(root, platform="posix")
    assert "Source Projects" in content
    assert shlex.quote(str(root)) in content


@pytest.mark.tier_unit
def test_shim_content_path_with_spaces_windows():
    root = Path("C:/Users/Alice/Source Projects/aletheia")
    content = install_cli._shim_content(root, platform="nt")
    assert '"C:/Users/Alice/Source Projects/aletheia"' in content


@pytest.mark.tier_unit
def test_shim_content_unicode_path():
    root = Path("/home/用户/aletheia_项目")
    unix_content = install_cli._shim_content(root, platform="posix")
    assert "用户" in unix_content
    win_content = install_cli._shim_content(root, platform="nt")
    assert "用户" in win_content


@pytest.mark.tier_unit
def test_shim_content_strips_trailing_separator():
    root = Path("/home/alice/aletheia/")
    content = install_cli._shim_content(root, platform="posix")
    # trailing slash should be stripped; path should still be present
    assert "/home/alice/aletheia" in content


@pytest.mark.tier_unit
def test_is_managed_shim_detects_marker(tmp_path):
    f = tmp_path / "aletheia"
    f.write_text(f"#!/bin/sh\n# {install_cli._MARKER}\nexec uv run ...\n")
    assert install_cli._is_managed_shim(f) is True


@pytest.mark.tier_unit
def test_is_managed_shim_rejects_unmanaged(tmp_path):
    f = tmp_path / "aletheia"
    f.write_text("#!/bin/sh\necho 'some other tool'\n")
    assert install_cli._is_managed_shim(f) is False


@pytest.mark.tier_unit
def test_is_managed_shim_missing_file(tmp_path):
    assert install_cli._is_managed_shim(tmp_path / "nonexistent") is False


# ---------------------------------------------------------------------------
# Integration tests: full install command via CliRunner
# ---------------------------------------------------------------------------

def _make_fake_run(bin_dir, *, update_shell_calls=None, update_shell_rc=0):
    """Return a fake subprocess.run that mocks uv subcommands."""
    def fake_run(command, **kwargs):
        if command == ["uv", "tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")
        if command == ["uv", "tool", "update-shell"]:
            if update_shell_calls is not None:
                update_shell_calls.append(True)
            return subprocess.CompletedProcess(command, update_shell_rc, "", "permission denied" if update_shell_rc else "")
        return subprocess.CompletedProcess(command, 0, "", "")
    return fake_run


@pytest.mark.tier_unit
def test_install_writes_shim_and_reports_ready(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin"]))

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 0, result.output
    shim = install_cli._shim_path(bin_dir)
    assert shim.exists()
    content = shim.read_text()
    assert install_cli._MARKER in content
    assert "-- aletheia" in content
    assert "You can now run `aletheia`" in result.output


@pytest.mark.tier_unit
def test_install_shim_points_to_project_root(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 0
    shim = install_cli._shim_path(bin_dir)
    root_str = str(install_cli.project_root()).rstrip("/\\")
    assert root_str in shim.read_text()


@pytest.mark.tier_unit
def test_install_refuses_unmanaged_collision(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = install_cli._shim_path(bin_dir)
    shim.write_text("#!/bin/sh\necho external\n")

    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 2
    assert "Review it before retrying with --force" in result.output
    assert shim.read_text() == "#!/bin/sh\necho external\n"


@pytest.mark.tier_unit
def test_install_force_replaces_unmanaged(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = install_cli._shim_path(bin_dir)
    shim.write_text("#!/bin/sh\necho external\n")

    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    result = runner.invoke(install_cli.app, ["--force"])

    assert result.exit_code == 0
    assert install_cli._MARKER in shim.read_text()


@pytest.mark.tier_unit
def test_install_idempotent(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    result1 = runner.invoke(install_cli.app, [])
    result2 = runner.invoke(install_cli.app, [])

    assert result1.exit_code == 0
    assert result2.exit_code == 0
    shim = install_cli._shim_path(bin_dir)
    content = shim.read_text()
    assert content.count(install_cli._MARKER) == 1


@pytest.mark.tier_unit
def test_install_path_already_set(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), "/usr/bin"]))

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code == 0
    assert "You can now run `aletheia`" in result.output


@pytest.mark.tier_unit
def test_install_path_missing_interactive_yes(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    update_shell_calls: list[bool] = []
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_calls=update_shell_calls))
    monkeypatch.setattr(install_cli, "_is_interactive", lambda: True)
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, [], input="y\n")

    assert result.exit_code == 0
    assert update_shell_calls, "uv tool update-shell should have been called"
    assert "PATH configuration updated" in result.output


@pytest.mark.tier_unit
def test_install_path_missing_interactive_no(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    update_shell_calls: list[bool] = []
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_calls=update_shell_calls))
    monkeypatch.setattr(install_cli, "_is_interactive", lambda: True)
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, [], input="n\n")

    assert result.exit_code == 0
    assert not update_shell_calls, "uv tool update-shell should NOT have been called"
    assert "manually" in result.output


@pytest.mark.tier_unit
def test_install_non_interactive_no_block(monkeypatch, tmp_path):
    """Non-interactive mode must not prompt and must not run update-shell."""
    bin_dir = tmp_path / "bin"
    update_shell_calls: list[bool] = []
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_calls=update_shell_calls))
    monkeypatch.setattr(install_cli, "_is_interactive", lambda: False)
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, [])  # no --yes, non-interactive

    assert result.exit_code == 0
    assert not update_shell_calls
    assert "not on PATH" in result.output


@pytest.mark.tier_unit
def test_install_yes_flag_runs_update_shell(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    update_shell_calls: list[bool] = []
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_calls=update_shell_calls))
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, ["--yes"])

    assert result.exit_code == 0
    assert update_shell_calls


@pytest.mark.tier_unit
def test_install_no_update_shell_flag(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    update_shell_calls: list[bool] = []
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_calls=update_shell_calls))
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, ["--no-update-shell"])

    assert result.exit_code == 0
    assert not update_shell_calls
    assert "not on PATH" in result.output


@pytest.mark.tier_unit
def test_install_update_shell_failure_surfaced(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir, update_shell_rc=1))
    monkeypatch.setenv("PATH", "/usr/bin")

    result = runner.invoke(install_cli.app, ["--yes"])

    assert result.exit_code == 0  # shim was installed; PATH config failed gracefully
    assert "Warning" in result.output or "failed" in result.output.lower()


@pytest.mark.tier_unit
def test_install_uv_dir_bin_failure(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "uv not found")

    monkeypatch.setattr(install_cli.subprocess, "run", fake_run)

    result = runner.invoke(install_cli.app, [])

    assert result.exit_code != 0
    assert "Could not locate" in result.output


@pytest.mark.tier_unit
def test_install_moved_checkout_updates_managed_shim(monkeypatch, tmp_path):
    """Reinstalling after moving the clone should update the shim to the new root."""
    bin_dir = tmp_path / "bin"
    root_a = tmp_path / "aletheia_v1"
    root_b = tmp_path / "aletheia_v2"

    monkeypatch.setattr(install_cli.subprocess, "run", _make_fake_run(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    monkeypatch.setattr(install_cli, "project_root", lambda: root_a)
    r1 = runner.invoke(install_cli.app, [])
    assert r1.exit_code == 0

    shim = install_cli._shim_path(bin_dir)
    assert str(root_a) in shim.read_text()

    monkeypatch.setattr(install_cli, "project_root", lambda: root_b)
    r2 = runner.invoke(install_cli.app, [])
    assert r2.exit_code == 0

    content = shim.read_text()
    assert str(root_b) in content
    assert str(root_a) not in content
