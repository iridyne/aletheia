from __future__ import annotations

import subprocess
import hashlib
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from aletheia.cli.agent_workflow import workflow_app
from aletheia.agent_workflow import (
    bootstrap,
    claim_task,
    complete_task,
    create_task,
    rebase_base,
    revalidate_completed_task,
    recovery_test,
    update_task,
    validate,
    verify_task,
    write_handoff,
)


def _handoff() -> str:
    return """# TASK-1 handoff

## Purpose

Prove cold recovery.

## Changes made

- Added the requested workflow behavior.

## Uncommitted work

- Task packet and this handoff only.

## Verification

- Registered workflow test passed.

## Remaining work

- None.

## Exact next action

- Run workflow bootstrap before claiming another task.

## Blockers

- None.

## Risks

- None known.
"""


def _repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".agent/tasks").mkdir(parents=True)
    (root / ".agent/handoffs").mkdir()
    (root / ".agent/project.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / ".agent/commands.yaml").write_text(
        "commands:\n"
        "  test:\n"
        "    command: git status --short\n"
        "  workflow_bootstrap:\n"
        "    command: uv run aletheia agent workflow bootstrap\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    task = {"schema_version": 1, "handoff_version": 1, "id": "TASK-1", "title": "Task", "status": "in_progress", "scope": {"boundary": "docs"}, "intent": "test", "acceptance": ["works"], "ownership": {"branch": branch}, "base_commit": head, "next_action": {"command_key": "test", "reason": "run test"}, "blockers": [], "verification": [], "handoff": ".agent/handoffs/TASK-1.md"}
    (root / ".agent/tasks/TASK-1.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    (root / ".agent/handoffs/TASK-1.md").write_text("# handoff\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def test_bootstrap_selects_current_branch_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    payload = bootstrap(root)
    assert payload["task"]["id"] == "TASK-1"
    assert payload["task_match_count"] == 1
    assert payload["dirty"] is True  # the freshly-created task packet is uncommitted


def test_done_task_requires_current_successful_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert any("no successful verification" in x.message for x in validate(root, strict=True))


def test_secret_like_task_metadata_is_rejected(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["notes"] = "api_key=not-allowed"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert any("forbidden" in x.message for x in validate(root))


def test_task_identity_and_handoff_path_must_match_filename(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["id"] = "TASK-DIFFERENT"
    task["handoff"] = ".agent/handoffs/TASK-WRONG.md"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    messages = {finding.message for finding in validate(root)}
    assert "task ID does not match its filename" in messages
    assert "handoff path does not match task ID" in messages


def test_claim_and_handoff_only_change_selected_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "ready"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    claimed = claim_task("TASK-1", holder="fresh-agent", root=root)
    assert claimed["status"] == "in_progress"
    handoff = write_handoff("TASK-1", _handoff(), root=root)
    assert handoff.read_text(encoding="utf-8") == _handoff()
    assert recovery_test(root)["recoverable"] is True


def test_claim_cli_reports_success_without_yaml_ownership(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "ready"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    result = CliRunner().invoke(
        workflow_app, ["task", "claim", "TASK-1", "--holder", "fresh-agent"]
    )
    assert result.exit_code == 0, result.stdout
    assert "Claimed TASK-1 on the current branch." in result.stdout


def test_handoff_rejects_secret_like_text(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    import pytest
    with pytest.raises(ValueError, match="secret-like"):
        write_handoff("TASK-1", "api_key=forbidden", root=root)


def test_create_task_writes_pair(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = create_task("TASK-2", title="Second", intent="Independent work", root=root)
    assert task["status"] == "ready"
    assert (root / ".agent/handoffs/TASK-2.md").is_file()


def test_complete_requires_clean_workspace_and_registered_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="successful verification at current HEAD"):
        complete_task("TASK-1", root=root)


def test_verify_task_executes_registered_command_and_records_machine_proof(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = verify_task("TASK-1", command_key="test", root=root)
    evidence = task["verification"][-1]
    assert evidence["exit_code"] == 0
    assert evidence["result"] == "passed"
    assert evidence["recorded_by"] == "agent_workflow.verify/v1"
    assert evidence["commit"] == subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()


def test_verify_task_does_not_record_a_failed_command(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    commands = yaml.safe_load((root / ".agent/commands.yaml").read_text(encoding="utf-8"))
    commands["commands"]["fail"] = {"command": "git show definitely-not-a-ref"}
    (root / ".agent/commands.yaml").write_text(yaml.safe_dump(commands), encoding="utf-8")
    subprocess.run(["git", "add", ".agent/commands.yaml"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "register failing command"], cwd=root, check=True, capture_output=True)
    with pytest.raises(ValueError, match="verification command failed"):
        verify_task("TASK-1", command_key="fail", root=root)
    task = yaml.safe_load((root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8"))
    assert task["verification"] == []


def test_complete_requires_structured_handoff_and_sets_safe_next_action(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    verify_task("TASK-1", command_key="test", root=root)
    with pytest.raises(ValueError, match="handoff is missing required headings"):
        complete_task("TASK-1", root=root)
    write_handoff("TASK-1", _handoff(), root=root)
    completed = complete_task("TASK-1", root=root)
    assert completed["status"] == "done"
    assert completed["next_action"]["command_key"] == "workflow_bootstrap"
    assert "complete" in completed["next_action"]["reason"].lower()


def test_complete_rejects_verification_from_an_older_head(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    verify_task("TASK-1", command_key="test", root=root)
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "record evidence"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "change implementation"], cwd=root, check=True, capture_output=True)
    with pytest.raises(ValueError, match="successful verification at current HEAD"):
        complete_task("TASK-1", root=root)


def test_done_task_rejects_implementation_next_action(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    task["verification"] = [{
        "command_key": "test",
        "command": "git status --short",
        "exit_code": 0,
        "commit": task["base_commit"],
        "result": "passed",
        "recorded_by": "agent_workflow.verify/v1",
    }]
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert any("completed task next action" in finding.message for finding in validate(root, strict=True))


def test_bootstrap_labels_historical_evidence_instead_of_presenting_it_as_current(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task.update({
        "status": "done",
        "next_action": {"command_key": "workflow_bootstrap", "reason": "Task complete; claim new work."},
        "verification": [{
            "command_key": "test",
            "command": "git status --short",
            "exit_code": 0,
            "commit": task["base_commit"],
            "result": "passed",
            "recorded_by": "agent_workflow.verify/v1",
        }],
    })
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    (root / "README.md").write_text("newer history\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "newer history"], cwd=root, check=True, capture_output=True)
    payload = bootstrap(root)
    assert payload["task_source"] == "branch_history"
    assert payload["evidence"]["freshness"] == "historical"
    assert payload["next_action"]["command_key"] == "workflow_bootstrap"


def test_update_task_enforces_legal_transition_and_registered_next_command(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="illegal task transition"):
        update_task("TASK-1", status="ready", root=root)
    with pytest.raises(ValueError, match="unknown command key"):
        update_task("TASK-1", next_command_key="missing", next_reason="no", root=root)
    updated = update_task(
        "TASK-1",
        next_command_key="workflow_bootstrap",
        next_reason="Inspect repository state.",
        root=root,
    )
    assert updated["next_action"]["command_key"] == "workflow_bootstrap"


def test_strict_change_check_requires_task_and_handoff_in_governed_diff(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "start task"], cwd=root, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    (root / "README.md").write_text("governed change\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "untracked work"], cwd=root, check=True, capture_output=True)
    messages = {finding.message for finding in validate(root, strict=True, changed_since=base)}
    assert "governed changes require a tracked task packet update" in messages
    assert "governed changes require a tracked handoff update" in messages

    task_path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["updated_at"] = "2026-02-01T00:00:00Z"
    task_path.write_text(yaml.safe_dump(task), encoding="utf-8")
    (root / ".agent/handoffs/TASK-1.md").write_text(_handoff().replace("None known.", "Low."), encoding="utf-8")
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "record handoff"], cwd=root, check=True, capture_output=True)
    messages = {finding.message for finding in validate(root, strict=True, changed_since=base)}
    assert "governed changes require a tracked task packet update" not in messages
    assert "governed changes require a tracked handoff update" not in messages


def test_check_cli_supports_json_and_task_filter(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    write_handoff("TASK-1", _handoff(), root=root)
    result = CliRunner().invoke(workflow_app, ["check", "--task", "TASK-1", "--json"])
    assert result.exit_code == 0
    assert yaml.safe_load(result.stdout) == {"valid": True, "findings": []}


def test_complete_cli_runs_verification_before_marking_done(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    write_handoff("TASK-1", _handoff(), root=root)
    result = CliRunner().invoke(
        workflow_app,
        ["task", "complete", "TASK-1", "--command-key", "test"],
    )
    assert result.exit_code == 0, result.stdout
    task = yaml.safe_load((root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8"))
    assert task["status"] == "done"
    assert task["verification"][-1]["recorded_by"] == "agent_workflow.verify/v1"


def test_versioned_hook_blocks_governed_change_without_task_and_handoff(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    (root / "README.md").write_text("governed\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    hook = Path(__file__).resolve().parents[1] / "scripts/hooks/pre-commit"
    blocked = subprocess.run(["sh", str(hook)], cwd=root, text=True, capture_output=True)
    assert blocked.returncode == 1
    assert "without a tracked task packet or handoff update" in blocked.stdout

    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent/tasks/TASK-1.yaml", ".agent/handoffs/TASK-1.md"], cwd=root, check=True)
    allowed = subprocess.run(["sh", str(hook)], cwd=root, text=True, capture_output=True)
    assert allowed.returncode == 0, allowed.stdout


def test_bootstrap_surfaces_single_completed_task_when_no_active_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    task["verification"] = [{"command_key": "test", "command": "uv run pytest tests/test_agent_workflow.py -q", "exit_code": 0, "commit": task["base_commit"]}]
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert bootstrap(root)["task"]["id"] == "TASK-1"


def test_bootstrap_selects_latest_completed_task_when_branch_has_history(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    first = yaml.safe_load((root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8"))
    first.update({"status": "done", "updated_at": "2026-01-01T00:00:00Z"})
    second = dict(first, id="TASK-2", updated_at="2026-02-01T00:00:00Z", handoff=".agent/handoffs/TASK-2.md")
    (root / ".agent/tasks/TASK-1.yaml").write_text(yaml.safe_dump(first), encoding="utf-8")
    (root / ".agent/tasks/TASK-2.yaml").write_text(yaml.safe_dump(second), encoding="utf-8")
    (root / ".agent/handoffs/TASK-2.md").write_text("# handoff\n", encoding="utf-8")
    assert bootstrap(root)["task"]["id"] == "TASK-2"


def test_bootstrap_falls_back_to_global_history_on_unclaimed_branch(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = yaml.safe_load((root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8"))
    task.update({"status": "done", "ownership": {"branch": "other"}, "updated_at": "2026-02-01T00:00:00Z"})
    (root / ".agent/tasks/TASK-1.yaml").write_text(yaml.safe_dump(task), encoding="utf-8")
    payload = bootstrap(root)
    assert payload["task"]["id"] == "TASK-1"
    assert payload["task_source"] == "global_history"


# ---------------------------------------------------------------------------
# Trust-boundary policy enforcement (TASK-2026-011)
# ---------------------------------------------------------------------------


def _repo_with_policy(tmp_path, monkeypatch):
    """Set up a scratch repo whose commands and policy files declare a fake
    trust boundary. Uses a real spec-style command_key so we can also test
    evidence attachment end-to-end."""
    root = _repo(tmp_path, monkeypatch)
    (root / ".agent/commands.yaml").write_text(
        "commands:\n"
        "  test:\n"
        "    command: git status --short\n"
        "  workflow_bootstrap:\n"
        "    command: uv run aletheia agent workflow bootstrap\n"
        "  spec_risk_engine:\n"
        "    command: git status --short\n"
        "  spec_execution:\n"
        "    command: git status --short\n",
        encoding="utf-8",
    )
    (root / ".agent/policy.yaml").write_text(
        "schema_version: 1\n"
        "trust_boundaries:\n"
        "  - id: risk\n"
        "    paths:\n"
        "      - aletheia/risk/**\n"
        "    required_command_keys:\n"
        "      - spec_risk_engine\n"
        "  - id: bitget_auth\n"
        "    paths:\n"
        "      - aletheia/exchanges/bitget/auth.py\n"
        "    required_command_keys:\n"
        "      - spec_execution\n",
        encoding="utf-8",
    )
    (root / "aletheia/risk").mkdir(parents=True)
    (root / "aletheia/risk/engine.py").write_text("# initial\n", encoding="utf-8")
    (root / "aletheia/exchanges/bitget").mkdir(parents=True)
    (root / "aletheia/exchanges/bitget/auth.py").write_text("# initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "seed policy"], cwd=root, check=True, capture_output=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    return root, base


def test_trust_boundary_change_without_evidence_fails_strict_check(tmp_path, monkeypatch):
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/risk/engine.py").write_text("# tweak\n", encoding="utf-8")
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "touch risk"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    assert any("trust-boundary 'risk' touched" in m and "spec_risk_engine" in m for m in messages), messages


def test_trust_boundary_change_with_matching_evidence_passes(tmp_path, monkeypatch):
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/risk/engine.py").write_text("# tweak\n", encoding="utf-8")
    subprocess.run(["git", "add", "aletheia/risk/engine.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "risk change"], cwd=root, check=True, capture_output=True)
    verify_task("TASK-1", command_key="spec_risk_engine", root=root)
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "attach evidence"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    trust_findings = [m for m in messages if "trust-boundary" in m]
    assert trust_findings == [], trust_findings


def test_trust_boundary_evidence_from_other_branch_still_counts_when_reachable(tmp_path, monkeypatch):
    """Evidence recorded at an ancestor of HEAD covers current-HEAD changes."""
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/risk/engine.py").write_text("# tweak\n", encoding="utf-8")
    subprocess.run(["git", "add", "aletheia/risk/engine.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "risk change"], cwd=root, check=True, capture_output=True)
    verify_task("TASK-1", command_key="spec_risk_engine", root=root)
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "evidence"], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("later\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "later"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    trust_findings = [m for m in messages if "trust-boundary" in m]
    assert trust_findings == [], trust_findings


def test_trust_boundary_evidence_at_orphan_commit_is_rejected(tmp_path, monkeypatch):
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/risk/engine.py").write_text("# tweak\n", encoding="utf-8")
    subprocess.run(["git", "add", "aletheia/risk/engine.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "risk change"], cwd=root, check=True, capture_output=True)
    verify_task("TASK-1", command_key="spec_risk_engine", root=root)
    task_path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["verification"][-1]["commit"] = "0" * 40  # unreachable commit hash
    task_path.write_text(yaml.safe_dump(task), encoding="utf-8")
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "attach evidence"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    assert any("trust-boundary 'risk' touched" in m for m in messages), messages


def test_trust_boundary_file_scoped_glob_matches_exact_file(tmp_path, monkeypatch):
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/exchanges/bitget/auth.py").write_text("# tweak\n", encoding="utf-8")
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "touch auth"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    assert any("trust-boundary 'bitget_auth' touched" in m and "spec_execution" in m for m in messages), messages


def test_trust_boundary_evidence_from_unrelated_command_key_does_not_count(tmp_path, monkeypatch):
    root, base = _repo_with_policy(tmp_path, monkeypatch)
    (root / "aletheia/risk/engine.py").write_text("# tweak\n", encoding="utf-8")
    subprocess.run(["git", "add", "aletheia/risk/engine.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "risk change"], cwd=root, check=True, capture_output=True)
    # Verify with a DIFFERENT command_key (not spec_risk_engine)
    verify_task("TASK-1", command_key="test", root=root)
    write_handoff("TASK-1", _handoff(), root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "wrong evidence"], cwd=root, check=True, capture_output=True)
    messages = [f.message for f in validate(root, strict=True, changed_since=base)]
    assert any("trust-boundary 'risk' touched" in m for m in messages), messages


def test_check_staged_cli_reports_missing_evidence(tmp_path, monkeypatch):
    root, _ = _repo_with_policy(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        workflow_app,
        ["check-staged", "--paths-stdin"],
        input="aletheia/risk/engine.py\n",
    )
    assert result.exit_code == 1, result.stdout
    combined = result.stdout + (getattr(result, "stderr", "") or "")
    assert "trust-boundary 'risk' touched" in combined
    assert "spec_risk_engine" in combined


def test_check_staged_cli_passes_when_evidence_present(tmp_path, monkeypatch):
    root, _ = _repo_with_policy(tmp_path, monkeypatch)
    verify_task("TASK-1", command_key="spec_risk_engine", root=root)
    result = CliRunner().invoke(
        workflow_app,
        ["check-staged", "--paths-stdin"],
        input="aletheia/risk/engine.py\n",
    )
    assert result.exit_code == 0, result.stdout


def test_check_staged_cli_ignores_non_boundary_paths(tmp_path, monkeypatch):
    root, _ = _repo_with_policy(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        workflow_app,
        ["check-staged", "--paths-stdin"],
        input="frontend/src/pages/Home.tsx\n",
    )
    assert result.exit_code == 0, result.stdout


# ---------------------------------------------------------------------------
# base_commit rebase resilience (TASK-2026-012)
# ---------------------------------------------------------------------------


def _make_orphan_base(root: Path) -> str:
    """Return a real-but-orphan commit hash by branching off, then abandoning it."""
    subprocess.run(["git", "checkout", "-b", "orphan-side"], cwd=root, check=True, capture_output=True)
    (root / "sidefile").write_text("side\n", encoding="utf-8")
    subprocess.run(["git", "add", "sidefile"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "orphan side"], cwd=root, check=True, capture_output=True)
    orphan = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    # Return to the primary branch (recorded on TASK-1 ownership); leave the
    # orphan commit reachable via reflog only.
    branch = yaml.safe_load((root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8"))["ownership"]["branch"]
    subprocess.run(["git", "checkout", branch], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-D", "orphan-side"], cwd=root, check=True, capture_output=True)
    return orphan


def test_validate_reports_orphan_base_commit_as_warning_not_error(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    orphan = _make_orphan_base(root)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["base_commit"] = orphan
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    findings = validate(root, strict=True)
    reachability = [f for f in findings if "base_commit is not reachable" in f.message]
    assert reachability, "expected an orphan-base finding"
    assert all(f.severity == "warning" for f in reachability), [(f.severity, f.message) for f in reachability]


def test_validate_still_treats_nonexistent_base_commit_as_error(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["base_commit"] = "0" * 40
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    findings = validate(root)
    missing = [f for f in findings if "base_commit is not a local Git commit" in f.message]
    assert missing and missing[0].severity == "error"


def test_rebase_base_resets_to_head_and_records_rebased_at(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    orphan = _make_orphan_base(root)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["base_commit"] = orphan
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    rebased = rebase_base("TASK-1", root=root)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    assert rebased["base_commit"] == head
    assert "rebased_at" in rebased and rebased["rebased_at"].endswith("Z")
    findings = validate(root)
    assert not any("base_commit is not reachable" in f.message for f in findings)


def test_rebase_base_rejects_done_tasks(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    with pytest.raises(ValueError, match="only in-progress or blocked tasks"):
        rebase_base("TASK-1", root=root)


def test_revalidate_completed_task_runs_registered_command_and_resets_base(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    task["verification"] = [{
        "command_key": "test",
        "command": "git status --short",
        "commit": "0" * 40,
        "exit_code": 0,
        "result": "passed",
        "recorded_by": "agent_workflow.verify/v1",
        "command_sha256": hashlib.sha256(b"git status --short").hexdigest(),
    }]
    path.write_text(yaml.safe_dump(task), encoding="utf-8")

    revalidated = revalidate_completed_task("TASK-1", command_key="test", root=root)

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    assert revalidated["status"] == "done"
    assert revalidated["base_commit"] == head
    assert revalidated["verification"][-1]["commit"] == head
    assert revalidated["verification"][-1]["recorded_by"] == "agent_workflow.verify/v1"
    assert revalidated["revalidated_at"].endswith("Z")


def test_revalidate_completed_task_rejects_active_tasks(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="only completed tasks"):
        revalidate_completed_task("TASK-1", command_key="test", root=root)


def test_revalidate_completed_task_cli(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    result = CliRunner().invoke(
        workflow_app, ["task", "revalidate", "TASK-1", "--command-key", "test"]
    )
    assert result.exit_code == 0, result.stdout
    assert "Revalidated TASK-1" in result.stdout


def test_rebase_base_cli(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    orphan = _make_orphan_base(root)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["base_commit"] = orphan
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    result = CliRunner().invoke(workflow_app, ["task", "rebase-base", "TASK-1"])
    assert result.exit_code == 0, result.stdout
    assert "Rebased base_commit" in result.stdout


def test_check_cli_treats_orphan_base_commit_as_success_with_warnings(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    write_handoff("TASK-1", _handoff(), root=root)
    orphan = _make_orphan_base(root)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["base_commit"] = orphan
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    result = CliRunner().invoke(workflow_app, ["check"])
    assert result.exit_code == 0, result.stdout
    assert "WARNING" in result.stdout
    assert "base_commit is not reachable" in result.stdout


# ---------------------------------------------------------------------------
# Handoff tiering: standard vs tiny (TASK-2026-013)
# ---------------------------------------------------------------------------


def _tiny_handoff() -> str:
    return (
        "# TASK-2 handoff\n\n"
        "## Purpose\n\nQuick fix.\n\n"
        "## Changes made\n\n- Adjusted a threshold.\n\n"
        "## Verification\n\n- Focused workflow test passed.\n"
    )


def test_create_task_tiny_writes_short_template_and_marks_packet_type(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = create_task("TASK-2", title="Small", intent="Tiny fix", packet_type="tiny", root=root)
    assert task["packet_type"] == "tiny"
    handoff = (root / ".agent/handoffs/TASK-2.md").read_text(encoding="utf-8")
    assert "## Purpose" in handoff and "## Changes made" in handoff and "## Verification" in handoff
    assert "Uncommitted work" not in handoff
    assert "Blockers" not in handoff


def test_create_task_defaults_to_standard(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    task = create_task("TASK-2", title="Std", intent="Standard task", root=root)
    assert task["packet_type"] == "standard"
    handoff = (root / ".agent/handoffs/TASK-2.md").read_text(encoding="utf-8")
    assert "## Uncommitted work" in handoff
    assert "## Blockers" in handoff


def test_create_task_rejects_unknown_packet_type(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="packet_type must be one of"):
        create_task("TASK-2", title="X", intent="bad", packet_type="huge", root=root)


def test_tiny_task_completes_with_three_headings(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "checkout", "-b", "tiny-branch"], cwd=root, check=True, capture_output=True)
    create_task("TASK-2", title="Small", intent="Tiny fix", packet_type="tiny", root=root)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "seed tiny"], cwd=root, check=True, capture_output=True)
    task = claim_task("TASK-2", holder="fresh-agent", root=root)
    assert task["packet_type"] == "tiny"
    verify_task("TASK-2", command_key="test", root=root)
    write_handoff("TASK-2", _tiny_handoff(), root=root)
    completed = complete_task("TASK-2", root=root)
    assert completed["status"] == "done"


def test_standard_task_still_requires_all_eight_headings(tmp_path, monkeypatch):
    """The existing 8-heading requirement must not regress for standard packets."""
    root = _repo(tmp_path, monkeypatch)
    # TASK-1 in _repo has no packet_type — treated as standard.
    verify_task("TASK-1", command_key="test", root=root)
    with pytest.raises(ValueError, match="Uncommitted work"):
        write_handoff("TASK-1", _tiny_handoff(), root=root)


def test_write_handoff_rejects_tiny_text_for_standard_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="handoff is missing required headings"):
        write_handoff("TASK-1", _tiny_handoff(), root=root)


def test_validate_rejects_unknown_packet_type(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["packet_type"] = "gigantic"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert any("unknown packet_type" in f.message for f in validate(root))


def test_cli_create_tiny_flag(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        workflow_app,
        ["task", "create", "TASK-2", "--title", "Small", "--intent", "Tiny", "--tiny"],
    )
    assert result.exit_code == 0, result.stdout
    assert "(tiny)" in result.stdout
    task = yaml.safe_load((root / ".agent/tasks/TASK-2.yaml").read_text(encoding="utf-8"))
    assert task["packet_type"] == "tiny"


def test_legacy_task_without_packet_type_still_validates(tmp_path, monkeypatch):
    """Legacy packets that pre-date this feature must continue to pass validate()."""
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    # ensure no packet_type field is present
    task.pop("packet_type", None)
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    findings = validate(root)
    assert not any("packet_type" in f.message for f in findings)


# ---------------------------------------------------------------------------
# _LEGAL_TRANSITIONS completeness (TASK-2026-014)
# ---------------------------------------------------------------------------


def _set_status(root: Path, status: str) -> None:
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = status
    path.write_text(yaml.safe_dump(task), encoding="utf-8")


def test_update_task_allows_blocked_to_in_progress(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    _set_status(root, "blocked")
    updated = update_task("TASK-1", status="in_progress", root=root)
    assert updated["status"] == "in_progress"


def test_update_task_allows_blocked_to_ready(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    _set_status(root, "blocked")
    updated = update_task("TASK-1", status="ready", root=root)
    assert updated["status"] == "ready"


def test_update_task_still_rejects_blocked_to_backlog(tmp_path, monkeypatch):
    """Backlog is not a legal target from blocked; regressions here would let
    a task lose its intake context."""
    root = _repo(tmp_path, monkeypatch)
    _set_status(root, "blocked")
    with pytest.raises(ValueError, match="illegal task transition"):
        update_task("TASK-1", status="backlog", root=root)


def test_update_task_still_rejects_done_via_update(tmp_path, monkeypatch):
    """Only complete_task may set done, regardless of source status."""
    root = _repo(tmp_path, monkeypatch)
    _set_status(root, "blocked")
    with pytest.raises(ValueError, match="use task complete to enter done state"):
        update_task("TASK-1", status="done", root=root)


def test_legal_transitions_map_declares_blocked_complements():
    from aletheia.agent_workflow import _LEGAL_TRANSITIONS
    assert "in_progress" in _LEGAL_TRANSITIONS["blocked"]
    assert "ready" in _LEGAL_TRANSITIONS["blocked"]
    assert "cancelled" in _LEGAL_TRANSITIONS["blocked"]
    # done is intentionally omitted — complete_task is the only path.
    assert "done" not in _LEGAL_TRANSITIONS["blocked"]


# ---------------------------------------------------------------------------
# Squash-merge tolerance for completed-task evidence (TASK-2026-017)
# ---------------------------------------------------------------------------


def _done_task_with_evidence_commit(root: Path, commit: str) -> None:
    """Mark TASK-1 done with a single passing evidence record at `commit`."""
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task.update({
        "status": "done",
        "next_action": {"command_key": "workflow_bootstrap", "reason": "Task complete; claim new work."},
        "verification": [{
            "command_key": "test",
            "command": "git status --short",
            "exit_code": 0,
            "commit": commit,
            "result": "passed",
            "recorded_by": "agent_workflow.verify/v1",
            "command_sha256": hashlib.sha256(b"git status --short").hexdigest(),
        }],
    })
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    (root / ".agent/handoffs/TASK-1.md").write_text(_handoff(), encoding="utf-8")


def test_strict_accepts_reachable_completed_evidence(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    _done_task_with_evidence_commit(root, head)
    findings = validate(root, strict=True)
    assert not any("evidence" in f.message and f.severity == "error" for f in findings)
    assert not any("not reachable" in f.message for f in findings)


def test_strict_warns_on_squash_merged_orphan_evidence(tmp_path, monkeypatch):
    """Evidence commit that still exists but is no longer a HEAD ancestor
    (the squash-merge case) is a warning, not an error."""
    root = _repo(tmp_path, monkeypatch)
    # Create a real commit on a side branch, capture its SHA, then return to the
    # main branch and delete the side branch — the commit still exists (dangling)
    # but is not reachable from HEAD, exactly like a squash-merged feature tip.
    branch = yaml.safe_load(
        (root / ".agent/tasks/TASK-1.yaml").read_text(encoding="utf-8")
    )["ownership"]["branch"]
    subprocess.run(["git", "checkout", "-b", "orphan-side"], cwd=root, check=True, capture_output=True)
    (root / "sidefile").write_text("side\n", encoding="utf-8")
    subprocess.run(["git", "add", "sidefile"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "orphan side"], cwd=root, check=True, capture_output=True)
    orphan = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
    ).stdout.strip()
    subprocess.run(["git", "checkout", branch], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "branch", "-D", "orphan-side"], cwd=root, check=True, capture_output=True)

    _done_task_with_evidence_commit(root, orphan)
    findings = validate(root, strict=True)
    orphan_findings = [f for f in findings if "not reachable" in f.message]
    assert orphan_findings, "expected an orphan-evidence finding"
    assert all(f.severity == "warning" for f in orphan_findings), [
        (f.severity, f.message) for f in orphan_findings
    ]
    # And it must NOT surface as an error anywhere.
    assert not any(
        f.severity == "error" and "evidence" in f.message for f in findings
    )


def test_strict_errors_on_nonexistent_evidence_commit(tmp_path, monkeypatch):
    """A completed task whose evidence commit does not exist at all is a hard
    error — that proof never happened."""
    root = _repo(tmp_path, monkeypatch)
    _done_task_with_evidence_commit(root, "0" * 40)
    findings = validate(root, strict=True)
    assert any(
        f.severity == "error" and "does not exist" in f.message for f in findings
    ), [(f.severity, f.message) for f in findings]


def test_range_strict_warns_for_unchanged_historical_missing_evidence(tmp_path, monkeypatch):
    """Remote CI cannot fetch deleted feature-branch objects after a squash merge.
    An unchanged historical task must not break every later PR for that reason."""
    root = _repo(tmp_path, monkeypatch)
    _done_task_with_evidence_commit(root, "0" * 40)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "record historical task"],
        cwd=root, check=True, capture_output=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True,
    ).stdout.strip()
    (root / "later.txt").write_text("unrelated later work\n", encoding="utf-8")
    subprocess.run(["git", "add", "later.txt"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "later unrelated change"],
        cwd=root, check=True, capture_output=True,
    )

    findings = validate(root, strict=True, changed_since=base)
    historical = [f for f in findings if "historical" in f.message and "evidence" in f.message]
    assert historical, [(f.severity, f.message) for f in findings]
    assert all(f.severity == "warning" for f in historical)
    assert not any(
        f.severity == "error" and "evidence commit does not exist" in f.message
        for f in findings
    )


def test_range_strict_errors_for_changed_task_with_missing_evidence(tmp_path, monkeypatch):
    """Range awareness must not let the task changed by the current PR invent proof."""
    root = _repo(tmp_path, monkeypatch)
    subprocess.run(["git", "add", ".agent"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "record active task"],
        cwd=root, check=True, capture_output=True,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True,
    ).stdout.strip()
    _done_task_with_evidence_commit(root, "0" * 40)
    subprocess.run(["git", "add", ".agent/tasks/TASK-1.yaml"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "complete current task"],
        cwd=root, check=True, capture_output=True,
    )

    findings = validate(root, strict=True, changed_since=base)
    assert any(
        f.severity == "error" and "evidence commit does not exist" in f.message
        for f in findings
    ), [(f.severity, f.message) for f in findings]
