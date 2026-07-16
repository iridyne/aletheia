"""Machine-local Task Lite commands; separate from trading runtime and Git proof."""

from __future__ import annotations

import json
import sys

import typer

from aletheia.agent_workflow import (
    bootstrap,
    claim_task,
    complete_task,
    create_task,
    delete_task,
    recovery_test,
    render_bootstrap,
    repo_root,
    trust_boundary_hits_for_paths,
    update_task,
    validate,
)

workflow_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)


@workflow_app.command("bootstrap")
def bootstrap_command(json_out: bool = typer.Option(False, "--json")):
    """Recover local branch/task context without reading tracked task packets."""
    payload = bootstrap()
    typer.echo(render_bootstrap(payload, json_out=json_out))
    if payload["task_match_count"] > 1 or any(
        item["severity"] == "error" for item in payload["findings"]
    ):
        raise typer.Exit(1)


@workflow_app.command("check")
def check(
    strict: bool = typer.Option(False, "--strict"),
    task_id: str | None = typer.Option(None, "--task"),
    changed_since: str | None = typer.Option(None, "--changed-since"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Validate local Task Lite state; trust-boundary findings are advisory."""
    findings = validate(
        strict=strict,
        task_id=task_id,
        changed_since=changed_since,
    )
    if json_out:
        typer.echo(
            json.dumps(
                {
                    "valid": not any(item.severity == "error" for item in findings),
                    "findings": [item.__dict__ for item in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for finding in findings:
            suffix = f" [{finding.task_id}]" if finding.task_id else ""
            typer.echo(f"{finding.severity.upper()}: {finding.message}{suffix}")
        if not findings:
            typer.echo("Task Lite state is valid.")
    if any(item.severity == "error" for item in findings):
        raise typer.Exit(1)


@task_app.command("create")
def create(
    task_id: str,
    title: str = typer.Option(..., "--title"),
    intent: str = typer.Option(..., "--intent"),
    boundary: str = typer.Option("", "--boundary"),
    excluded: str = typer.Option("", "--excluded"),
    acceptance: list[str] | None = typer.Option(None, "--acceptance"),
    next_action: str = typer.Option("", "--next-action"),
):
    """Create a machine-local task; no repository file is changed."""
    try:
        task = create_task(
            task_id,
            title=title,
            intent=intent,
            scope_boundary=boundary,
            scope_excluded=excluded,
            acceptance=acceptance,
            next_action=next_action,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Created local task {task['id']}; claim it from its worktree branch.")


@task_app.command("claim")
def claim(
    task_id: str,
    holder: str = typer.Option(..., "--holder"),
    hours: int = typer.Option(8, "--hours", min=1, max=72),
):
    """Atomically claim a local task for the current branch."""
    try:
        task = claim_task(task_id, holder=holder, hours=hours)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Claimed {task['id']} on {task['branch']} until {task['expires_at']}.")


@task_app.command("update")
def update(
    task_id: str,
    status: str | None = typer.Option(None, "--status"),
    intent: str | None = typer.Option(None, "--intent"),
    boundary: str | None = typer.Option(None, "--boundary"),
    excluded: str | None = typer.Option(None, "--excluded"),
    acceptance: list[str] | None = typer.Option(None, "--acceptance"),
    next_action: str | None = typer.Option(None, "--next-action"),
    blocker: list[str] | None = typer.Option(None, "--blocker"),
):
    """Update local task context without touching Git."""
    try:
        task = update_task(
            task_id,
            status=status,
            intent=intent,
            scope_boundary=boundary,
            scope_excluded=excluded,
            acceptance=acceptance,
            next_action=next_action,
            blockers=blocker,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Updated {task['id']} ({task['status']}).")


@task_app.command("complete")
def complete(task_id: str):
    """Release local ownership after code and CI evidence are handled in Git."""
    try:
        task = complete_task(task_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Completed local task {task['id']}; no Git metadata was written.")


@task_app.command("delete")
def delete(task_id: str):
    """Delete one obsolete local task record."""
    try:
        delete_task(task_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Deleted local task {task_id}.")


@workflow_app.command("recovery-test")
def recovery(json_out: bool = typer.Option(False, "--json")):
    """Assess whether a blank session can recover local worktree context."""
    payload = recovery_test()
    typer.echo(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if json_out
        else ("RECOVERABLE" if payload["recoverable"] else "NOT RECOVERABLE")
    )
    if not payload["recoverable"]:
        raise typer.Exit(1)


@workflow_app.command("check-staged")
def check_staged(paths_stdin: bool = typer.Option(False, "--paths-stdin")):
    """Print focused test hints for staged trust-boundary paths.

    This command is advisory and always leaves Git ownership and proof to the
    normal commit/PR/CI workflow.
    """
    if not paths_stdin:
        typer.echo("--paths-stdin is required; pipe staged paths on stdin", err=True)
        raise typer.Exit(2)
    changed = {line.strip() for line in sys.stdin.read().splitlines() if line.strip()}
    hits = trust_boundary_hits_for_paths(changed, root=repo_root())
    for boundary_id, meta in sorted(hits.items()):
        paths = ", ".join(meta["paths"][:3])
        commands = ", ".join(meta["required_command_keys"]) or "focused tests"
        typer.echo(f"Trust boundary {boundary_id}: {paths}; run {commands}.")


workflow_app.add_typer(
    task_app,
    name="task",
    help="Coordinate machine-local AI tasks without changing Git-tracked state.",
)
