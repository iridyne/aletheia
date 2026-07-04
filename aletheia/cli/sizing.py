"""`aletheia sizing` - read-only sizing trace diagnostics."""

from __future__ import annotations

import json

import typer

sizing_app = typer.Typer(no_args_is_help=True)


@sizing_app.command("trace")
def trace(
    hours: int | None = typer.Option(24, "--hours", help="Lookback window in hours."),
    days: int | None = typer.Option(None, "--days", help="Lookback window in days. Overrides --hours."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print the read-only sizing trace report."""
    from aletheia.analytics.sizing_trace import build_sizing_trace_audit, render_sizing_trace_audit

    report = build_sizing_trace_audit(hours=hours, days=days)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(render_sizing_trace_audit(report))
