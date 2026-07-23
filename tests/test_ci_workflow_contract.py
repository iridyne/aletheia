from __future__ import annotations

from pathlib import Path

import yaml


_WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
_WORKFLOW_PATH = _WORKFLOW_DIR / "ci.yml"

# All jobs that ci-gate lists in its `needs` array
_REQUIRED_GATE_NEEDS = {
    "changes",
    "e2e",
    "unit",
    "integration",
    "simulation",
    "specs",
    "operator-shell",
    "native",
    "frontend",
    "static-checks",
    "secrets-scan",
}

# Python + static surfaces: must succeed (no skip tolerance)
_STRICT_SURFACES = frozenset({
    "e2e", "unit", "integration", "simulation", "specs",
    "static-checks", "secrets-scan",
})

# Path-gated surfaces: success or skipped both accepted by the gate
_SKIP_OK_SURFACES = frozenset({"operator-shell", "native", "frontend"})


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


def test_changes_job_detects_path_areas_for_pull_requests() -> None:
    job = _workflow()["jobs"]["changes"]
    commands = _run_commands(job)
    outputs = job.get("outputs", {})

    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "ubuntu-24.04"]
    assert "native" in outputs
    assert "frontend" in outputs
    assert "operator_shell" in outputs
    assert "pull_request" in commands
    assert "GITHUB_OUTPUT" in commands


def test_native_frontend_operator_shell_are_gated_on_path_changes() -> None:
    jobs = _workflow()["jobs"]
    for surface in ("native", "frontend", "operator-shell"):
        job = jobs[surface]
        assert "changes" in job.get("needs", []), f"{surface} must need the changes job"
        assert "changes.outputs" in str(job.get("if", "")), f"{surface} must have a path-change if condition"


def test_operator_shell_has_an_independent_test_and_build_gate() -> None:
    job = _workflow()["jobs"]["operator-shell"]
    commands = _run_commands(job)

    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "ubuntu-24.04"]
    assert "working-directory" in str(job)
    assert "operator-shell" in str(job)
    assert "npm ci" in commands
    assert "npm test" in commands
    assert "npm run build" in commands


def test_e2e_job_covers_the_full_pipeline_directory() -> None:
    job = _workflow()["jobs"]["e2e"]
    commands = _run_commands(job)

    assert "tests/e2e/" in commands
    assert "continue-on-error" not in str(job)


def test_ci_gate_aggregates_the_exact_required_matrix() -> None:
    job = _workflow()["jobs"]["ci-gate"]
    commands = _run_commands(job)
    condition = str(job["if"])

    assert job["name"] == "CI gate"
    assert set(job["needs"]) == _REQUIRED_GATE_NEEDS
    assert "always()" in condition
    assert "github.event_name == 'pull_request'" in condition
    assert "refs/heads/master" in condition
    # Strict surfaces must require success
    for surface in _STRICT_SURFACES:
        assert f"needs.{surface}.result" in commands, f"gate missing strict check for {surface}"
    # Path-gated surfaces must be checked but allow skipped
    for surface in _SKIP_OK_SURFACES:
        assert f"needs.{surface}.result" in commands, f"gate missing check for {surface}"
    assert "skipped" in commands, "gate must tolerate skipped for path-gated surfaces"
    assert "continue-on-error" not in str(job)


def test_full_regression_is_a_separate_manual_job_with_realistic_timeout() -> None:
    workflow = _workflow()
    jobs = workflow["jobs"]
    full = jobs["full-regression"]

    assert int(full["timeout-minutes"]) >= 45
    assert "workflow_dispatch" in str(full["if"])
    assert "inputs.full_regression" in str(full["if"])
    assert "uv run pytest tests/" in _run_commands(full)


def test_python_test_jobs_set_scoped_data_dir() -> None:
    """Python CI jobs must isolate runtime state via ALETHEIA_DATA_DIR (#205).

    runner.temp is unavailable while GitHub evaluates job-level env, so each
    job must export a unique runtime path before checkout or dependency setup.
    """
    jobs = _workflow()["jobs"]
    python_test_jobs = {"e2e", "unit", "integration", "simulation", "specs", "full-regression"}
    failures: list[str] = []
    for job_name in python_test_jobs:
        job = jobs.get(job_name)
        if job is None:
            failures.append(f"{job_name}: job missing from workflow")
            continue
        job_env = job.get("env") or {}
        if not isinstance(job_env, dict):
            failures.append(f"{job_name}: env block is not a mapping")
            continue
        if "runner." in str(job_env):
            failures.append(f"{job_name}: runner context is invalid in job-level env")

        steps = job.get("steps", [])
        setup_steps = [
            (index, str(step.get("run") or ""))
            for index, step in enumerate(steps)
            if isinstance(step, dict) and "ALETHEIA_DATA_DIR" in str(step.get("run") or "")
        ]
        if not setup_steps:
            failures.append(f"{job_name}: missing ALETHEIA_DATA_DIR runtime setup step")
            continue
        setup_index, command = setup_steps[0]
        if setup_index != 0:
            failures.append(f"{job_name}: ALETHEIA_DATA_DIR must be exported by the first step")
        for required in ("RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_JOB", "GITHUB_ENV"):
            if required not in command:
                failures.append(f"{job_name}: data-dir setup missing {required}")
    assert not failures, (
        "Python test jobs must isolate runtime state via ALETHEIA_DATA_DIR:\n"
        + "\n".join(failures)
    )


def test_native_host_tray_feature_split() -> None:
    """Linux CI must not compile aletheia-tray; macOS build must explicitly enable the tray feature.

    The tray binary requires macOS GUI dependencies (tray-icon / winit / muda) that
    cannot compile on Linux.  The Cargo `tray` feature gates both the binary (via
    required-features) and its optional deps.  This split must not silently regress:

    - ci.yml native job: no `--features tray` — aletheia-tray is skipped on Linux.
    - build-packaged-runtime.yml macOS job: `--features tray` so aletheia-tray is built.
    """
    # Linux CI (ci.yml) native job must NOT pass --features tray
    native_job = _workflow()["jobs"]["native"]
    linux_cargo_commands = _run_commands(native_job)
    assert "--features tray" not in linux_cargo_commands, (
        "ci.yml native job must not pass '--features tray' — Linux CI must skip aletheia-tray "
        "via required-features, not by opting into the tray feature"
    )
    assert "cargo test" in linux_cargo_commands, (
        "ci.yml native job must still run cargo test for platform-neutral targets"
    )

    # macOS build workflow must explicitly enable tray
    macos_wf_path = _WORKFLOW_DIR / "build-packaged-runtime.yml"
    macos_wf = _load_workflow(macos_wf_path)
    macos_job = macos_wf.get("jobs", {}).get("macos-package", {})
    macos_commands = _run_commands(macos_job)
    assert "--features tray" in macos_commands, (
        "build-packaged-runtime.yml macos-package job must pass '--features tray' "
        "so aletheia-tray is compiled and the tray smoke test can run"
    )


def test_repository_task_subsystem_is_absent_from_ci() -> None:
    workflow_text = _WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "agent workflow" not in workflow_text.lower()
    obsolete_test_name = "test_agent_" + "workflow"
    assert obsolete_test_name not in workflow_text
    assert ".agent/" not in workflow_text
    assert "--changed-since" not in workflow_text
