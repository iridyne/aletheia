"""Repository-agent recovery commands; separate from the operator runtime."""
from pathlib import Path
import json
import typer
from aletheia.agent_workflow import bootstrap, claim_task, recovery_test, render_bootstrap, validate, write_handoff

workflow_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)

@workflow_app.command("bootstrap")
def bootstrap_command(json_out: bool = typer.Option(False, "--json")):
    """Recover the current development task from tracked state and Git."""
    payload = bootstrap()
    typer.echo(render_bootstrap(payload, json_out=json_out))
    if payload["task_match_count"] != 1 or any(x["severity"] == "error" for x in payload["findings"]):
        raise typer.Exit(1)

@workflow_app.command("check")
def check(strict: bool = typer.Option(False, "--strict")):
    """Validate tracked task state without touching runtime or trading state."""
    findings = validate(strict=strict)
    for finding in findings:
        typer.echo(f"{finding.severity.upper()}: {finding.message}" + (f" [{finding.task_id}]" if finding.task_id else ""))
    if any(x.severity == "error" for x in findings):
        raise typer.Exit(1)
    typer.echo("Agent workflow state is valid.")

@task_app.command("claim")
def claim(task_id: str, holder: str = typer.Option(..., "--holder"), hours: int = typer.Option(8, "--hours", min=1, max=72)):
    """Claim a ready task for this branch; never changes runtime state."""
    try:
        task = claim_task(task_id, holder=holder, hours=hours)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)
    typer.echo(f"Claimed {task['id']} on {task['ownership']['branch']}.")

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

workflow_app.add_typer(task_app, name="task", help="Claim repository tasks without touching the trading runtime.")
