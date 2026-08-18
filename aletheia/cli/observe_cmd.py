"""Observation Lab CLI (Issue #1072).

Observe-only surface. No command in this module mutates production behavior,
holds Risk / Execution authority, or reaches for exchange credentials. Positive
verdicts are surfaced through ``observe status``; promotion decisions remain
manual PR + owner admission work outside the CLI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

observe_app = typer.Typer(
    name="observe",
    no_args_is_help=True,
    help="Observation Lab: register, sync, and inspect prospective experiments.",
)


@observe_app.command("list")
def list_observations(
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (collecting / ready_for_review / …)."
    ),
    limit: int = typer.Option(50, "--limit"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List registered Observations with their frozen-unit counts."""
    from aletheia.observation_lab.units import count_frozen_units
    from aletheia.storage import repositories as repo

    rows = repo.list_filter_shadow_experiments(status=status, limit=limit)
    payload = [
        {
            "id": row["id"],
            "filter_name": row["filter_name"],
            "scanner_family": row["scanner_family"],
            "horizon": row["horizon"],
            "status": row["status"],
            "started_at": row["started_at"],
            "frozen_units": count_frozen_units(row["id"]),
        }
        for row in rows
    ]
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    if not payload:
        typer.echo("(no observations)")
        return
    for entry in payload:
        typer.echo(
            f"{entry['id'][:12]}… "
            f"{entry['filter_name']}/{entry['scanner_family']}/{entry['horizon']} "
            f"status={entry['status']} frozen_units={entry['frozen_units']} "
            f"started={entry['started_at'][:19]}"
        )


@observe_app.command("status")
def observation_status(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Show latest evaluation + frozen unit count for one Observation."""
    from aletheia.observation_lab.units import count_frozen_units
    from aletheia.storage import repositories as repo

    row = _find_observation(observation_id)
    if row is None:
        raise typer.Exit(code=1)
    latest = repo.latest_filter_shadow_evaluation(row["id"])
    frozen = count_frozen_units(row["id"])
    if json_output:
        typer.echo(json.dumps(
            {"spec": row, "frozen_units": frozen, "latest_evaluation": latest},
            indent=2,
            default=str,
        ))
        return
    typer.echo(f"id={row['id']}")
    typer.echo(f"scope={row['filter_name']}/{row['scanner_family']}/{row['horizon']}")
    typer.echo(f"status={row['status']} frozen_units={frozen}")
    typer.echo(f"control={row['control_parameter']}")
    typer.echo(f"treatment={row['treatment_parameter']}")
    if row.get("adapter_identity_hash"):
        typer.echo(f"adapter_identity_hash={row['adapter_identity_hash'][:16]}…")
    if latest:
        typer.echo(f"latest.disposition={latest['disposition']}")
        typer.echo(f"latest.control_settled_n={latest.get('control_settled_n')}")
        typer.echo(f"latest.treatment_settled_n={latest.get('treatment_settled_n')}")
        typer.echo(f"latest.edge_delta_pct={latest.get('edge_delta_pct')}")


@observe_app.command("sync")
def sync_all(
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Capture new first-exposure units + evaluate all collecting Observations.

    Same body as the controller's periodic cycle for the Lab; convenient when
    an operator wants an immediate refresh without waiting for the next cycle.
    """
    from aletheia.counterfactual.filter_experiments import (
        capture_frozen_units_for,
        evaluate_active_shadow_experiments,
    )
    from aletheia.storage import repositories as repo

    captured = 0
    for exp in repo.list_filter_shadow_experiments(status="collecting"):
        captured += capture_frozen_units_for(exp)
    summary = evaluate_active_shadow_experiments()
    if json_output:
        typer.echo(json.dumps(
            {"frozen_units_captured": captured, "evaluation": summary},
            indent=2,
        ))
        return
    typer.echo(f"captured {captured} new frozen units")
    typer.echo(f"evaluation: {summary}")


@observe_app.command("evaluate")
def evaluate_observation(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one evaluation pass for a specific Observation."""
    from aletheia.counterfactual.filter_experiments import evaluate_one_experiment

    row = _find_observation(observation_id)
    if row is None:
        raise typer.Exit(code=1)
    try:
        result = evaluate_one_experiment(row["id"])
    except ValueError as exc:
        typer.echo(f"evaluation failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    typer.echo(f"disposition={result.get('disposition')}")
    for key in (
        "control_settled_n", "treatment_settled_n",
        "treatment_raw_avg_net_pct", "edge_delta_pct",
    ):
        if key in result:
            typer.echo(f"{key}={result[key]}")


@observe_app.command("register")
def register(
    spec_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="Path to a YAML file describing the Observation spec.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Register an Observation from a hand-authored YAML spec.

    Required keys: ``filter_name``, ``stage``, ``scanner_family``, ``direction``,
    ``market_type``, ``horizon``, ``control`` (dict), ``treatment`` (dict).
    Optional: ``filter_version``, ``assignment_policy``, ``min_samples``,
    ``min_coverage``, ``min_edge_delta_pct``. The adapter causal identity hash
    is computed at registration time from the declared adapter identity vector
    (see docs/design/contracts/observation_lab_contract.md).
    """
    import yaml  # local — CLI-only path, keeps import cost off hot startup

    from aletheia.counterfactual.filter_experiments import register_observation_from_spec

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"cannot parse YAML: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(spec, dict):
        typer.echo("spec file must contain a YAML mapping", err=True)
        raise typer.Exit(code=1)
    try:
        observation_id = register_observation_from_spec(spec)
    except (KeyError, ValueError) as exc:
        typer.echo(f"failed to register: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps({"observation_id": observation_id}, indent=2))
    else:
        typer.echo(f"registered observation {observation_id}")


@observe_app.command("close")
def close_observation(
    observation_id: str = typer.Argument(..., help="Observation id (or unique prefix)."),
    reason: str = typer.Option(
        ..., "--reason",
        help="Human-readable close reason (recorded in invalidation_reason).",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Stop a collecting Observation and record the operator's reason.

    Owner review §7: stopping preserves all evidence/lifecycle history — this
    only halts further collection. Historical evaluations remain queryable.
    """
    from aletheia.storage import repositories as repo

    row = _find_observation(observation_id)
    if row is None:
        raise typer.Exit(code=1)
    if row["status"] != "collecting":
        typer.echo(f"observation is not collecting (status={row['status']}); no-op")
        return
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    repo.update_filter_shadow_experiment(
        row["id"],
        status="invalidated",
        last_evaluated_at=now,
        completed_at=now,
        invalidation_reason=f"operator_closed:{reason[:200]}",
    )
    if json_output:
        typer.echo(json.dumps(
            {"id": row["id"], "closed_at": now, "reason": reason}, indent=2,
        ))
    else:
        typer.echo(f"closed {row['id']} at {now}: {reason}")


def _find_observation(observation_id: str) -> dict | None:
    """Resolve an id or unique prefix to a single observation row."""
    from aletheia.storage import repositories as repo

    matches = [
        row for row in repo.list_filter_shadow_experiments(limit=1000)
        if row["id"] == observation_id or row["id"].startswith(observation_id)
    ]
    if not matches:
        typer.echo(f"no observation matches {observation_id!r}", err=True)
        return None
    if len(matches) > 1:
        typer.echo(
            f"prefix {observation_id!r} is ambiguous — "
            f"matches {[m['id'][:12] for m in matches]}",
            err=True,
        )
        return None
    return matches[0]
