"""CLI over the existing counterfactual prospective-observation subsystem.

Observation v1 deliberately reuses ``filter_shadow_experiments`` instead of
creating another runtime, database, scheduler, or experiment ledger.
"""

from __future__ import annotations

import json

import typer

from aletheia.analytics.filter_controller import run_controller_observe
from aletheia.counterfactual.filter_experiments import build_filter_shadow_experiment_report

observe_app = typer.Typer(
    no_args_is_help=True,
    help="Inspect and run existing observe-only counterfactual windows.",
)

@observe_app.command("list")
def list_observations(
    limit: int = typer.Option(50, min=1, max=500, help="Maximum observations to return."),
):
    """List current filter/ranker prospective observations and latest evidence."""
    typer.echo(json.dumps(
        build_filter_shadow_experiment_report(limit=limit),
        ensure_ascii=False,
        default=str,
    ))

@observe_app.command("status")
def observation_status(experiment_id: str):
    """Show one existing prospective observation by experiment id."""
    rows = build_filter_shadow_experiment_report(limit=500)
    row = next((item for item in rows if str(item.get("id")) == experiment_id), None)
    if row is None:
        typer.echo(f"Observation not found: {experiment_id}", err=True)
        raise typer.Exit(1)
    typer.echo(json.dumps(row, ensure_ascii=False, default=str))

@observe_app.command("run")
def run_observations(
    min_samples: int = typer.Option(20, min=1, help="Minimum evidence sample gate."),
):
    """Run the existing freshness-gated observe cycle once."""
    typer.echo(json.dumps(
        run_controller_observe(min_samples=min_samples),
        ensure_ascii=False,
        default=str,
    ))
