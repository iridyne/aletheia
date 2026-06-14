"""Cloudflare Tunnel CLI commands."""

import typer

tunnel_app = typer.Typer(no_args_is_help=True)


@tunnel_app.command()
def status():
    """Show tunnel status."""
    from aletheia.gateway.tunnel import tunnel_status
    st = tunnel_status()
    if st["running"]:
        typer.echo(f"Tunnel: running (PID {st['pid']})")
        typer.echo(f"  Hostname: https://{st['hostname']}")
    elif st["enabled"]:
        typer.echo("Tunnel: enabled but not running")
    else:
        typer.echo("Tunnel: disabled")
    typer.echo(f"  cloudflared: {'installed' if st['cloudflared_installed'] else 'NOT FOUND'}")
    if st["tunnel_id"]:
        typer.echo(f"  Tunnel ID: {st['tunnel_id']}")


@tunnel_app.command()
def start():
    """Start the tunnel (without starting the gateway)."""
    from aletheia.gateway.tunnel import start_tunnel
    result = start_tunnel()
    if result["ok"]:
        typer.echo(f"Tunnel {result['status']} → https://{result.get('hostname', '?')}")
    else:
        typer.echo(f"Failed: {result['reason']}", err=True)
        raise typer.Exit(1)


@tunnel_app.command()
def stop():
    """Stop the tunnel."""
    from aletheia.gateway.tunnel import stop_tunnel
    result = stop_tunnel()
    typer.echo(f"Tunnel {result['status']}.")


@tunnel_app.command()
def setup(
    tunnel_id: str = typer.Option(..., prompt="Tunnel ID (from `cloudflared tunnel list`)"),
    hostname: str = typer.Option(..., prompt="Public hostname (e.g. aletheia.example.com)"),
    credentials: str = typer.Option("", help="Path to credentials JSON (auto-detected if blank)"),
):
    """One-time tunnel configuration."""
    from aletheia.gateway.tunnel import find_cloudflared, setup_tunnel

    exe = find_cloudflared()
    if not exe:
        typer.echo("ERROR: cloudflared not found. Install with: winget install Cloudflare.cloudflared", err=True)
        raise typer.Exit(1)

    typer.echo(f"cloudflared found: {exe}")
    result = setup_tunnel(tunnel_id=tunnel_id, hostname=hostname, credentials_path=credentials)
    if result["ok"]:
        typer.echo(f"Tunnel configured: {result['hostname']} → localhost:8787")
        typer.echo("Run `aletheia gateway start` to activate.")
    else:
        typer.echo(f"Setup failed: {result}", err=True)
        raise typer.Exit(1)


@tunnel_app.command()
def enable():
    """Enable tunnel (starts automatically with gateway)."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    if not cfg.tunnel.tunnel_id:
        typer.echo("Run `aletheia tunnel setup` first.", err=True)
        raise typer.Exit(1)
    cfg.tunnel.enabled = True
    save_config(cfg)
    typer.echo("Tunnel enabled. Will start with next `gateway start`.")


@tunnel_app.command()
def disable():
    """Disable tunnel (stops if running)."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.gateway.tunnel import stop_tunnel, is_tunnel_running

    if is_tunnel_running():
        stop_tunnel()
        typer.echo("Tunnel stopped.")

    cfg = load_config()
    cfg.tunnel.enabled = False
    save_config(cfg)
    typer.echo("Tunnel disabled.")
