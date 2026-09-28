"""One-time cleanup: retire operator tasks whose failure was a fixed defect.

A failed `agent_shell_tasks` row is history, not state, but two surfaces read it:
the failed-task recommendation (now age-bounded, see
`operator_recommendations.FAILED_TASK_VISIBILITY_MINUTES`) and any human scanning
the backlog. Rows produced by a defect that is already fixed and deployed are
therefore noise, and they cannot be resolved by acting on them.

This retires exactly those rows: `failed` tasks of the named task types created
before a cutoff, moved to `cancelled` with the reason recorded in `output_data`.
Nothing is deleted and the prior error string is preserved.

Usage:
    uv run python scripts/retire_fixed_operator_failures.py --before 2026-09-28T06:00:00Z
    uv run python scripts/retire_fixed_operator_failures.py --before ... --apply

Dry-run is the default; `--apply` performs the update. Idempotent: once a row is
cancelled it no longer matches.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

DEFAULT_TASK_TYPES = (
    # The retired-command loop: the shell drove `run_market_scan` every cycle (#3641).
    "run_market_scan",
    # Delegation crash: the shell read a synchronous result from a 202+job_id contract (#3647).
    "run_portfolio_snapshot",
    "run_risk_critique",
    "run_dreaming",
)

RETIRED_REASON = (
    "retired by scripts/retire_fixed_operator_failures.py: the failure cause is fixed and "
    "deployed (#3641 retired-command loop, #3647 delegation contract, #3648 review budget)"
)


def _parse_before(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def select_tasks(*, before: datetime, task_types: tuple[str, ...]) -> list[dict]:
    """Failed tasks of `task_types` created before `before`, newest first."""
    from aletheia.agent_shell.operator_store import list_tasks
    from aletheia.storage.migrations import run_migrations

    # Same schema guarantee the runtime boots with, so the script is usable on a
    # fresh data root instead of failing on a missing table.
    run_migrations()

    matched: list[dict] = []
    for task in list_tasks(limit=1000, status="failed"):
        if task.get("task_type") not in task_types:
            continue
        created_at = str(task.get("created_at") or "")
        try:
            created = datetime.fromisoformat(created_at.replace(" ", "T")).replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created < before:
            matched.append(task)
    return matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        required=True,
        help="ISO timestamp; failures created before it are retired (e.g. 2026-09-28T06:00:00Z)",
    )
    parser.add_argument(
        "--task-type",
        action="append",
        dest="task_types",
        help=f"limit to this task type (repeatable); default: {', '.join(DEFAULT_TASK_TYPES)}",
    )
    parser.add_argument("--apply", action="store_true", help="perform the update")
    args = parser.parse_args(argv)

    before = _parse_before(args.before)
    task_types = tuple(args.task_types or DEFAULT_TASK_TYPES)
    tasks = select_tasks(before=before, task_types=task_types)

    by_type: dict[str, int] = {}
    for task in tasks:
        by_type[str(task.get("task_type"))] = by_type.get(str(task.get("task_type")), 0) + 1
    print(f"retire candidates before {before.isoformat()}: {len(tasks)}")
    for task_type, count in sorted(by_type.items(), key=lambda item: -item[1]):
        print(f"  {task_type}: {count}")
    if not args.apply:
        print("dry run — pass --apply to cancel these rows")
        return 0

    from aletheia.agent_shell.operator_store import update_task

    retired = 0
    for task in tasks:
        output_data = task.get("output_data")
        if isinstance(output_data, str):
            import json

            try:
                output_data = json.loads(output_data)
            except ValueError:
                output_data = {}
        note = dict(output_data) if isinstance(output_data, dict) else {}
        note["retired_reason"] = RETIRED_REASON
        note["prior_error"] = task.get("error")
        update_task(str(task["id"]), status="cancelled", output_data=note)
        retired += 1
    print(f"retired {retired} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
