"""`aletheia funnel` - read-only trade funnel diagnostics."""

from __future__ import annotations

import json

import typer

funnel_app = typer.Typer(no_args_is_help=True)


@funnel_app.command("audit")
def audit(
    hours: int | None = typer.Option(24, "--hours", help="Lookback window in hours."),
    days: int | None = typer.Option(None, "--days", help="Lookback window in days. Overrides --hours."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print the read-only opportunity-to-position funnel report."""
    from aletheia.analytics.trade_funnel import build_trade_funnel_audit, render_trade_funnel_audit

    report = build_trade_funnel_audit(hours=hours, days=days)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(render_trade_funnel_audit(report))
