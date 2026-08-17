"""Software-update CLI for development and installed Aletheia runtimes."""

from __future__ import annotations

import json

import typer

update_app = typer.Typer(
    no_args_is_help=False,
    invoke_without_command=True,
    help="Update a source checkout end to end or manage packaged releases.",
)
auto_app = typer.Typer(
    no_args_is_help=True,
    help="Follow stable release tags on an explicitly opted-in Linux Paper runtime every five minutes.",
)


def _is_packaged() -> bool:
    from aletheia.update.packaged_commands import is_packaged_mode

    return is_packaged_mode()


@update_app.callback(invoke_without_command=True)
def update(
    ctx: typer.Context,
    restart: bool = typer.Option(
        True,
        "--restart/--no-restart",
        help="Rebuild dashboard assets and restart a source runtime after verification.",
    ),
) -> None:
    """Install the latest verified update for this runtime."""

    if ctx.invoked_subcommand is not None:
        return
    _run_update(restart=restart)


def _run_update(*, restart: bool) -> None:
    if _is_packaged():
        if not restart:
            typer.echo(
                "`--no-restart` is only available for source checkouts; "
                "packaged updates require Tray activation and restart.",
                err=True,
            )
            raise typer.Exit(1)
        from aletheia.update.packaged_commands import stage_update_blocking

        result = stage_update_blocking()
        state = result.get("state") or {}
        if not result.get("accepted"):
            typer.echo(f"Update not started: {result.get('reason')}")
            raise typer.Exit(1)
        if state.get("status") == "up_to_date":
            typer.echo("Already up to date.")
            return
        if state.get("status") == "failed":
            typer.echo(f"Update staging failed: {state.get('error')}", err=True)
            raise typer.Exit(1)
        if state.get("phase") != "ready_to_switch":
            typer.echo(f"Unexpected update state: {state.get('phase')}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Release {state.get('target_release')} is verified and migration-preflighted.")
        typer.echo("Aletheia Tray will complete the short switch and automatic health check.")
        return

    from aletheia.update.applier import apply_update

    result = apply_update(restart=restart)
    if not result.get("applied"):
        typer.echo(f"Update not applied: {result.get('reason')}")
        if result.get("message"):
            typer.echo(f"  {result['message']}")
        raise typer.Exit(1)
    typer.echo(
        f"Updated {result['old_commit'][:8]} -> {result['new_commit'][:8]} "
        f"(deps reinstalled: {result['deps_reinstalled']})."
    )
    if restart:
        typer.echo("Gateway restarted and readiness verified.")
    else:
        typer.echo("Restart the gateway to load the new code: aletheia gateway restart")


@update_app.command()
def status(json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")):
    """Show durable local update progress without repeating a network check."""

    from aletheia.update.applier import update_progress

    result = update_progress()
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    typer.echo(f"Mode: {result.get('mode', 'unknown')}")
    typer.echo(f"Phase: {result.get('phase', 'unknown')}")
    typer.echo(f"Status: {result.get('status', 'unknown')}")
    if result.get("target_release"):
        typer.echo(f"Target: {result['target_release']}")
    if result.get("progress") is not None:
        typer.echo(f"Progress: {float(result['progress']) * 100:.0f}%")
    if result.get("message"):
        typer.echo(str(result["message"]))
    if result.get("error"):
        typer.echo(f"Error: {result['error']}", err=True)


@update_app.command()
def check():
    """Check whether a newer development commit or signed release is available."""

    from aletheia.update.applier import check_for_update

    result = check_for_update()
    if not result.get("ok"):
        typer.echo(f"Update check unavailable: {result.get('reason')}")
        if result.get("message"):
            typer.echo(f"  {result['message']}")
        raise typer.Exit(0)

    if result.get("mode") == "packaged":
        if result.get("has_update"):
            typer.echo(
                f"Signed update available: {result.get('available_release')} ({result.get('size_bytes', 0)} bytes)."
            )
            typer.echo("Run `aletheia update` to stage and install it through Aletheia Tray.")
        else:
            typer.echo(f"Up to date ({result.get('current_release')}).")
        return

    if result["has_update"]:
        typer.echo(f"Update available: {result['behind_count']} commit(s) behind origin/{result['branch']}.")
        typer.echo(f"  current: {result['current_commit'][:8]}  remote: {result['remote_commit'][:8]}")
        typer.echo("Run `aletheia update` for the complete pull, build, and restart.")
    else:
        typer.echo(f"Up to date on origin/{result['branch']} ({result['current_commit'][:8]}).")


@update_app.command()
def rollback(yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation.")):
    """Request rollback within the packaged update safety window."""

    if not _is_packaged():
        typer.echo("Packaged rollback is unavailable in a development checkout.", err=True)
        raise typer.Exit(1)
    if not yes and not typer.confirm(
        "Restore the pre-update backup and previous release? Changes since the update will be lost."
    ):
        typer.echo("Aborted.")
        raise typer.Exit(0)
    from aletheia.update.applier import rollback_update

    result = rollback_update()
    if not result.get("accepted"):
        typer.echo(f"Rollback not requested: {result.get('reason')}", err=True)
        if result.get("message"):
            typer.echo(f"  {result['message']}", err=True)
        raise typer.Exit(1)
    typer.echo("Rollback request accepted. Aletheia Tray will restore the checkpoint safely.")


@auto_app.command("status")
def auto_status(json_output: bool = typer.Option(False, "--json", help="Print machine-readable JSON.")):
    """Show durable release-tag deployment state without checking the network."""
    from aletheia.update.auto import status

    result = status()
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False))
        return
    typer.echo(f"Phase: {result.get('phase', 'idle')}")
    typer.echo(f"Status: {result.get('status', 'not_installed')}")
    if result.get("target_tag"):
        typer.echo(f"Target: {result['target_tag']} ({str(result.get('target_sha') or '')[:12]})")
    if result.get("reason"):
        typer.echo(f"Reason: {result['reason']}")


@auto_app.command("run")
def auto_run():
    """Check once and deploy a newer stable release tag on a Paper runtime."""
    from aletheia.update.auto import run

    result = run()
    typer.echo(result.get("reason", "unknown"))
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("resume")
def auto_resume():
    """Clear a recovery hold after the operator has repaired the runtime."""
    from aletheia.update.auto import resume

    result = resume()
    typer.echo(result.get("reason", "resumed"))
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("install")
def auto_install():
    """Install the timer; set ALETHEIA_RUNTIME_ROLE=paper for auto-deployment."""
    from aletheia.update.auto import install

    result = install()
    typer.echo(result.get("reason", "unknown"))
    if not result.get("ok"):
        raise typer.Exit(1)


@auto_app.command("uninstall")
def auto_uninstall():
    """Remove only Aletheia's release-tag timer and service units."""
    from aletheia.update.auto import uninstall

    result = uninstall()
    typer.echo(result.get("reason", "unknown"))
    if not result.get("ok"):
        raise typer.Exit(1)


update_app.add_typer(auto_app, name="auto")
