"""Spec: Task Lite machine-local agent coordination.

Source: docs/design/agent_workflow_contract.md
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = "agent_workflow_contract"


@spec_claim(
    SpecClaim(
        "AW-S01",
        _CONTRACT,
        "§invariants",
        "L2a",
        "Task state is machine-local and excluded from Git-tracked workflow truth.",
    )
)
def test_task_state_is_machine_local():
    from aletheia.runtime_paths import workflow_tasks_db

    source = Path(workflow_tasks_db.__code__.co_filename).read_text(encoding="utf-8")
    assert 'runtime_root\n        / "development"\n        / "agent-tasks"' in source
    assert ".agent/tasks" not in source


@spec_claim(
    SpecClaim(
        "AW-S02",
        _CONTRACT,
        "§invariants",
        "L2b",
        "Git, PR, and CI own verification; Task Lite has no evidence ledger.",
    )
)
def test_verification_is_not_stored_in_task_state():
    from aletheia.agent_workflow import complete_task, verify_task

    verify_source = Path(verify_task.__code__.co_filename).read_text(encoding="utf-8")
    complete_source = Path(complete_task.__code__.co_filename).read_text(encoding="utf-8")
    assert "does not record verification evidence" in verify_source
    assert "CI remains the verification authority" in complete_source
    assert "command_sha256" not in complete_source


@spec_claim(
    SpecClaim(
        "AW-S03",
        _CONTRACT,
        "§invariants",
        "L2a",
        "One local branch/worktree can hold at most one active task.",
    )
)
def test_active_branch_claim_is_unique():
    from aletheia import agent_tasks_db

    source = Path(agent_tasks_db.__file__).read_text(encoding="utf-8")
    assert "uq_agent_tasks_active_branch" in source
    assert "WHERE status = 'in_progress' AND branch IS NOT NULL" in source


@spec_claim(
    SpecClaim(
        "AW-S04",
        _CONTRACT,
        "§invariants",
        "L2a",
        "Local task context rejects secret-like and runtime-state metadata.",
    )
)
def test_task_metadata_safety_is_preserved():
    from aletheia.agent_workflow import _contains_forbidden

    assert _contains_forbidden({"api_key": "x"})
    assert _contains_forbidden({"gateway": "running"})


@spec_claim(
    SpecClaim(
        "AW-S05",
        _CONTRACT,
        "§invariants",
        "L2b",
        "Bootstrap reports local task, worktree, Git head, and runtime probe boundary.",
    )
)
def test_bootstrap_keeps_context_and_runtime_boundaries_visible():
    from aletheia.agent_workflow import bootstrap

    source = Path(bootstrap.__code__.co_filename).read_text(encoding="utf-8")
    assert '"status", "--porcelain"' in source
    assert "task_store" in source
    assert "runtime_next_command" in source
    assert "agent_tasks_db.get_tasks_for_branch" in source


@spec_claim(
    SpecClaim(
        "AW-S06",
        _CONTRACT,
        "§invariants",
        "L2b",
        "Sibling worktrees of one clone share the same local coordination store.",
    )
)
def test_worktree_identity_uses_git_common_directory():
    from aletheia.runtime_paths import _git_common_dir, workflow_repository_id

    source = Path(_git_common_dir.__code__.co_filename).read_text(encoding="utf-8")
    assert 'git_dir.parent.name == "worktrees"' in source
    assert "git_dir.parent.parent" in source
    assert callable(workflow_repository_id)


@spec_claim(
    SpecClaim(
        "AW-S07",
        _CONTRACT,
        "§invariants",
        "L2b",
        "Code and documentation changes do not require task packet or handoff commits.",
    )
)
def test_git_is_decoupled_from_task_metadata():
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    hook = (_ROOT / "scripts/hooks/pre-commit").read_text(encoding="utf-8")
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Verify agent workflow integrity" not in workflow
    assert "code/docs changed without a task packet" not in hook
    assert "task packet and handoff" not in agents.lower()


@spec_claim(
    SpecClaim(
        "AW-S08",
        _CONTRACT,
        "§invariants",
        "L2b",
        "Trust-boundary matching remains available as a focused-test hint, not a task gate.",
    )
)
def test_trust_boundary_check_is_advisory():
    from aletheia.agent_workflow import validate

    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    assert "Task Lite does not store or gate that proof" in source
    assert "governed changes require a tracked task packet" not in source


@spec_claim(
    SpecClaim(
        "AW-S09",
        _CONTRACT,
        "§invariants",
        "L2a",
        "Historical tracked task packets remain frozen archives, not active state.",
    )
)
def test_historical_task_tree_is_not_loaded():
    from aletheia.agent_workflow import load_tasks

    source = Path(load_tasks.__code__.co_filename).read_text(encoding="utf-8")
    assert "historical `.agent/tasks` tree is intentionally not consulted" in source
    assert "agent_tasks_db.list_tasks" in source
