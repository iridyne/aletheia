from __future__ import annotations

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
