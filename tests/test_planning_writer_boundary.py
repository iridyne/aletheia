"""Declared Generation/EntryPlan writers; transactional behavior has Planner tests."""

from pathlib import Path

import pytest

from tests.authority_writer_checks import assert_writer_boundary


_OWNERS = {
    "expire_candidate_attention": set(),
    "reconcile_candidate_attention": {"universe/service.py"},
    "candidate_attention_is_active": {"agent_shell/entry_planner.py"},
    "count_pending_candidate_planning": {"agent_shell/entry_planner.py", "agent_runtime/pi_daemon.py"},
    "complete_candidate_planning_no_plan": {"agent_shell/entry_planner.py"},
    "block_candidate_planning": {"agent_shell/entry_planner.py"},
    "mark_candidate_planning_stage": {"agent_shell/entry_planner.py"},
    "fail_candidate_planning": {"agent_shell/entry_planner.py"},
    "defer_candidate_planning": {"agent_shell/entry_planner.py"},
    "defer_candidate_planning_with_code": {"agent_shell/entry_planner.py"},
    "release_candidate_planning_claim": {"agent_shell/entry_planner.py"},
    "ensure_candidate_planning_generation": {"universe/service.py", "trading/serenity_publication.py"},
    "claim_next_candidate_planning": {"agent_shell/entry_planner.py"},
    "bind_candidate_planner_input": {"agent_shell/entry_planner.py"},
    "bind_candidate_planning_hypothesis": {"agent_shell/entry_planner.py"},
    "complete_candidate_planning_with_plan": {"agent_shell/entry_planner.py"},
    "bind_entry_trigger_activation": {"agent_runtime/fast_trigger_watch.py"},
    "record_entry_plan_monitoring": {"agent_runtime/fast_trigger_watch.py"},
    "transition_entry_plan": {"agent_runtime/fast_trigger_watch.py", "execution/entry_plan_execution.py", "execution/gateway.py"},
}
_DELEGATIONS = {
    (caller, "expire_candidate_attention") for caller in (
        "candidate_attention_is_active", "reconcile_candidate_attention",
        "claim_next_candidate_planning", "count_pending_candidate_planning",
        "complete_candidate_planning_no_plan", "defer_candidate_planning",
        "complete_candidate_planning_with_plan",
    )
} | {("defer_candidate_planning_with_code", "defer_candidate_planning")}
_RAW_OWNERS = {
    "candidate_planning_generations": {
        "expire_candidate_attention", "reconcile_candidate_attention",
        "ensure_candidate_planning_generation", "claim_next_candidate_planning",
        "complete_candidate_planning_no_plan", "block_candidate_planning",
        "mark_candidate_planning_stage", "fail_candidate_planning",
        "defer_candidate_planning", "release_candidate_planning_claim",
        "bind_candidate_planner_input", "bind_candidate_planning_hypothesis",
        "complete_candidate_planning_with_plan",
    },
    "entry_plans": {
        "ensure_candidate_planning_generation", "complete_candidate_planning_with_plan",
        "bind_entry_trigger_activation", "transition_entry_plan", "record_entry_plan_monitoring",
    },
}


def _assert_planning_writer_boundary(root):
    for table, raw_owners in _RAW_OWNERS.items():
        assert_writer_boundary(root, table=table, owners=_OWNERS, raw_owners=raw_owners, delegations=_DELEGATIONS)


def test_planning_writer_ownership():
    _assert_planning_writer_boundary(Path(__file__).resolve().parents[1])


@pytest.mark.parametrize("writer", [
    "complete_candidate_planning_no_plan", "block_candidate_planning",
    "mark_candidate_planning_stage", "fail_candidate_planning",
    "defer_candidate_planning", "defer_candidate_planning_with_code",
    "release_candidate_planning_claim", "expire_candidate_attention",
    "reconcile_candidate_attention", "candidate_attention_is_active",
    "count_pending_candidate_planning",
])
def test_all_generation_lifecycle_callers_are_guarded(tmp_path, writer):
    target = tmp_path / "aletheia/other.py"
    target.parent.mkdir()
    target.write_text(f"from aletheia.storage.repositories import {writer}\n{writer}(row)")
    with pytest.raises(AssertionError):
        _assert_planning_writer_boundary(tmp_path)


@pytest.mark.parametrize("source", [
    "plan_state_writer = transition_entry_plan",
    "plan_state_writer: Callable = repo.transition_entry_plan",
    "from aletheia.storage.repositories import transition_entry_plan as plan_state_writer",
    "if True:\n    from aletheia.storage.repositories import transition_entry_plan as plan_state_writer",
    "class Export:\n    from aletheia.storage.repositories import transition_entry_plan as plan_state_writer",
])
def test_named_writer_alias_cannot_be_reexported(tmp_path, source):
    owner = tmp_path / "aletheia/execution/gateway.py"
    owner.parent.mkdir(parents=True)
    owner.write_text(source)
    other = tmp_path / "aletheia/other.py"
    other.write_text("from aletheia.execution.gateway import plan_state_writer\nplan_state_writer(row)")
    with pytest.raises(AssertionError):
        _assert_planning_writer_boundary(tmp_path)


@pytest.mark.parametrize("writer", _OWNERS)
@pytest.mark.parametrize("directory", ["aletheia", "extensions", "legacy", "ops", "scripts"])
def test_planning_guard_rejects_new_named_owner(tmp_path, writer, directory):
    target = tmp_path / directory / "other.py"
    target.parent.mkdir()
    target.write_text(f"from aletheia.storage.repositories import {writer} as persist\npersist(row)")
    with pytest.raises(AssertionError):
        _assert_planning_writer_boundary(tmp_path)


@pytest.mark.parametrize("table", _RAW_OWNERS)
@pytest.mark.parametrize("source", [
    "persist: Callable = repo._update\npersist('{table}', rid, row)",
    "db.execute('UPDATE /* comment */ main.[{table}] SET status=?')",
    "def new_writer(row):\n    _insert('{table}', row)",
])
def test_planning_guard_rejects_raw_writer(tmp_path, table, source):
    target = tmp_path / "aletheia/storage/repositories.py"
    target.parent.mkdir(parents=True)
    target.write_text(source.format(table=table))
    with pytest.raises(AssertionError):
        _assert_planning_writer_boundary(tmp_path)


@pytest.mark.parametrize("table,writer", [
    ("entry_plans", "claim_next_candidate_planning"),
    ("candidate_planning_generations", "transition_entry_plan"),
])
def test_planning_raw_ownership_is_table_specific(tmp_path, table, writer):
    target = tmp_path / "aletheia/storage/repositories.py"
    target.parent.mkdir(parents=True)
    target.write_text(f"def {writer}(row):\n    _insert('{table}', row)")
    with pytest.raises(AssertionError):
        _assert_planning_writer_boundary(tmp_path)


def test_planning_guard_accepts_declared_writers(tmp_path):
    repository = tmp_path / "aletheia/storage/repositories.py"
    repository.parent.mkdir(parents=True)
    repository.write_text(
        "def complete_candidate_planning_with_plan(row):\n"
        "    _insert('entry_plans', row)\n"
        "    _update('candidate_planning_generations', rid, row)\n"
    )
    for writer, paths in _OWNERS.items():
        for path in paths:
            target = tmp_path / "aletheia" / path
            target.parent.mkdir(parents=True, exist_ok=True)
            source = target.read_text() if target.exists() else ""
            target.write_text(source + f"repo.{writer}(row)\n")
    _assert_planning_writer_boundary(tmp_path)
