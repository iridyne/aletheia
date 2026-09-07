"""Bitget CLI commands."""

import typer

bitget_app = typer.Typer(no_args_is_help=True)


@bitget_app.command()
def test():
    """Test Bitget demo connection."""
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Credentials (Test connection)")
    from aletheia.config.loader import load_config
    from aletheia.uta.manager import probe_account_reachable, venue_execution_mode

    mode = venue_execution_mode("bitget", load_config().exchange.mode)
    result = probe_account_reachable(mode)
    if result["connected"]:
        typer.echo(f"✓ Bitget {mode} connection successful")
    else:
        typer.echo(f"✗ Bitget {mode} connection failed: {result['error']}", err=True)
        raise typer.Exit(1)


@bitget_app.command()
def sync():
    """Refresh the configured Bitget account through UTA."""
    from aletheia.config.loader import load_config
    from aletheia.storage.migrations import assert_migrations_current
    from aletheia.portfolio.exchange_sync import sync_all_exchanges, status_for

    assert_migrations_current()
    from aletheia.uta.manager import venue_execution_mode

    mode = venue_execution_mode("bitget", load_config().exchange.mode)
    state = sync_all_exchanges(execution_modes=(mode,))
    if state is None or status_for(state) != "ok":
        typer.echo(f"UTA account unavailable: {state}", err=True)
        raise typer.Exit(1)
    account = next(row for row in state["accounts"] if row["broker_id"] == mode)
    typer.echo(f"mode: {mode}")
    typer.echo(f"equity_usdt: {account.get('equity_usdt')}")
    typer.echo(f"available_usdt: {account.get('available_usdt')}")
    typer.echo(f"fetched_at: {state.get('fetched_at')}")
