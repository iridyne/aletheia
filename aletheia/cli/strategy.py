"""Strategy lifecycle CLI commands."""

import typer

strategy_app = typer.Typer(no_args_is_help=True)


@strategy_app.command("list")
def list_strategies_cmd():
    """List strategies."""
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.repositories import list_strategies

    run_migrations()
    rows = list_strategies(limit=200)
    if not rows:
        typer.echo("No strategies found.")
        return
    for row in rows:
        typer.echo(f"{row['id']}  {row['state']}  {row['name']}  created={row['created_at']}")


@strategy_app.command()
def status(strategy_id: str = typer.Argument(...)):
    """Show strategy lifecycle status."""
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.repositories import get_strategy
    from aletheia.trading.strategy_lifecycle import check_promotion_gates

    run_migrations()
    row = get_strategy(strategy_id)
    if row is None:
        typer.echo("Strategy not found.", err=True)
        raise typer.Exit(1)

    gates = check_promotion_gates(strategy_id)
    typer.echo(f"ID: {row['id']}")
    typer.echo(f"Name: {row['name']}")
    typer.echo(f"State: {row['state']}")
    typer.echo(f"Description: {row.get('description') or ''}")
    typer.echo(f"Parameters: {row.get('parameters') or '{}'}")
    typer.echo(f"Created: {row['created_at']}")
    typer.echo(f"Updated: {row['updated_at']}")
    typer.echo(f"Promotion gates: {'passed' if gates['passed'] else 'failed'}")
    if gates["failures"]:
        typer.echo(f"Failures: {', '.join(gates['failures'])}")
    typer.echo(f"Metrics: {gates['metrics']}")
