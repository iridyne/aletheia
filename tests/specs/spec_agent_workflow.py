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


@spec_claim(SpecClaim("AW-S02", _CONTRACT, "§invariants", "L2b", "Done tasks require registered successful evidence at current HEAD in strict mode."))
def test_validator_locks_honest_completion():
    from aletheia.agent_workflow import validate
    source = Path(validate.__code__.co_filename).read_text(encoding="utf-8")
    assert "completed task has no successful verification evidence" in source
    assert "completed task evidence is stale for current HEAD" in source


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
