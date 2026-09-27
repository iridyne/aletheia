from __future__ import annotations

import json

import pytest

import scripts.ci_local as ci_local
from scripts.ci_plan import PathChange


def test_local_ci_reports_failure_when_required_check_fails(monkeypatch, tmp_path, capsys) -> None:
    body = tmp_path / "body.md"
    body.write_text("References #1\n\n## SDD Depth\n\nD1\n\n## Contract Delta\n\nMODIFIED: test.\n", encoding="utf-8")

    monkeypatch.setattr(
        ci_local.ci_plan,
        "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="scripts/ci_plan.py")]),
    )

    calls = []

    def fake_run(command, cwd=None):
        calls.append(command)
        return {
            "command": command,
            "working_directory": cwd or ".",
            "exit_code": 1 if any("test_ci_plan.py" in part for part in command) else 0,
            "duration_seconds": 0.01,
        }

    monkeypatch.setattr(ci_local, "_run", fake_run)

    assert ci_local.main(["--pr-body-file", str(body)]) == 1
    output = capsys.readouterr().out
    assert '"result": "fail"' in output
    assert calls[0][:5] == ["uv", "run", "--no-sync", "python", "scripts/ci_preflight.py"]
    assert ["git", "diff", "--quiet", "HEAD", "--"] in calls
    assert ["git", "diff", "--check", f"{'b' * 40}...{'a' * 40}", "--"] in calls
    assert any(any("test_ci_plan.py" in part for part in command) for command in calls)


def test_local_ci_planned_mode_lists_planner_required_checks(monkeypatch, tmp_path, capsys) -> None:
    body = tmp_path / "body.md"
    body.write_text("References #1", encoding="utf-8")
    monkeypatch.setattr(
        ci_local.ci_plan,
        "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="frontend/src/App.tsx")]),
    )

    assert ci_local.main(["--pr-body-file", str(body), "--skip-execute"]) == 0
    output = capsys.readouterr().out
    assert '"result": "planned"' in output
    assert '"frontend tests"' in output


def test_local_ci_changed_file_override_still_binds_head_identity(monkeypatch, tmp_path, capsys) -> None:
    body = tmp_path / "body.md"
    body.write_text("References #1", encoding="utf-8")
    base = "b" * 40
    head = "a" * 40

    def fake_git(args):
        if args == ["rev-parse", "origin/master"]:
            return base
        if args == ["rev-parse", "HEAD"]:
            return head
        raise AssertionError(args)

    monkeypatch.setattr(ci_local.ci_plan, "_git", fake_git)

    assert ci_local.main([
        "--pr-body-file",
        str(body),
        "--base-ref",
        "origin/master",
        "--changed-file",
        "scripts/ci_plan.py",
        "--skip-execute",
    ]) == 0
    output = capsys.readouterr().out
    assert f'"base_sha": "{base}"' in output
    assert f'"head_sha": "{head}"' in output


def test_local_ci_rejects_dirty_tracked_worktree(monkeypatch, tmp_path, capsys) -> None:
    body = tmp_path / "body.md"
    body.write_text("References #1", encoding="utf-8")
    monkeypatch.setattr(
        ci_local.ci_plan,
        "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="scripts/ci_plan.py")]),
    )

    def fake_run(command, cwd=None):
        return {
            "command": command,
            "working_directory": cwd or ".",
            "exit_code": 1 if command == ["git", "diff", "--quiet", "HEAD", "--"] else 0,
            "duration_seconds": 0.01,
        }

    monkeypatch.setattr(ci_local, "_run", fake_run)

    assert ci_local.main(["--pr-body-file", str(body)]) == 1
    output = capsys.readouterr().out
    assert '"result": "fail"' in output
    assert '"failure_reason": "clean tracked worktree failed"' in output


@pytest.mark.parametrize("path", ["core/aletheia/execution/gateway.py", "scripts/ci_plan.py"])
def test_every_required_proof_lane_has_an_executor_or_explicit_unavailability(path):
    report = ci_local.ci_plan.plan(
        event="local", base_sha="b" * 40, head_sha="a" * 40,
        changes=[PathChange(path=path)],
    )
    required = {lane for lane, needed in report["required"].items() if needed}
    covered = {check.get("lane") for check in report["local_checks"]}
    assert required - {"trusted_ci"} <= covered
    for check in report["local_checks"]:
        if check.get("lane") in required:
            assert check.get("command") or check.get("unavailable_reason")


def test_full_proof_is_unavailable_not_a_passing_print_command(monkeypatch, tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("References #3289")
    monkeypatch.setattr(
        ci_local.ci_plan, "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="scripts/ci_plan.py")]),
    )
    commands = []

    def run_command(command, cwd=None):
        commands.append(command)
        return {"command": command, "exit_code": 0, "duration_seconds": 0.01}

    monkeypatch.setattr(ci_local, "_run", run_command)
    assert ci_local.main(["--pr-body-file", str(body)]) != 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["result"] == "required_but_unavailable"
    assert receipt["required"]["full_ci"] is True
    missing = [check for check in receipt["checks"] if check.get("status") == "required_but_unavailable"]
    assert [check["lane"] for check in missing] == ["full_ci"]
    assert "Fedora" in missing[0]["unavailable_reason"]
    assert all(command[0] not in {"npm", "cargo"} for command in commands)


def test_affected_python_failure_stops_the_receipt(monkeypatch, tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("References #3289")
    monkeypatch.setattr(
        ci_local.ci_plan, "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="core/aletheia/cli/control_cmd.py")]),
    )

    def run_command(command, cwd=None):
        return {
            "command": command, "working_directory": cwd or ".",
            "exit_code": 3 if "tier_unit" in command else 0, "duration_seconds": 0.01,
        }

    monkeypatch.setattr(ci_local, "_run", run_command)
    assert ci_local.main(["--pr-body-file", str(body)]) == 3
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["result"] == "fail"
    assert receipt["checks"][-1]["status"] == "fail"
    assert receipt["checks"][-1]["lane"] == "python_unit"
    assert receipt["checks"][-1]["exit_code"] == 3
    assert receipt["base_sha"] == "b" * 40
    assert receipt["head_sha"] == "a" * 40


def test_deep_receipt_refuses_unisolated_exhaustive_proof(monkeypatch, tmp_path, capsys):
    body = tmp_path / "body.md"
    body.write_text("References #3289")
    monkeypatch.setattr(
        ci_local.ci_plan, "_load_changes",
        lambda _args: ("b" * 40, "a" * 40, [PathChange(path="core/aletheia/execution/gateway.py")]),
    )

    def run_command(command, cwd=None):
        return {"command": command, "exit_code": 0, "duration_seconds": 0.01}

    monkeypatch.setattr(ci_local, "_run", run_command)
    assert ci_local.main(["--pr-body-file", str(body)]) == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["result"] == "required_but_unavailable"
    assert receipt["checks"][-1]["lane"] == "python_unit"
    assert "Fedora" in receipt["checks"][-1]["unavailable_reason"]
