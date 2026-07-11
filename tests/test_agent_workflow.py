from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from aletheia.agent_workflow import bootstrap, claim_task, complete_task, create_task, recovery_test, validate, write_handoff


def _repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    (root / ".agent/tasks").mkdir(parents=True)
    (root / ".agent/handoffs").mkdir()
    (root / ".agent/project.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    (root / ".agent/commands.yaml").write_text("commands:\n  test:\n    command: uv run pytest tests/test_agent_workflow.py -q\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True).stdout.strip()
    task = {"schema_version": 1, "id": "TASK-1", "title": "Task", "status": "in_progress", "scope": {"boundary": "docs"}, "intent": "test", "acceptance": ["works"], "ownership": {"branch": "master"}, "base_commit": head, "next_action": {"command_key": "test", "reason": "run test"}, "blockers": [], "verification": [], "handoff": ".agent/handoffs/TASK-1.md"}
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


def test_claim_and_handoff_only_change_selected_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "ready"
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    claimed = claim_task("TASK-1", holder="fresh-agent", root=root)
    assert claimed["status"] == "in_progress"
    handoff = write_handoff("TASK-1", "# Purpose\nContinue safely.", root=root)
    assert handoff.read_text(encoding="utf-8") == "# Purpose\nContinue safely.\n"
    assert recovery_test(root)["recoverable"] is True


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
    import pytest
    with pytest.raises(ValueError, match="workspace is dirty"):
        complete_task("TASK-1", command_key="test", root=root)


def test_bootstrap_surfaces_single_completed_task_when_no_active_task(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch)
    path = root / ".agent/tasks/TASK-1.yaml"
    task = yaml.safe_load(path.read_text(encoding="utf-8"))
    task["status"] = "done"
    task["verification"] = [{"command_key": "test", "command": "uv run pytest tests/test_agent_workflow.py -q", "exit_code": 0, "commit": task["base_commit"]}]
    path.write_text(yaml.safe_dump(task), encoding="utf-8")
    assert bootstrap(root)["task"]["id"] == "TASK-1"
