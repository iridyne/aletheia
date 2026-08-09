from __future__ import annotations

from pathlib import Path

import yaml


WORKFLOW_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
WORKFLOW_PATH = WORKFLOW_DIR / "ci.yml"
AUTO_MERGE_WORKFLOW_PATH = WORKFLOW_DIR / "auto-merge.yml"
DELETE_MERGED_WORKFLOW_PATH = WORKFLOW_DIR / "delete-merged-branch.yml"
PINNED_ACTION_SHA_LENGTH = 40
GITHUB_SCRIPT_ACTION = "actions/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd"


def _workflow() -> dict:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def _auto_merge_workflow() -> dict:
    return yaml.load(
        AUTO_MERGE_WORKFLOW_PATH.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _delete_merged_workflow() -> dict:
    return yaml.load(
        DELETE_MERGED_WORKFLOW_PATH.read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def _run_commands(job: dict) -> str:
    return "\n".join(
        str(step.get("run") or "")
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def test_only_expected_workflows_are_active() -> None:
    assert sorted(path.name for path in WORKFLOW_DIR.glob("*.yml")) == [
        "auto-merge.yml",
        "ci.yml",
        "delete-merged-branch.yml",
    ]


def test_ci_only_validates_pull_requests_and_merge_queue_entries() -> None:
    triggers = _workflow()["on"]

    assert set(triggers) == {"pull_request", "merge_group"}
    assert triggers["pull_request"]["branches"] == ["master"]


def test_ci_has_two_review_focused_jobs() -> None:
    jobs = _workflow()["jobs"]

    assert set(jobs) == {"review", "tests"}
    assert jobs["review"]["name"] == "Review checks"
    assert jobs["tests"]["name"] == "Python regression"


def test_ci_never_builds_or_distributes_release_artifacts() -> None:
    source = WORKFLOW_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "upload-artifact",
        "deploy-pages",
        "pyinstaller",
        "cargo build",
        "build_bundle",
        "build_packaged_runtime",
        "sign_update_feed",
        "workflow_dispatch",
        "schedule:",
    )

    assert not [token for token in forbidden if token in source]


def test_all_jobs_use_self_hosted_runners_and_read_only_permissions() -> None:
    workflow = _workflow()

    assert workflow["permissions"] == {"contents": "read"}
    for job in workflow["jobs"].values():
        assert job["runs-on"] == ["self-hosted", "Linux", "X64", "ubuntu-24.04"]
        setup_uv = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"))
        assert setup_uv["with"]["enable-cache"] == "false"


def test_third_party_actions_are_pinned_to_full_commit_shas() -> None:
    failures: list[str] = []
    for job_name, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            uses = step.get("uses") if isinstance(step, dict) else None
            if not uses or uses.startswith("./"):
                continue
            reference = uses.rsplit("@", 1)[-1]
            if len(reference) != PINNED_ACTION_SHA_LENGTH or not all(
                character in "0123456789abcdef" for character in reference
            ):
                failures.append(f"{job_name}: {uses}")

    assert not failures, "Actions must use full immutable commit SHAs:\n" + "\n".join(failures)


def test_secret_scanner_download_is_versioned_and_checksum_verified() -> None:
    commands = _run_commands(_workflow()["jobs"]["review"])

    assert "version=8.24.3" in commands
    assert "gitleaks_${version}_linux_x64.tar.gz" in commands
    assert "sha256sum --check" in commands
    assert "9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c" in commands
    assert "--redact" in commands


def test_review_checks_cover_each_language_without_release_builds() -> None:
    commands = _run_commands(_workflow()["jobs"]["review"])

    assert "ruff check" in commands
    assert "test_architecture_fitness.py" in commands
    assert "python scripts/check_docs.py" in commands
    assert "npm run lint" in commands
    assert "npm test" in commands
    assert "npx tsc -b" in commands
    assert "rustup target add" in commands
    assert "x86_64-unknown-linux-musl" in commands
    assert "sudo" not in commands
    assert "cargo test --locked --all-targets" in commands
    assert "cargo build" not in commands


def test_native_host_build_uses_self_contained_linker() -> None:
    """Ensure the Rust compilation step never falls back to the system C linker.

    Historically (runs #814-#826, 2026-07-31) the review job failed because
    `cargo test` tried to link via `cc`, which was absent on some runner nodes.
    The fix pins the musl target to the LLVM self-contained linker (`rust-lld`)
    via RUSTFLAGS.  This test locks that protection in so a later edit cannot
    silently regress to the system-linker path.
    """
    review_steps = _workflow()["jobs"]["review"]["steps"]
    native_step = next(
        (s for s in review_steps if isinstance(s, dict) and "native host" in s.get("name", "")),
        None,
    )
    assert native_step is not None, "'Test the platform-neutral native host' step not found"

    step_env = native_step.get("env", {})
    rustflags = step_env.get("RUSTFLAGS", "")
    assert "linker=rust-lld" in rustflags, (
        f"Native host step must set RUSTFLAGS=-C linker=rust-lld to avoid "
        f"depending on the system C toolchain (got: {rustflags!r})"
    )

    # The step must also target the musl platform so no glibc symbols leak in.
    run = native_step.get("run", "")
    assert "x86_64-unknown-linux-musl" in run, (
        "Native host step must compile for x86_64-unknown-linux-musl"
    )


def test_python_regression_excludes_nightly_simulation_and_isolates_state() -> None:
    job = _workflow()["jobs"]["tests"]
    commands = _run_commands(job)

    assert "for tier in tier_spec tier_unit tier_integration tier_e2e" in commands
    assert "uv run pytest tests/" in commands
    assert "--ignore=tests/simulation" in commands
    assert "--ignore=tests/test_auto_resume.py" in commands
    assert "--ignore=tests/test_operator_cli.py" in commands
    assert "tests/test_auto_resume.py tests/test_operator_cli.py" in commands
    assert "-n 2 --dist loadfile" in commands
    assert "--dist loadfile" in commands
    assert "ALETHEIA_DATA_DIR=$RUNNER_TEMP" in commands
    assert "GITHUB_RUN_ATTEMPT" in commands
    assert "GITHUB_ENV" in commands
    assert "unzip" in commands
    assert "python -m zipfile" in commands
    assert "sudo" not in commands


def test_superseded_pull_request_runs_are_cancelled() -> None:
    concurrency = _workflow()["concurrency"]

    assert concurrency["cancel-in-progress"] == "true"
    assert "github.event.pull_request.number" in concurrency["group"]


def test_auto_merge_hands_off_pr_status_before_mutating_without_running_pr_code() -> None:
    workflow = _auto_merge_workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"pull_request_target", "repository_dispatch"}
    assert triggers["pull_request_target"]["branches"] == ["master"]
    assert triggers["pull_request_target"]["types"] == [
        "opened",
        "reopened",
        "ready_for_review",
        "edited",
        "synchronize",
        "labeled",
        "unlabeled",
    ]
    assert triggers["repository_dispatch"]["types"] == ["auto_merge_update"]
    assert workflow["permissions"] == {"contents": "read"}

    assert set(workflow["jobs"]) == {"schedule", "update"}
    schedule = workflow["jobs"]["schedule"]
    assert schedule["name"] == "Schedule auto-merge update"
    assert schedule["runs-on"] == [
        "self-hosted",
        "Linux",
        "X64",
        "ubuntu-24.04",
    ]
    assert "event_name == 'pull_request_target'" in schedule["if"]
    assert "draft == false" in schedule["if"]
    assert schedule["permissions"] == {"contents": "write"}
    assert set(schedule["env"]) == {"OPERATION", "PR_NUMBER", "EXPECTED_HEAD_SHA"}
    assert "do-not-auto-merge" in schedule["env"]["OPERATION"]
    assert len(schedule["steps"]) == 1
    schedule_step = schedule["steps"][0]
    assert schedule_step["name"] == "Dispatch trusted auto-merge update"
    assert schedule_step["uses"] == GITHUB_SCRIPT_ACTION
    assert schedule_step["with"]["retries"] == "3"
    schedule_script = schedule_step["with"]["script"]
    assert "createDispatchEvent" in schedule_script
    assert "auto_merge_update" in schedule_script
    assert "pull_request_number" in schedule_script
    assert "process.env.OPERATION" in schedule_script

    update = workflow["jobs"]["update"]
    assert update["name"] == "Apply auto-merge update"
    assert update["runs-on"] == [
        "self-hosted",
        "Linux",
        "X64",
        "ubuntu-24.04",
    ]
    assert "event_name == 'repository_dispatch'" in update["if"]
    assert update["permissions"] == {
        "contents": "write",
        "pull-requests": "write",
    }
    assert update["timeout-minutes"] == "15"
    assert set(update["env"]) == {"OPERATION", "PR_NUMBER", "EXPECTED_HEAD_SHA"}
    assert len(update["steps"]) == 1
    update_step = update["steps"][0]
    assert update_step["name"] == "Apply pull request auto-merge state"
    assert update_step["uses"] == GITHUB_SCRIPT_ACTION
    assert update_step["with"]["retries"] == "3"
    update_script = update_step["with"]["script"]
    assert "repository(owner:" in update_script
    assert "pullRequest(number:" in update_script
    assert "mergeStateStatus" in update_script
    assert "autoMergeRequest" in update_script
    assert "UNSTABLE" in update_script
    assert "setTimeout" in update_script
    assert "enablePullRequestAutoMerge" in update_script
    assert "disablePullRequestAutoMerge" in update_script

    # Both phases carry write-capable base-repository tokens. Keep them
    # metadata-only: never check out or execute pull-request content.
    assert not any(
        str(step.get("uses", "")).startswith("actions/checkout@")
        or bool(step.get("run"))
        for workflow_job in workflow["jobs"].values()
        for step in workflow_job["steps"]
    )


def test_auto_merge_clean_state_uses_exact_head_protected_squash() -> None:
    workflow = _auto_merge_workflow()
    schedule = workflow["jobs"]["schedule"]
    update = workflow["jobs"]["update"]

    assert set(schedule["env"]) == {"OPERATION", "PR_NUMBER", "EXPECTED_HEAD_SHA"}
    assert "pull_request.head.sha" in schedule["env"]["EXPECTED_HEAD_SHA"]
    schedule_script = schedule["steps"][0]["with"]["script"]
    assert "expected_head_sha" in schedule_script

    assert set(update["env"]) == {"OPERATION", "PR_NUMBER", "EXPECTED_HEAD_SHA"}
    update_script = update["steps"][0]["with"]["script"]
    assert "headRefOid" in update_script
    assert "pullRequest.headRefOid !== expectedHeadSha" in update_script
    assert "pullRequest.mergeStateStatus === 'CLEAN'" in update_script
    assert "github.rest.pulls.merge" in update_script
    assert "sha: expectedHeadSha" in update_script
    assert "merge_method: 'squash'" in update_script
    assert "const autoMergeStates = new Set(['BEHIND', 'BLOCKED', 'HAS_HOOKS'])" in update_script
    assert "enablePullRequestAutoMerge" in update_script
    assert "mergeMethod: SQUASH" in update_script
    assert "mergeMethod: MERGE" not in update_script
    assert "String(error).includes('clean status')" in update_script
    assert "admin" not in update_script.lower()
    assert "bypass" not in update_script.lower()


def test_delete_merged_branch_only_touches_merged_same_repo_head_refs() -> None:
    workflow = _delete_merged_workflow()
    triggers = workflow["on"]

    assert set(triggers) == {"pull_request_target"}
    assert triggers["pull_request_target"]["types"] == ["closed"]
    assert workflow["permissions"] == {"contents": "write"}

    assert set(workflow["jobs"]) == {"delete"}
    job = workflow["jobs"]["delete"]
    assert job["runs-on"] == ["self-hosted", "Linux", "X64", "ubuntu-24.04"]

    guard = job["if"]
    # Three guards must all be present or the workflow risks deleting the
    # wrong branch. Any change to these strings should be intentional.
    assert "merged == true" in guard
    assert "head.repo.full_name == github.repository" in guard
    assert "head.ref != github.event.repository.default_branch" in guard

    # The job must not check out any untrusted code from the PR — it only
    # invokes the REST API.
    assert not any("uses" in step for step in job["steps"])

    commands = _run_commands(job)
    assert "gh api -X DELETE" in commands
    assert "repos/$GITHUB_REPOSITORY/git/refs/heads/$BRANCH" in commands
