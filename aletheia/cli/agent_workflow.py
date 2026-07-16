"""Repository-agent recovery commands; separate from the operator runtime."""
from pathlib import Path
import json
import sys
import typer
from aletheia.agent_workflow import (
    bootstrap,
    claim_task,
    complete_task,
    create_task,
    load_tasks,
    rebase_base,
    revalidate_completed_task,
    recovery_test,
    render_bootstrap,
    repo_root,
    trust_boundary_hits_for_paths,
    update_task,
    validate,
    verify_task,
    write_handoff,
)

workflow_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)

@workflow_app.command("bootstrap")
def bootstrap_command(json_out: bool = typer.Option(False, "--json")):
    """Recover the current development task from tracked state and Git."""
    payload = bootstrap()
    typer.echo(render_bootstrap(payload, json_out=json_out))
    if payload["task"] is None or payload["task_match_count"] > 1 or any(x["severity"] == "error" for x in payload["findings"]):
        raise typer.Exit(1)

@workflow_app.command("check")
def check(
    strict: bool = typer.Option(False, "--strict"),
    task_id: str | None = typer.Option(None, "--task"),
    changed_since: str | None = typer.Option(None, "--changed-since"),
    json_out: bool = typer.Option(False, "--json"),
):
    """Validate tracked task state without touching runtime or trading state."""
    findings = validate(strict=strict, task_id=task_id, changed_since=changed_since)
    if json_out:
        typer.echo(json.dumps({"valid": not any(x.severity == "error" for x in findings), "findings": [x.__dict__ for x in findings]}, ensure_ascii=False, indent=2))
    else:
        for finding in findings:
            typer.echo(f"{finding.severity.upper()}: {finding.message}" + (f" [{finding.task_id}]" if finding.task_id else ""))
        if not findings:
            typer.echo("Agent workflow state is valid.")
    if any(x.severity == "error" for x in findings):
        raise typer.Exit(1)

@task_app.command("claim")
def claim(task_id: str, holder: str = typer.Option(..., "--holder"), hours: int = typer.Option(8, "--hours", min=1, max=72)):
    """Claim a ready task for this branch; never changes runtime state."""
    try:
        task = claim_task(task_id, holder=holder, hours=hours)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Claimed {task['id']} on the current branch.")

@task_app.command("create")
def create(
    task_id: str,
    title: str = typer.Option(..., "--title"),
    intent: str = typer.Option(..., "--intent"),
    tiny: bool = typer.Option(False, "--tiny", help="Use the 3-heading tiny handoff instead of the 8-heading standard one."),
):
    """Create a ready task packet and matching handoff template."""
    try:
        task = create_task(
            task_id,
            title=title,
            intent=intent,
            packet_type="tiny" if tiny else "standard",
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(
        f"Created {task['id']} ({task.get('packet_type', 'standard')}); "
        "claim it from its dedicated branch before editing code."
    )

@task_app.command("complete")
def complete(task_id: str, command_key: str | None = typer.Option(None, "--command-key")):
    """Run optional registered verification, then complete with current proof."""
    try:
        if command_key is not None:
            verify_task(task_id, command_key=command_key)
        task = complete_task(task_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Recorded completion evidence for {task['id']}; commit its task packet and handoff.")


@task_app.command("verify")
def verify(task_id: str, command_key: str = typer.Option(..., "--command-key")):
    """Execute an approved command and record proof only if it really passes."""
    try:
        task = verify_task(task_id, command_key=command_key)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    evidence = task["verification"][-1]
    typer.echo(f"Verified {task['id']} at {evidence['commit'][:7]} with {command_key}.")


@task_app.command("rebase-base")
def rebase_base_command(task_id: str):
    """Reset the task's base_commit to current HEAD after a rebase/squash-merge."""
    try:
        task = rebase_base(task_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Rebased base_commit of {task['id']} to {task['base_commit'][:7]}.")


@task_app.command("revalidate")
def revalidate_command(task_id: str, command_key: str = typer.Option(..., "--command-key")):
    """Refresh a completed task's evidence by executing a registered command."""
    try:
        task = revalidate_completed_task(task_id, command_key=command_key)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    evidence = task["verification"][-1]
    typer.echo(f"Revalidated {task['id']} at {evidence['commit'][:7]} with {command_key}.")


@task_app.command("update")
def update(
    task_id: str,
    status: str | None = typer.Option(None, "--status"),
    next_command_key: str | None = typer.Option(None, "--next-command-key"),
    next_reason: str | None = typer.Option(None, "--next-reason"),
    blocker: list[str] = typer.Option([], "--blocker"),
    acceptance: list[str] = typer.Option([], "--acceptance"),
):
    """Update one task without bypassing state-transition or command checks."""
    try:
        task = update_task(
            task_id,
            status=status,
            next_command_key=next_command_key,
            next_reason=next_reason,
            blockers=blocker or None,
            acceptance=acceptance or None,
        )
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Updated {task['id']} ({task['status']}).")

@workflow_app.command("handoff")
def handoff(task_id: str = typer.Option(..., "--task"), from_file: Path = typer.Option(..., "--from-file")):
    """Store one task's concise handoff from a reviewed local file."""
    try:
        path = write_handoff(task_id, from_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Updated {path.relative_to(Path.cwd())} and its task packet; stage both files.")

@workflow_app.command("recovery-test")
def recovery(json_out: bool = typer.Option(False, "--json")):
    """Assess whether a blank session can recover this branch's task."""
    payload = recovery_test()
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if json_out else ("RECOVERABLE" if payload["recoverable"] else "NOT RECOVERABLE"))
    if not payload["recoverable"]:
        raise typer.Exit(1)

@workflow_app.command("check-staged")
def check_staged(paths_stdin: bool = typer.Option(False, "--paths-stdin")):
    """Verify a staged-file list against `.agent/policy.yaml`.

    Reads one path per line from stdin. Exits 0 when no trust-boundary is
    touched or every touched boundary is covered by a passing task packet;
    exits 1 with a summary otherwise. Used by the pre-commit hook.
    """
    if not paths_stdin:
        typer.echo("--paths-stdin is required; pipe staged paths on stdin", err=True)
        raise typer.Exit(2)
    changed = {line.strip() for line in sys.stdin.read().splitlines() if line.strip()}
    root = repo_root()
    hits = trust_boundary_hits_for_paths(changed, root=root)
    if not hits:
        raise typer.Exit(0)
    missing = []
    task_packets = [task for _, task in load_tasks(root)]
    for boundary_id, meta in sorted(hits.items()):
        required = set(meta["required_command_keys"])
        satisfied = any(
            isinstance(item, dict)
            and item.get("exit_code") == 0
            and item.get("result", "passed") == "passed"
            and item.get("command_key") in required
            for task in task_packets
            for item in task.get("verification", [])
        )
        if not satisfied:
            paths_summary = ", ".join(meta["paths"][:3])
            if len(meta["paths"]) > 3:
                paths_summary += ", ..."
            missing.append(
                f"trust-boundary {boundary_id!r} touched ({paths_summary}); "
                f"stage a task packet with passing evidence for one of: "
                f"{', '.join(sorted(required))}"
            )
    if missing:
        for line in missing:
            typer.echo(line, err=True)
        raise typer.Exit(1)


workflow_app.add_typer(task_app, name="task", help="Claim repository tasks without touching the trading runtime.")
