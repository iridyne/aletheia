"""Self-upgrade CLI commands: `aletheia update check|apply`."""

import typer

update_app = typer.Typer(no_args_is_help=True)


@update_app.callback()
def _deprecated():
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Update")


@update_app.command()
def check():
    """Check whether a newer version is available on the configured branch."""
    from aletheia.update.applier import check_for_update
    result = check_for_update()
    if not result.get("ok"):
        typer.echo(f"Update check unavailable: {result.get('reason')}")
        raise typer.Exit(0)
    if result["has_update"]:
        typer.echo(f"Update available: {result['behind_count']} commit(s) behind "
                   f"origin/{result['branch']}.")
        typer.echo(f"  current: {result['current_commit'][:8]}  remote: {result['remote_commit'][:8]}")
        typer.echo("Run `aletheia update apply` to update, then restart the gateway.")
    else:
        typer.echo(f"Up to date on origin/{result['branch']} ({result['current_commit'][:8]}).")


@update_app.command()
def apply(yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")):
    """Pull the latest code (refuses if the worktree is dirty) and prompt to restart."""
    if not yes:
        confirm = typer.confirm("Pull latest code and apply update? Gateway restart required after.")
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(0)
    from aletheia.update.applier import apply_update
    result = apply_update()
    if not result.get("applied"):
        typer.echo(f"Update not applied: {result.get('reason')}")
        if result.get("message"):
            typer.echo(f"  {result['message']}")
        raise typer.Exit(1)
    typer.echo(f"Updated {result['old_commit'][:8]} -> {result['new_commit'][:8]} "
               f"(deps reinstalled: {result['deps_reinstalled']}).")
    typer.echo("Restart the gateway to load the new code: aletheia gateway restart")
