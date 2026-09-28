"""Contract for scripts/retire_fixed_operator_failures.py.

The script exists because failed operator-task rows outlive the defect that
produced them: they keep the failed-task surface noisy and cannot be resolved by
acting on them. It must retire exactly the named task types older than the
cutoff, leave every other row alone, and stay idempotent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.agent_shell.operator_store import create_task, get_task, update_task
from scripts import retire_fixed_operator_failures as retire


def _failed_task(task_type: str, *, created_at: str | None = None, error: str = "boom") -> str:
    task_id = create_task(
        task_type=task_type,
        status="failed",
        priority="normal",
        created_by="operator_loop",
        assigned_to="operator",
        input_data={},
        error=error,
    )
    if created_at is not None:
        update_task(task_id, created_at=created_at)
    return task_id


def test_selects_only_named_types_older_than_the_cutoff():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    stale_fixed = _failed_task("run_market_scan", created_at=old)
    # Same defect class but outside the named set: untouched.
    _failed_task("diagnose_system_health", created_at=old)
    # Fresh failure of a named type: a current signal, not history. Its timestamp
    # is explicit because stored `created_at` has second granularity, so "now"
    # would compare as before the cutoff for a row written in the same second.
    fresh = _failed_task("run_market_scan", created_at=(now + timedelta(minutes=1)).isoformat())

    selected = retire.select_tasks(before=now, task_types=retire.DEFAULT_TASK_TYPES)

    assert [task["id"] for task in selected] == [stale_fixed]
    assert fresh not in {task["id"] for task in selected}


def test_apply_cancels_with_a_recorded_reason_and_is_idempotent(capsys):
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    task_id = _failed_task(
        "run_portfolio_snapshot",
        created_at=old,
        error="Cannot read properties of undefined (reading '0')",
    )
    before = now.isoformat()

    assert retire.main(["--before", before, "--apply"]) == 0
    task = get_task(task_id)
    assert task is not None
    assert task["status"] == "cancelled"
    payload = task["output_data"]
    assert payload["retired_reason"].startswith("retired by scripts/retire_fixed_operator_failures.py")
    assert payload["prior_error"] == "Cannot read properties of undefined (reading '0')"
    capsys.readouterr()

    # Second run finds nothing: cancelled rows no longer match `status='failed'`.
    assert retire.main(["--before", before, "--apply"]) == 0
    assert "retire candidates before" in capsys.readouterr().out


def test_dry_run_changes_nothing():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=2)).isoformat()
    task_id = _failed_task("run_risk_critique", created_at=old)

    assert retire.main(["--before", now.isoformat()]) == 0
    task = get_task(task_id)
    assert task is not None and task["status"] == "failed"


def test_rejects_a_non_iso_cutoff():
    with pytest.raises(ValueError):
        retire._parse_before("yesterday")
