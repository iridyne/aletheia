import json
import subprocess

import pytest
from typer.testing import CliRunner

from aletheia.cli.observe_cmd import observe_app


def test_observe_drift_fetch_failure_is_unavailable(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="auth failed"
        ),
    )

    result = CliRunner().invoke(observe_app, ["drift", "--fetch", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["available"] is False
    assert payload["fetched"] is False
    assert payload["unavailable_reason"] == "git_fetch_failed"
    assert payload["drifted"] is None


def test_observe_drift_success_marks_fetch_observed(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    def fake_run(command, **_kwargs):
        if command[1] == "fetch":
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1:3] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, "head-sha\n", "")
        if command[1:3] == ["rev-parse", "origin/master"]:
            return subprocess.CompletedProcess(command, 0, "master-sha\n", "")
        if command[1:3] == ["rev-list", "--count"]:
            return subprocess.CompletedProcess(command, 0, "2\n", "")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(observe_app, ["drift", "--fetch", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["available"] is True
    assert payload["fetched"] is True
    assert payload["behind"] == 2
    assert payload["ahead"] == 2
    assert payload["drifted"] is False


@pytest.mark.parametrize(
    "failed_command",
    ["origin", "behind", "ahead"],
)
def test_observe_drift_failed_git_reads_never_become_zero(monkeypatch, tmp_path, failed_command):
    (tmp_path / ".git").mkdir()
    monkeypatch.chdir(tmp_path)

    def fake_run(command, **_kwargs):
        if command[1:3] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, "head-sha\n", "")
        if command[1:3] == ["rev-parse", "origin/master"]:
            return subprocess.CompletedProcess(
                command, 1 if failed_command == "origin" else 0,
                "" if failed_command == "origin" else "master-sha\n", "error",
            )
        if command[1:3] == ["rev-list", "--count"]:
            is_behind = "HEAD..origin/master" in command
            failed = failed_command == ("behind" if is_behind else "ahead")
            return subprocess.CompletedProcess(command, 1 if failed else 0, "" if failed else "2\n", "error")
        raise AssertionError(command)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = CliRunner().invoke(observe_app, ["drift", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["available"] is False
    assert payload["behind"] is None
    assert payload["ahead"] is None
    assert payload["drifted"] is None
