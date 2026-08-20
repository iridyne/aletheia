"""Tests for the post-command install hint in aletheia.main."""
from __future__ import annotations

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from typer.testing import CliRunner

import aletheia.main as main_mod
from aletheia.cli import install as install_cli


runner = CliRunner()


def _fake_uv_run(bin_dir: Path):
    def fake(command, **kwargs):
        if command == ["uv", "tool", "dir", "--bin"]:
            return subprocess.CompletedProcess(command, 0, f"{bin_dir}\n", "")
        return subprocess.CompletedProcess(command, 0, "", "")
    return fake


# ---------------------------------------------------------------------------
# _do_post_command_hint unit tests
# ---------------------------------------------------------------------------

@pytest.mark.tier_unit
def test_hint_skipped_if_marker_exists(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    marker.touch()
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)

    install_calls: list[bool] = []
    monkeypatch.setattr(main_mod, "_run_install", lambda **kw: install_calls.append(True))

    main_mod._do_post_command_hint()

    assert not install_calls


@pytest.mark.tier_unit
def test_hint_skipped_if_ran_install(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", True)

    main_mod._do_post_command_hint()

    assert marker.exists()


@pytest.mark.tier_unit
def test_hint_skipped_in_non_interactive(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", False)
    monkeypatch.setattr("sys.stdin", StringIO(""))  # not a TTY

    install_calls: list[bool] = []
    monkeypatch.setattr(main_mod, "_run_install", lambda **kw: install_calls.append(True))

    main_mod._do_post_command_hint()

    assert not install_calls
    assert not marker.exists()


@pytest.mark.tier_unit
def test_hint_yes_runs_installer(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", False)

    fake_stdin = StringIO("y\n")
    fake_stdin.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    install_calls: list[dict] = []

    def fake_install(**kw):
        install_calls.append(kw)

    monkeypatch.setattr(main_mod, "_run_install", fake_install)

    main_mod._do_post_command_hint()

    assert install_calls, "installer should have been called"
    assert install_calls[0] == {"force": False, "yes": True, "no_update_shell": False}
    assert marker.exists()


@pytest.mark.tier_unit
def test_hint_no_writes_marker_skips_installer(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", False)

    fake_stdin = StringIO("n\n")
    fake_stdin.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    install_calls: list[bool] = []
    monkeypatch.setattr(main_mod, "_run_install", lambda **kw: install_calls.append(True))

    main_mod._do_post_command_hint()

    assert not install_calls
    assert marker.exists()


@pytest.mark.tier_unit
def test_hint_enter_treated_as_yes(monkeypatch, tmp_path):
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", False)

    fake_stdin = StringIO("\n")  # bare Enter = default Yes
    fake_stdin.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    install_calls: list[bool] = []
    monkeypatch.setattr(main_mod, "_run_install", lambda **kw: install_calls.append(True))

    main_mod._do_post_command_hint()

    assert install_calls


@pytest.mark.tier_unit
def test_hint_eof_on_stdin_does_not_crash(monkeypatch, tmp_path):
    """If stdin hits EOF (e.g. piped empty stream with TTY spoofed), hint must not crash."""
    marker = tmp_path / ".aletheia_install_asked"
    monkeypatch.setattr(main_mod, "_install_asked_path", lambda: marker)
    monkeypatch.setattr(main_mod, "_RAN_INSTALL", False)

    fake_stdin = StringIO("")  # EOF immediately
    fake_stdin.isatty = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setattr("sys.stdin", fake_stdin)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)

    install_calls: list[bool] = []
    monkeypatch.setattr(main_mod, "_run_install", lambda **kw: install_calls.append(True))

    # Must not raise
    main_mod._do_post_command_hint()

    # EOF-blank readline returns "" which matches the default "yes"
    # this is acceptable behaviour; what matters is no crash


# ---------------------------------------------------------------------------
# aletheia install subcommand accessible from main app
# ---------------------------------------------------------------------------

@pytest.mark.tier_unit
def test_install_subcommand_in_main_app_help(monkeypatch, tmp_path):
    result = runner.invoke(main_mod.app, ["install", "--help"])
    assert result.exit_code == 0
    assert "--force" in result.output
    assert "--yes" in result.output
    assert "--no-update-shell" in result.output


@pytest.mark.tier_unit
def test_install_subcommand_runs_via_main_app(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(install_cli.subprocess, "run", _fake_uv_run(bin_dir))
    monkeypatch.setenv("PATH", str(bin_dir))

    result = runner.invoke(main_mod.app, ["install"])

    assert result.exit_code == 0
    shim = install_cli._shim_path(bin_dir)
    assert shim.exists()
    assert install_cli._MARKER in shim.read_text()
