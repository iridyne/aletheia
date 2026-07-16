from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.agent_workflow import (
    bootstrap,
    claim_task,
    complete_task,
    create_task,
    recovery_test,
    update_task,
    validate,
    verify_task,
)
from aletheia.cli.agent_workflow import workflow_app
from aletheia.runtime_paths import workflow_repository_id, workflow_tasks_db


def _run(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def _repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".agent").mkdir(parents=True)
    (root / ".agent/project.yaml").write_text(
        "schema_version: 1\nrepository: test-repo\n",
        encoding="utf-8",
    )
    (root / ".agent/policy.yaml").write_text(
        """schema_version: 1
trust_boundaries:
  - id: risk
    paths:
      - aletheia/risk/**
    required_command_keys:
      - spec_risk_engine
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    _run(root, "config", "user.email", "test@example.com")
    _run(root, "config", "user.name", "Test")
    (root / "README.md").write_text("x\n", encoding="utf-8")
    _run(root, "add", ".")
    _run(root, "commit", "-m", "init")

    runtime = tmp_path / "runtime"
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", runtime)
    monkeypatch.chdir(root)
    return root


def test_create_task_is_machine_local_and_keeps_git_clean(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = create_task(
        "TASK-LOCAL-1",
        title="Local coordination",
        intent="Keep sibling agents from duplicating work",
        scope_boundary="agent workflow only",
        scope_excluded="trading runtime",
        acceptance=["bootstrap recovers this task"],
        next_action="Add the focused regression test.",
        root=root,
    )

    assert task["status"] == "ready"
    assert _run(root, "status", "--porcelain") == ""
    assert not (root / ".agent/tasks/TASK-LOCAL-1.yaml").exists()
    assert not (root / ".agent/handoffs/TASK-LOCAL-1.md").exists()


def test_claim_and_bootstrap_use_local_branch_context(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    create_task(
        "TASK-LOCAL-2",
        title="Claim task",
        intent="Recover current local work",
        next_action="Inspect the target module.",
        root=root,
    )
    claimed = claim_task("TASK-LOCAL-2", holder="codex", root=root)
    payload = bootstrap(root)

    assert claimed["status"] == "in_progress"
    assert claimed["branch"] == _run(root, "branch", "--show-current")
    assert payload["task"]["id"] == "TASK-LOCAL-2"
    assert payload["task_source"] == "local_active"
    assert payload["evidence"]["freshness"] == "ci-owned"
    assert Path(payload["task_store"]).is_file()


def test_no_active_task_is_a_valid_recoverable_state(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    payload = bootstrap(root)

    assert payload["task"] is None
    assert payload["task_match_count"] == 0
    assert recovery_test(root)["recoverable"] is True


def test_completion_needs_no_handoff_or_embedded_test_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    create_task(
        "TASK-LOCAL-3",
        title="Complete locally",
        intent="Release local ownership after Git work is done",
        root=root,
    )
    claim_task("TASK-LOCAL-3", holder="claude", root=root)
    completed = complete_task("TASK-LOCAL-3", root=root)

    assert completed["status"] == "done"
    assert completed["branch"] is None
    assert completed["worker_id"] is None
    assert _run(root, "status", "--porcelain") == ""


def test_update_keeps_only_small_recovery_context(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    create_task(
        "TASK-LOCAL-4",
        title="Update context",
        intent="Initial intent",
        root=root,
    )
    updated = update_task(
        "TASK-LOCAL-4",
        intent="Narrow intent",
        scope_boundary="CLI and local task DB",
        scope_excluded="Git metadata and trading runtime",
        acceptance=["No repository task files are written"],
        next_action="Run workflow tests.",
        blockers=["None"],
        root=root,
    )

    assert updated["intent"] == "Narrow intent"
    assert updated["scope"]["boundary"] == "CLI and local task DB"
    assert updated["acceptance"] == ["No repository task files are written"]
    assert updated["next_action"] == "Run workflow tests."


def test_secret_or_runtime_facts_are_rejected(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="secret-like or runtime-state"):
        create_task(
            "TASK-LOCAL-5",
            title="Unsafe",
            intent="api_key=should-not-be-stored",
            root=root,
        )


def test_changed_range_reports_trust_boundary_without_git_task_gate(
    tmp_path, monkeypatch
):
    root = _repo(tmp_path, monkeypatch)
    base = _run(root, "rev-parse", "HEAD")
    (root / "aletheia/risk").mkdir(parents=True)
    (root / "aletheia/risk/example.py").write_text("VALUE = 1\n", encoding="utf-8")
    _run(root, "add", ".")
    _run(root, "commit", "-m", "touch risk path")

    findings = validate(root, strict=True, changed_since=base)
    assert not any(item.severity == "error" for item in findings)
    assert any("trust-boundary 'risk' touched" in item.message for item in findings)
    assert not any("task packet" in item.message.lower() for item in findings)
    assert not any("handoff" in item.message.lower() for item in findings)


def test_old_verification_ledger_command_fails_closed(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="does not record verification evidence"):
        verify_task("TASK-OLD", command_key="specs", root=root)


def test_cli_create_claim_complete_does_not_touch_git(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    runner = CliRunner()

    created = runner.invoke(
        workflow_app,
        [
            "task",
            "create",
            "TASK-CLI",
            "--title",
            "CLI task",
            "--intent",
            "Prove local-only workflow",
            "--next-action",
            "Claim it.",
        ],
    )
    assert created.exit_code == 0, created.stdout

    claimed = runner.invoke(
        workflow_app,
        ["task", "claim", "TASK-CLI", "--holder", "codex"],
    )
    assert claimed.exit_code == 0, claimed.stdout

    completed = runner.invoke(workflow_app, ["task", "complete", "TASK-CLI"])
    assert completed.exit_code == 0, completed.stdout
    assert "no Git metadata" in completed.stdout
    assert _run(root, "status", "--porcelain") == ""


def test_cli_bootstrap_succeeds_without_an_active_task(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    result = CliRunner().invoke(workflow_app, ["bootstrap"])

    assert result.exit_code == 0, result.stdout
    assert "Task: none for this branch" in result.stdout
    assert "Verification: Git/PR/CI" in result.stdout


def test_check_staged_is_advisory(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        workflow_app,
        ["check-staged", "--paths-stdin"],
        input="aletheia/risk/engine.py\n",
    )

    assert result.exit_code == 0, result.stdout
    assert "spec_risk_engine" in result.stdout


def test_sibling_worktrees_share_one_task_store(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    _run(root, "branch", "worker-branch")
    worktree = tmp_path / "worker"
    _run(root, "worktree", "add", str(worktree), "worker-branch")

    assert workflow_repository_id(root) == workflow_repository_id(worktree)
    import aletheia.config.loader as loader

    assert workflow_tasks_db(root, loader.DATA_DIR) == workflow_tasks_db(
        worktree, loader.DATA_DIR
    )

    create_task(
        "TASK-SHARED",
        title="Shared local task",
        intent="Coordinate sibling worktrees",
        root=root,
    )
    claimed = claim_task(
        "TASK-SHARED",
        holder="worker-agent",
        root=worktree,
    )
    assert claimed["branch"] == "worker-branch"
    assert bootstrap(worktree)["task"]["id"] == "TASK-SHARED"


def test_one_branch_cannot_hold_two_active_tasks(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    for task_id in ("TASK-A", "TASK-B"):
        create_task(
            task_id,
            title=task_id,
            intent="Parallelism guard",
            root=root,
        )
    claim_task("TASK-A", holder="agent-a", root=root)

    with pytest.raises(ValueError, match="branch already has an active task"):
        claim_task("TASK-B", holder="agent-b", root=root)
