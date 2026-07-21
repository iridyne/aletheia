from __future__ import annotations

from pathlib import Path

import yaml


_WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_WORKFLOW_PATH = _WORKFLOW_DIR / "ci.yml"
_REQUIRED_GATE_NEEDS = {
    "secrets-scan",
    "backend",
    "fast-tests",
    "integration-tests",
    "active-paper-chain",
    "frontend",
    "operator-shell",
    "windows-runtime-smoke",
}


def _load_workflow(path: Path) -> dict:
    # BaseLoader preserves the literal YAML key `on` instead of applying YAML 1.1
    # boolean coercion. GitHub Actions uses `on` as a string key.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _workflow() -> dict:
    return _load_workflow(_WORKFLOW_PATH)


def _run_commands(job: dict) -> str:
    return "\n".join(
        str(step.get("run") or "")
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def _runner_targets(job: dict) -> list[object]:
    runs_on = job.get("runs-on")
    if isinstance(runs_on, list):
        return [runs_on]
    if not isinstance(runs_on, str):
        return []
    if "matrix.runner" not in runs_on:
        return [runs_on]

    matrix = job.get("strategy", {}).get("matrix", {})
    include = matrix.get("include", []) if isinstance(matrix, dict) else []
    return [
        entry["runner"]
        for entry in include
        if isinstance(entry, dict) and "runner" in entry
    ]


def test_all_workflow_jobs_target_self_hosted_runners() -> None:
    failures: list[str] = []
    workflow_paths = sorted((*_WORKFLOW_DIR.glob("*.yml"), *_WORKFLOW_DIR.glob("*.yaml")))

    assert workflow_paths, "No GitHub Actions workflows were found."

    for path in workflow_paths:
        jobs = _load_workflow(path).get("jobs", {})
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                failures.append(f"{path.name}:{job_name} is not a job mapping")
                continue
            targets = _runner_targets(job)
            if not targets:
                failures.append(f"{path.name}:{job_name} has no verifiable runs-on target")
                continue
            for target in targets:
                rendered = " ".join(target) if isinstance(target, list) else str(target)
                if "self-hosted" not in rendered:
                    failures.append(
                        f"{path.name}:{job_name} targets GitHub-hosted or unknown runner {rendered!r}"
                    )

    assert not failures, "All workflow jobs must use repository self-hosted runners:\n" + "\n".join(failures)


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


def test_manual_dispatch_does_not_share_a_pending_group_with_same_sha_push() -> None:
    group = str(_workflow()["concurrency"]["group"])

    assert "format('ci-{0}-{1}-{2}'" in group
    assert "github.workflow, github.event_name, github.sha" in group


def test_operator_shell_has_an_independent_test_and_build_gate() -> None:
    job = _workflow()["jobs"]["operator-shell"]
    commands = _run_commands(job)

    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "ubuntu-24.04"]
    assert "working-directory" in str(job)
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

    assert job["runs-on"] == ["self-hosted", "Windows", "X64"]
    assert "test_event_loop_policy.py" in commands
    assert "test_pidfile.py" in commands
    assert "continue-on-error" not in str(job)


def test_pr_ci_gate_aggregates_the_exact_required_matrix() -> None:
    job = _workflow()["jobs"]["pr-ci-gate"]
    commands = _run_commands(job)
    condition = str(job["if"])

    assert job["name"] == "PR CI gate"
    assert set(job["needs"]) == _REQUIRED_GATE_NEEDS
    assert "always()" in condition
    assert "github.event_name == 'pull_request'" in condition
    for dependency in _REQUIRED_GATE_NEEDS:
        assert f"needs.{dependency}.result" in commands
    assert "continue-on-error" not in str(job)


def test_master_verification_gate_aggregates_the_exact_required_matrix() -> None:
    job = _workflow()["jobs"]["master-verification-gate"]
    commands = _run_commands(job)
    condition = str(job["if"])

    assert job["name"] == "Master verification gate"
    assert set(job["needs"]) == _REQUIRED_GATE_NEEDS
    assert "always()" in condition
    assert "github.event_name == 'push'" in condition
    assert "refs/heads/master" in condition
    for dependency in _REQUIRED_GATE_NEEDS:
        assert f"needs.{dependency}.result" in commands
    assert "continue-on-error" not in str(job)


def test_full_regression_is_a_separate_manual_job_with_realistic_timeout() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    backend_commands = _run_commands(jobs["backend"])
    full = jobs["full-regression"]

    assert "uv run pytest tests/ -q" not in backend_commands
    assert int(full["timeout-minutes"]) >= 45
    assert "workflow_dispatch" in str(full["if"])
    assert "inputs.full_regression" in str(full["if"])
    assert "uv run pytest tests/" in _run_commands(full)


def test_repository_task_subsystem_is_absent_from_ci() -> None:
    workflow_text = _WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "agent workflow" not in workflow_text.lower()
    obsolete_test_name = "test_agent_" + "workflow"
    assert obsolete_test_name not in workflow_text
    assert ".agent/" not in workflow_text
    assert "--changed-since" not in workflow_text
