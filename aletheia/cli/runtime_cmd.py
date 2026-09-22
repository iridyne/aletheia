"""Runtime home migration CLI."""

import json
from pathlib import Path

import typer

runtime_app = typer.Typer(no_args_is_help=True)


@runtime_app.callback()
def runtime() -> None:
    """Manage the runtime home."""


@runtime_app.command("migrate-home")
def migrate_home(
    source: Path = typer.Option(..., "--from", exists=True, file_okay=False, resolve_path=True),
    target: Path | None = typer.Option(None, "--target", file_okay=False, resolve_path=True),
    apply: bool = typer.Option(False, "--apply", help="Perform the migration; default is dry-run."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the migration plan without writing."),
):
    """Copy verified legacy runtime state into the per-user runtime home."""
    from aletheia.runtime_home_migration import (
        RuntimeHomeMigrationError,
        apply_migration,
        build_plan,
    )
    from aletheia.runtime_paths import default_runtime_home

    if apply and dry_run:
        raise typer.BadParameter("choose either --apply or --dry-run")
    destination = (target or default_runtime_home()).expanduser().resolve()
    try:
        plan = apply_migration(source, destination) if apply else build_plan(source, destination)
    except RuntimeHomeMigrationError as exc:
        typer.echo(f"Runtime-home migration rejected: {exc}")
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
    if not apply:
        typer.echo("Dry-run only. Stop the gateway and repeat with --apply to migrate.")


@runtime_app.command("retire-experiment-schema")
def retire_experiment_schema(
    backup: Path = typer.Option(..., "--backup", exists=True, dir_okay=False, resolve_path=True),
):
    """Offline: retire registry tables after verified same-SHA directory migration."""
    from aletheia.gateway.pidfile import find_gateway_orphans, is_running
    from aletheia.storage.migrations import MigrationStateError, retire_runtime_continuity_tables
    from aletheia.storage.sqlite import acquire_write_lock, get_db, release_write_lock
    from ops.update.auto import _deployment_lock

    with _deployment_lock() as acquired:
        if not acquired or is_running() or find_gateway_orphans():
            typer.echo("Retirement rejected: deployment busy or Gateway running")
            raise typer.Exit(1)
        acquire_write_lock()
        try:
            retire_runtime_continuity_tables(get_db(), backup_path=backup)
        except (MigrationStateError, OSError) as exc:
            typer.echo(f"Retirement rejected: {exc}")
            raise typer.Exit(1) from exc
        finally:
            release_write_lock()
    typer.echo("Experimental runtime schema retired; backup retained.")
