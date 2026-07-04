"""`aletheia thesis` - read-only thesis lineage diagnostics."""

from __future__ import annotations

import json

import typer

thesis_app = typer.Typer(no_args_is_help=True)


@thesis_app.command("audit")
def audit(
    days: int = typer.Option(7, "--days", help="Lookback window in days."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print the read-only thesis lineage audit."""
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit, render_thesis_lineage_audit

    report = build_thesis_lineage_audit(days=days)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(render_thesis_lineage_audit(report))


@thesis_app.command("records")
def records(
    days: int = typer.Option(7, "--days", help="Lookback window in days."),
    limit: int = typer.Option(100, "--limit", help="Maximum records to output."),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Output machine-readable JSON."),
):
    """Print reconstructed thesis lineage records."""
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_records

    data = build_thesis_lineage_records(days=days)[:limit]
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    for row in data:
        typer.echo(
            f"{row.get('lineage_id')} {row.get('symbol')} {row.get('direction')} "
            f"verdict={row.get('verdict')} traded={row.get('was_traded')}"
        )
