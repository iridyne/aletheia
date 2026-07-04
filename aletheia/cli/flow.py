"""`aletheia flow` - read-only opportunity lifecycle ledger."""

from __future__ import annotations

import json

import typer

flow_app = typer.Typer(no_args_is_help=True)


@flow_app.command("audit")
def audit(
    hours: int = typer.Option(24, "--hours", help="Lookback window in hours."),
    limit: int = typer.Option(200, "--limit", help="Maximum opportunities to reconstruct."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print the read-only opportunity flow ledger."""
    from aletheia.analytics.opportunity_flow import build_opportunity_flow_ledger, render_flow_audit

    report = build_opportunity_flow_ledger(hours=hours, limit=limit)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(render_flow_audit(report))


@flow_app.command("trace")
def trace(
    opportunity_id: str = typer.Option(..., "--opportunity-id", help="Opportunity id to trace."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print one opportunity's reconstructed lifecycle."""
    from aletheia.analytics.opportunity_flow import render_flow_trace, trace_opportunity_flow

    record = trace_opportunity_flow(opportunity_id)
    if json_output:
        typer.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    typer.echo(render_flow_trace(record))
