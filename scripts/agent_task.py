"""Standalone task management script. Run with: uv run python scripts/agent_task.py <sub> ...

Sub-commands:
  claim    TASK-ID --holder NAME [--hours N]
  create   TASK-ID --title T --intent I [--tiny]
  update   TASK-ID [--status S] [--next-command-key K --next-reason R] [--blocker B ...]
  verify   TASK-ID --command-key KEY
  complete TASK-ID [--command-key KEY]
  rebase-base TASK-ID
"""
import argparse
import json
import sys

from aletheia.agent_workflow import (
    claim_task,
    complete_task,
    create_task,
    rebase_base,
    repo_root,
    update_task,
    verify_task,
)
from aletheia import agent_tasks_db as _db


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)
    sys.exit(1)


def cmd_claim(args: argparse.Namespace) -> None:
    try:
        task = claim_task(args.task_id, holder=args.holder, hours=args.hours)
    except ValueError as exc:
        _err(str(exc))
    root = repo_root()
    claim = _db.get_claim(args.task_id, root)
    branch = claim["branch"] if claim else "(unknown)"
    print(f"Claimed {task['id']} on {branch}.")


def cmd_create(args: argparse.Namespace) -> None:
    try:
        task = create_task(
            args.task_id,
            title=args.title,
            intent=args.intent,
            packet_type="tiny" if args.tiny else "standard",
        )
    except ValueError as exc:
        _err(str(exc))
    print(
        f"Created {task['id']} ({task.get('packet_type', 'standard')}); "
        "claim it from its dedicated branch before editing code."
    )


def cmd_update(args: argparse.Namespace) -> None:
    try:
        task = update_task(
            args.task_id,
            status=args.status,
            next_command_key=args.next_command_key,
            next_reason=args.next_reason,
            blockers=args.blocker or None,
        )
    except ValueError as exc:
        _err(str(exc))
    print(f"Updated {task['id']} ({task['status']}).")


def cmd_verify(args: argparse.Namespace) -> None:
    try:
        task = verify_task(args.task_id, command_key=args.command_key)
    except ValueError as exc:
        _err(str(exc))
    evidence = task["verification"][-1]
    print(f"Verified {task['id']} at {evidence['commit'][:7]} with {args.command_key}.")


def cmd_complete(args: argparse.Namespace) -> None:
    try:
        if args.command_key:
            verify_task(args.task_id, command_key=args.command_key)
        task = complete_task(args.task_id)
    except ValueError as exc:
        _err(str(exc))
    print(f"Recorded completion evidence for {task['id']}; commit its task packet and handoff.")


def cmd_rebase_base(args: argparse.Namespace) -> None:
    try:
        task = rebase_base(args.task_id)
    except ValueError as exc:
        _err(str(exc))
    print(f"Rebased base_commit of {task['id']} to {task['base_commit'][:7]}.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent_task.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("claim", help="Claim a ready task for this branch.")
    p.add_argument("task_id")
    p.add_argument("--holder", required=True)
    p.add_argument("--hours", type=int, default=8)

    p = sub.add_parser("create", help="Create a new ready task.")
    p.add_argument("task_id")
    p.add_argument("--title", required=True)
    p.add_argument("--intent", required=True)
    p.add_argument("--tiny", action="store_true")

    p = sub.add_parser("update", help="Update task status or next action.")
    p.add_argument("task_id")
    p.add_argument("--status")
    p.add_argument("--next-command-key")
    p.add_argument("--next-reason")
    p.add_argument("--blocker", action="append", default=[])

    p = sub.add_parser("verify", help="Execute a registered command and record evidence.")
    p.add_argument("task_id")
    p.add_argument("--command-key", required=True)

    p = sub.add_parser("complete", help="Complete a task (optionally run verify first).")
    p.add_argument("task_id")
    p.add_argument("--command-key")

    p = sub.add_parser("rebase-base", help="Reset base_commit to HEAD after rebase/squash-merge.")
    p.add_argument("task_id")

    args = parser.parse_args()
    dispatch = {
        "claim": cmd_claim,
        "create": cmd_create,
        "update": cmd_update,
        "verify": cmd_verify,
        "complete": cmd_complete,
        "rebase-base": cmd_rebase_base,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
