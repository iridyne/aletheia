"""Deprecated `aletheia flow` compatibility commands."""

from __future__ import annotations

import typer

flow_app = typer.Typer(no_args_is_help=True)


@flow_app.command("audit")
def audit(
    hours: int = typer.Option(24, "--hours", help="Lookback window in hours."),
    limit: int = typer.Option(200, "--limit", help="Maximum opportunities to reconstruct."),
    stage: str | None = typer.Option(None, "--stage", help="Only show opportunities whose timeline includes this stage."),
    verdict: str | None = typer.Option(None, "--verdict", help="Only show opportunities with this counterfactual verdict."),
    symbol: str | None = typer.Option(None, "--symbol", help="Only show opportunities for this canonical symbol."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Compatibility alias for `aletheia opportunity audit`."""
    from aletheia.cli.opportunity import _echo_audit

    _echo_audit(
        hours=hours,
        limit=limit,
        stage=stage,
        verdict=verdict,
        symbol=symbol,
        json_output=json_output,
        deprecated_from="uv run aletheia flow audit",
    )


@flow_app.command("trace")
def trace(
    opportunity_id: str = typer.Option(..., "--opportunity-id", help="Opportunity id to trace."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Compatibility alias for `aletheia opportunity trace`."""
    from aletheia.cli.opportunity import _echo_trace

    _echo_trace(
        opportunity_id=opportunity_id,
        json_output=json_output,
        deprecated_from="uv run aletheia flow trace",
    )
