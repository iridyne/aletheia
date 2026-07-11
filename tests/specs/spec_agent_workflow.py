"""Spec: session-independent repository-agent workflow.

Source: docs/design/agent_workflow_contract.md
"""
from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_ROOT = Path(__file__).resolve().parents[2]
_CONTRACT = "agent_workflow_contract"


@spec_claim(SpecClaim("AW-S01", _CONTRACT, "§invariants", "L2a", "Tracked workflow metadata and validator exist."))
def test_workflow_metadata_and_validator_exist():
    assert (_ROOT / ".agent/project.yaml").is_file()
    assert (_ROOT / ".agent/commands.yaml").is_file()
    assert (_ROOT / "aletheia/agent_workflow.py").is_file()


@spec_claim(SpecClaim("AW-S02", _CONTRACT, "§invariants", "L2b", "Completion requires a registered command actually executed successfully at current HEAD."))
def test_validator_locks_honest_completion():
    from aletheia.agent_workflow import complete_task, validate, verify_task
    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    verify_source = Path(verify_task.__code__.co_filename).read_text(encoding="utf-8")
    complete_source = Path(complete_task.__code__.co_filename).read_text(encoding="utf-8")
    assert "completed task has no successful verification evidence" in source
    assert "completed task evidence is not reachable from current HEAD" in source
    assert "subprocess.run(shlex.split(command)" in verify_source
    assert '"recorded_by": "agent_workflow.verify/v1"' in verify_source
    assert 'item.get("commit") == head' in complete_source


@spec_claim(SpecClaim("AW-S03", _CONTRACT, "§invariants", "L2a", "Concurrent work cannot silently claim one branch twice."))
def test_validator_locks_duplicate_branch_detection():
    from aletheia.agent_workflow import validate
    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    assert "duplicate in-progress branch ownership" in source


@spec_claim(SpecClaim("AW-S04", _CONTRACT, "§invariants", "L2a", "Tracked state rejects secret-like and runtime metadata."))
def test_validator_locks_metadata_safety():
    from aletheia.agent_workflow import _contains_forbidden
    assert _contains_forbidden({"api_key": "x"})
    assert _contains_forbidden({"gateway": "running"})


@spec_claim(SpecClaim("AW-S05", _CONTRACT, "§invariants", "L2b", "Bootstrap has a visible task-selection and runtime-probe boundary."))
def test_bootstrap_does_not_execute_recorded_task_commands():
    from aletheia.agent_workflow import bootstrap
    source = Path(bootstrap.__code__.co_filename).read_text(encoding="utf-8")
    assert '"status", "--porcelain"' in source
    assert "runtime_next_command" in source
    assert "subprocess.run" not in source[source.index("def bootstrap"):]


@spec_claim(SpecClaim("AW-S06", _CONTRACT, "§invariants", "L2b", "New handoffs are structured and done tasks return to bootstrap."))
def test_handoff_and_done_state_are_deterministic():
    from aletheia.agent_workflow import _HANDOFF_HEADINGS, complete_task
    assert "Exact next action" in _HANDOFF_HEADINGS
    source = Path(complete_task.__code__.co_filename).read_text(encoding="utf-8")
    assert '"command_key": "workflow_bootstrap"' in source


@spec_claim(SpecClaim("AW-S07", _CONTRACT, "§invariants", "L2b", "Governed changes require task and handoff updates in strict range checks."))
def test_change_range_enforcement_is_wired_to_ci():
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    hook = (_ROOT / "scripts/hooks/pre-commit").read_text(encoding="utf-8")
    assert "--changed-since" in workflow
    assert "governed changes require a tracked task packet update" in Path(
        __import__("aletheia.agent_workflow", fromlist=["x"]).__file__
    ).read_text(encoding="utf-8")
    assert "exit 1" in hook


@spec_claim(SpecClaim("AW-S08", _CONTRACT, "§invariants", "L2b", "base_commit resilience: orphaned base is a warning, and rebase_base can reset it."))
def test_base_commit_resilience():
    from aletheia.agent_workflow import rebase_base, validate
    validate_src = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    rebase_src = Path(rebase_base.__code__.co_filename).read_text(encoding="utf-8")
    assert 'Finding("warning", "base_commit is not reachable' in validate_src
    assert "def rebase_base" in rebase_src
    assert '"rebased_at"' in rebase_src
    assert 'in {"in_progress", "blocked"}' in rebase_src
