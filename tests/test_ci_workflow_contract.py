from __future__ import annotations

from pathlib import Path

import yaml


_WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    # BaseLoader preserves the literal YAML key `on` instead of applying YAML 1.1
    # boolean coercion. GitHub Actions uses `on` as a string key.
    return yaml.load(_WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _run_commands(job: dict) -> str:
    return "\n".join(
        str(step.get("run") or "")
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def test_ci_triggers_pull_requests_master_pushes_and_manual_regression() -> None:
    workflow = _workflow()
    triggers = workflow["on"]

    assert triggers["pull_request"]["branches"] == ["master"]
    assert triggers["push"]["branches"] == ["master"]
    assert triggers["workflow_dispatch"]["inputs"]["full_regression"]["type"] == "boolean"


def test_master_pushes_cannot_be_cancelled_by_a_later_push() -> None:
    concurrency = _workflow()["concurrency"]
    group = str(concurrency["group"])
    cancel = str(concurrency["cancel-in-progress"])

    assert "github.event.pull_request.number" in group
    assert "github.sha" in group
    assert "github.event_name == 'pull_request'" in cancel


def test_operator_shell_has_an_independent_test_and_build_gate() -> None:
    job = _workflow()["jobs"]["operator-shell"]
    commands = _run_commands(job)

    assert job["runs-on"] == "ubuntu-24.04"
    assert "operator-shell" in str(job)
    assert "npm ci" in commands
    assert "npm test" in commands
    assert "npm run build" in commands


def test_active_paper_chain_is_an_independent_required_surface() -> None:
    job = _workflow()["jobs"]["active-paper-chain"]
    commands = _run_commands(job)

    assert "test_active_mainline_golden_path.py" in commands
    assert "test_pipeline_real_paper_roundtrip.py" in commands
    assert "test_pipeline_futures_leverage.py" in commands
    assert "test_pipeline_feedback_loop.py" in commands
    assert "test_ci_execution_contracts.py" in commands
    assert "continue-on-error" not in str(job)


def test_windows_runtime_smoke_runs_platform_specific_contracts() -> None:
    job = _workflow()["jobs"]["windows-runtime-smoke"]
    commands = _run_commands(job)

    assert job["runs-on"] == "windows-latest"
    assert "test_event_loop_policy.py" in commands
    assert "test_pidfile.py" in commands
    assert "continue-on-error" not in str(job)


def test_full_regression_is_a_separate_manual_job_with_realistic_timeout() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    backend_commands = _run_commands(jobs["backend"])
    full = jobs["full-regression"]

    assert "pytest tests/" not in backend_commands
    assert int(full["timeout-minutes"]) >= 45
    assert "workflow_dispatch" in str(full["if"])
    assert "inputs.full_regression" in str(full["if"])
    assert "uv run pytest tests/" in _run_commands(full)


def test_master_integrity_check_is_full_state_not_cancellable_increment_only() -> None:
    backend_commands = _run_commands(_workflow()["jobs"]["backend"])

    assert "github.event_name" in backend_commands
    assert "--changed-since" in backend_commands
    assert "else" in backend_commands
    assert "workflow check --strict" in backend_commands
