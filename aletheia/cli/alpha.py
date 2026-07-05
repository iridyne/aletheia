"""`aletheia alpha` — read-only alpha attribution audit."""

from __future__ import annotations

import json

import typer

alpha_app = typer.Typer(no_args_is_help=True)


@alpha_app.command("audit")
def audit(
    days: int = typer.Option(30, "--days", help="Lookback window in days, typically 7, 14, or 30."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Print the five-layer read-only alpha attribution report."""
    from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit, render_alpha_audit

    report = build_alpha_attribution_audit(days=days)
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo("Deprecated: use `uv run aletheia opportunity audit --view alpha` instead.\n")
    typer.echo(render_alpha_audit(report))
