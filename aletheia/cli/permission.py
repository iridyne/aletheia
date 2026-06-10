"""Permission mode CLI commands."""

import typer

permission_app = typer.Typer(no_args_is_help=True)


@permission_app.callback()
def _deprecated():
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Permission Mode")


@permission_app.command()
def status():
    """Show current permission mode."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    typer.echo(f"Permission mode: {cfg.agent.permission_mode}")


@permission_app.command(name="set")
def set_mode(mode: str = typer.Argument(..., help="Observe|Paper|Full")):
    """Set the agent permission mode."""
    from aletheia.config.loader import load_config, save_config
    valid = {"Observe", "Paper", "Full"}
    if mode not in valid:
        typer.echo(f"Invalid mode. Choose from: {', '.join(sorted(valid))}", err=True)
        raise typer.Exit(1)
    cfg = load_config()
    old = cfg.agent.permission_mode
    cfg.agent.permission_mode = mode
    save_config(cfg)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli", "event_type": "permission_changed",
        "entity_type": "permission_mode", "entity_id": "global",
        "before": {"mode": old}, "after": {"mode": mode},
        "message": f"Permission mode {old} -> {mode} via CLI",
    })
    typer.echo(f"Permission mode set to: {mode}")
