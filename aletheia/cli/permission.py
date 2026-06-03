"""Permission mode CLI commands."""

import typer

permission_app = typer.Typer(no_args_is_help=True)


@permission_app.command()
def status():
    """Show current permission mode."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    typer.echo(f"Permission mode: {cfg.agent.permission_mode}")


@permission_app.command(name="set")
def set_mode(mode: str = typer.Argument(..., help="ReadOnly|Research|PaperAuto|AllowEditing|Auto")):
    """Set the agent permission mode."""
    from aletheia.config.loader import load_config, save_config
    valid = {"ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto"}
    if mode not in valid:
        typer.echo(f"Invalid mode. Choose from: {', '.join(sorted(valid))}", err=True)
        raise typer.Exit(1)
    cfg = load_config()
    cfg.agent.permission_mode = mode
    save_config(cfg)
    typer.echo(f"Permission mode set to: {mode}")
