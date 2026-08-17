"""CLI for the observe-only ranker-confidence sidecar."""

import json
import typer

from aletheia.observation.ranker_confidence import (
    get_ranker_observation,
    list_ranker_observations,
    register_ranker_observation,
    run_ranker_observations,
)

observe_app = typer.Typer(no_args_is_help=True)

@observe_app.command("register-ranker")
def register_ranker(source_experiment_id: str, observation_id: str | None = typer.Option(None, "--id")):
    result = register_ranker_observation(source_experiment_id, observation_id=observation_id)
    typer.echo(json.dumps(result, ensure_ascii=False, default=str))

@observe_app.command("run")
def run_observations():
    typer.echo(json.dumps(run_ranker_observations(), ensure_ascii=False, default=str))

@observe_app.command("status")
def observation_status(observation_id: str):
    typer.echo(json.dumps(get_ranker_observation(observation_id), ensure_ascii=False, default=str))

@observe_app.command("list")
def observation_list():
    typer.echo(json.dumps(list_ranker_observations(), ensure_ascii=False, default=str))
