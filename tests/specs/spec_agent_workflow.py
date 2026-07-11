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
    # Nonexistent evidence commits remain a hard error (fabricated proof).
    assert "completed task evidence commit does not exist in this repository" in source
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


@spec_claim(SpecClaim("AW-S06", _CONTRACT, "§invariants", "L2b", "New handoffs are structured (standard 8-heading or tiny 3-heading) and done tasks return to bootstrap."))
def test_handoff_and_done_state_are_deterministic():
    from aletheia.agent_workflow import (
        _HANDOFF_HEADINGS,
        _TINY_HANDOFF_HEADINGS,
        complete_task,
        _handoff_headings_for,
    )
    assert "Exact next action" in _HANDOFF_HEADINGS
    assert set(_TINY_HANDOFF_HEADINGS) == {"Purpose", "Changes made", "Verification"}
    # Tiny tasks require the 3-heading subset; unmarked/standard tasks require all 8.
    assert _handoff_headings_for({"packet_type": "tiny"}) == _TINY_HANDOFF_HEADINGS
    assert _handoff_headings_for({}) == _HANDOFF_HEADINGS
    assert _handoff_headings_for(None) == _HANDOFF_HEADINGS
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


@spec_claim(SpecClaim("AW-S09", _CONTRACT, "§invariants", "L2a", "Handoff channel convergence: AGENTS.md points at .agent/handoffs and AI_HANDOFF.md is frozen archival."))
def test_handoff_channel_is_single_source():
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    ai_handoff = (_ROOT / "docs/_ai/AI_HANDOFF.md").read_text(encoding="utf-8")
    assert ".agent/handoffs/TASK-*.md" in agents, "AGENTS.md must name the per-task handoff path"
    assert "FROZEN AS OF" in ai_handoff, "AI_HANDOFF.md must carry a frozen banner"
    assert ".agent/handoffs/" in ai_handoff, "AI_HANDOFF.md banner must point to the live channel"


@spec_claim(SpecClaim("AW-S10", _CONTRACT, "§invariants", "L2b", "Squash-merge tolerance: completed-task evidence that still exists but is unreachable from HEAD is a warning; a nonexistent evidence commit stays an error."))
def test_squash_merged_evidence_is_tolerated():
    from aletheia.agent_workflow import validate
    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    # The still-exists-but-unreachable branch downgrades to a warning...
    assert 'Finding(\n                            "warning",' in source or (
        '"warning"' in source and "likely squash-merged" in source
    )
    assert "likely squash-merged" in source
    # ...while a truly-missing commit remains a hard error.
    assert "completed task evidence commit does not exist in this repository" in source
    # It proves existence with cat-file before deciding.
    assert 'cat-file", "-e"' in source
