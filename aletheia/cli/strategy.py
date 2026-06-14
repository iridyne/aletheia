"""Strategy lifecycle CLI commands."""

import typer

strategy_app = typer.Typer(no_args_is_help=True)


@strategy_app.callback()
def _callback():
    """Strategy lifecycle and backtest commands."""
    pass


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


@strategy_app.command()
def backtest(
    strategy_id: str = typer.Argument(None, help="Backtest a single strategy (omit for all active)"),
    bar_limit: int = typer.Option(500, help="Number of OHLCV bars to load"),
    no_retire: bool = typer.Option(False, "--no-retire", help="Don't retire KILL-verdict strategies"),
    report: bool = typer.Option(False, "--report", help="Print markdown daily report"),
    ledger: bool = typer.Option(False, "--ledger", help="Print performance ledger snapshot"),
):
    """Run backtests on active strategies with executable RuleSets.

    Without arguments, evaluates all paper_running/paper_candidate strategies.
    With a strategy_id, backtests only that strategy.
    """
    from aletheia.storage.migrations import run_migrations
    run_migrations()

    from aletheia.strategy.cost_model import CostModel
    from aletheia.strategy.orchestrator import run_all_backtests, generate_daily_report

    typer.echo("Running backtests...")
    result = run_all_backtests(
        retire_killed=not no_retire,
        cost_model=CostModel(),
        bar_limit=bar_limit,
    )

    typer.echo(
        f"Done: {result.strategies_evaluated} evaluated | "
        f"{result.strategies_killed} killed | {result.strategies_kept} kept | "
        f"{result.strategies_review} review | {result.strategies_skipped} skipped"
    )

    for r in result.reports:
        icon = {"kill": "X", "keep": "+", "review": "?"}.get(r.verdict, " ")
        typer.echo(f"  [{icon}] {r.strategy_name}: {r.verdict} -> {r.action_taken}")
        if r.metrics:
            typer.echo(
                f"      WR={r.metrics.get('win_rate', 0):.1%} "
                f"E={r.metrics.get('expectancy', 0):+.2f} "
                f"PF={r.metrics.get('profit_factor', 0):.2f} "
                f"DD={r.metrics.get('max_drawdown_pct', 0):.1f}%"
            )
        if r.error:
            typer.echo(f"      ERROR: {r.error}", err=True)

    if report:
        typer.echo("\n" + generate_daily_report(result))

    if ledger:
        from aletheia.strategy.performance_ledger import (
            compute_ledger_snapshot, format_ledger_report, append_ledger_event,
            compute_ledger_trend,
        )
        snapshot = compute_ledger_snapshot(period_days=7)
        append_ledger_event(snapshot)
        typer.echo("\n" + format_ledger_report(snapshot))

        trend = compute_ledger_trend()
        if trend.improving:
            typer.echo("TREND: IMPROVING")
        elif trend.stagnant:
            typer.echo("TREND: STAGNANT")
        else:
            typer.echo("TREND: NEUTRAL")
        for s in trend.signals:
            typer.echo(f"  - {s}")
