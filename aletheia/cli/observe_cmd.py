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


@observe_app.command("overview")
def observation_overview(
    equivalence_limit: int = typer.Option(
        5, "--equivalence-limit",
        help="How many recent UFC equivalence records to include.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Whole-Lab health snapshot: experiments, UFC equivalence, manifests, checkpoint.

    This is the "is Observation Lab alive and healthy?" single command
    Fedora operators run to answer the four questions ``observation_lab_v1_design.md``
    §9 asks: is it running, is UFC still matching, did anything drift, and
    what manifests are live.
    """
    payload = _collect_overview(equivalence_limit=equivalence_limit)
    if json_output:
        typer.echo(json.dumps(payload, indent=2, default=str))
        return

    typer.echo(
        f"experiments: {payload['experiments']['total']} total, "
        f"{payload['experiments']['collecting']} collecting, "
        f"{payload['experiments']['ready_for_review']} ready_for_review, "
        f"{payload['experiments']['invalidated']} invalidated"
    )
    typer.echo(
        f"ufc_shadow_equivalence: last {equivalence_limit} — "
        f"matched={payload['ufc_equivalence']['recent_matched']}/"
        f"{payload['ufc_equivalence']['recent_total']}"
    )
    for record in payload["ufc_equivalence"]["recent_records"]:
        marker = "✓" if record["matched"] else "✗"
        detail = (
            f" mismatch={record['mismatch_field']}"
            if not record["matched"] and record.get("mismatch_field") else ""
        )
        typer.echo(
            f"  {marker} {record['checked_at']} {record['observation_id']}{detail}"
        )
    typer.echo(f"active_manifests: {payload['manifests']['active_count']}")
    for m in payload["manifests"]["active"]:
        typer.echo(
            f"  {m['observation_id']} gen={m['generation']} "
            f"hash={m['manifest_hash'][:12]}… composability={m['composability_contract']}"
        )
    cp = payload["checkpoint"]
    if cp["latest"] is None:
        typer.echo("checkpoint: (no fingerprint recorded yet)")
    else:
        marker = "✓" if cp["verified"]["ok"] else "✗"
        typer.echo(
            f"checkpoint: {marker} recorded_at={cp['latest']['recorded_at']} "
            f"app_migrations={cp['latest']['app_migration_count']} "
            f"active_manifests={cp['latest']['active_manifest_count']} "
            f"reason={cp['verified']['reason']}"
        )


@observe_app.command("checkpoint")
def observation_checkpoint(
    verify_only: bool = typer.Option(
        False, "--verify-only",
        help="Only verify without appending; useful when diagnosing drift.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run one joint app.db + lab.db + manifest checkpoint pass."""
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
        verify_joint_checkpoint,
    )

    verdict = verify_joint_checkpoint()
    saved = False
    if not verify_only:
        save_joint_checkpoint(compute_joint_checkpoint())
        saved = True
    payload = {
        "ok": verdict.ok,
        "reason": verdict.reason,
        "current_fingerprint": verdict.current_fingerprint,
        "last_fingerprint": (
            verdict.last.joint_fingerprint if verdict.last is not None else None
        ),
        "saved": saved,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    marker = "✓" if verdict.ok else "✗"
    typer.echo(f"{marker} {verdict.reason}")
    typer.echo(f"current_fingerprint={verdict.current_fingerprint[:16]}…")
    if verdict.last is not None:
        typer.echo(f"last_fingerprint   ={verdict.last.joint_fingerprint[:16]}…")
    if saved:
        typer.echo("appended new checkpoint row (idempotent no-op if unchanged)")


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


@observe_app.command("publish-manifest")
def publish_manifest_cmd(
    spec_path: Path = typer.Argument(
        ..., exists=True, dir_okay=False, readable=True,
        help="Path to a YAML file describing the ExecutionManifest.",
    ),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Publish one ExecutionManifest to lab.db (Packet 2 admission entry).

    Required YAML keys mirror ``aletheia.observation_lab.manifest.build_manifest``:

    - ``observation_id``, ``generation``, ``spec_hash``
    - ``treatment_adapter_id``, ``treatment_adapter_version``
    - ``assignment_policy``, ``assignment_salt``, ``assignment_unit_key_template``
    - ``allocation_pct`` (0-1 float)
    - ``control_payload``, ``treatment_payload`` (dicts)
    - ``eligible_population`` (dict; include ``population_key`` when
      ``composability_contract`` is ``disjoint_population``)
    - ``behavior_contract_hash``, ``interference_key``
    - ``composability_contract`` — ``exclusive`` / ``disjoint_population`` / ``factorial``
    - ``effective_from`` (ISO UTC timestamp)
    - ``deterministic_baseline`` (dict), ``unavailable_fallback``
      (``baseline`` / ``exclude``)

    Optional: ``effective_until``, ``owner_admission_ref``.

    Exit codes:
    - 0: manifest published or silent no-op (same hash already present).
    - 1: YAML parse error, missing/invalid field, or generation conflict
      (different content already occupies this ``(observation_id,
      generation)``; caller must bump generation).
    """
    import yaml

    from aletheia.observation_lab.manifest import (
        ManifestGenerationConflict,
        build_manifest,
        publish_manifest,
    )

    try:
        spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        typer.echo(f"cannot parse YAML: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(spec, dict):
        typer.echo("spec file must contain a YAML mapping", err=True)
        raise typer.Exit(code=1)

    try:
        manifest = build_manifest(**spec)
    except (TypeError, ValueError, KeyError) as exc:
        typer.echo(f"invalid manifest spec: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    try:
        newly_inserted = publish_manifest(manifest)
    except ManifestGenerationConflict as exc:
        typer.echo(f"generation conflict: {exc}", err=True)
        typer.echo(
            "bump ``generation`` in the spec for a semantically different manifest.",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    payload = {
        "manifest_id": manifest.manifest_id,
        "manifest_hash": manifest.manifest_hash,
        "observation_id": manifest.observation_id,
        "generation": manifest.generation,
        "newly_inserted": newly_inserted,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return
    verb = "published" if newly_inserted else "already published (no-op)"
    typer.echo(f"{verb} manifest {manifest.manifest_id[:12]}…")
    typer.echo(f"  observation_id={manifest.observation_id} generation={manifest.generation}")
    typer.echo(f"  manifest_hash={manifest.manifest_hash}")


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


def _collect_overview(*, equivalence_limit: int = 5) -> dict:
    """Whole-Lab snapshot backing ``observe overview``.

    Kept as a pure function so unit tests can seed lab.db, call it once,
    and inspect the shape without spawning a subprocess. The CLI command
    is a thin renderer on top.
    """
    from aletheia.observation_lab.checkpoint import (
        latest_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.manifest import load_active_manifests
    from aletheia.storage import repositories as repo

    experiments = repo.list_filter_shadow_experiments(limit=1000)
    counts = {"total": len(experiments)}
    for status in ("collecting", "ready_for_review", "completed_no_support", "invalidated"):
        counts[status] = sum(1 for r in experiments if r["status"] == status)

    limit = max(0, int(equivalence_limit))
    equivalence_rows = [
        dict(row) for row in get_lab_db().execute(
            "SELECT observation_id, checked_at, matched, mismatch_field, "
            "learning_verdict, recomputed_verdict, notes "
            "FROM ufc_shadow_equivalence ORDER BY checked_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    ]
    recent_matched = sum(1 for r in equivalence_rows if r["matched"] == 1)

    active_manifests = load_active_manifests()
    manifest_dicts = [
        {
            "observation_id": m.observation_id,
            "generation": m.generation,
            "manifest_hash": m.manifest_hash,
            "composability_contract": m.composability_contract,
            "effective_from": m.effective_from,
            "effective_until": m.effective_until,
        }
        for m in active_manifests
    ]

    latest_cp = latest_joint_checkpoint()
    verified = verify_joint_checkpoint()
    checkpoint_payload = {
        "latest": None if latest_cp is None else {
            "recorded_at": latest_cp.recorded_at,
            "joint_fingerprint": latest_cp.joint_fingerprint,
            "app_migration_head": latest_cp.app_migration_head,
            "app_migration_count": latest_cp.app_migration_count,
            "active_manifest_count": latest_cp.active_manifest_count,
        },
        "verified": {
            "ok": verified.ok,
            "reason": verified.reason,
            "current_fingerprint": verified.current_fingerprint,
        },
    }

    return {
        "experiments": counts,
        "ufc_equivalence": {
            "recent_records": [
                {
                    "observation_id": r["observation_id"],
                    "checked_at": r["checked_at"],
                    "matched": bool(r["matched"]),
                    "mismatch_field": r.get("mismatch_field"),
                    "learning_verdict": r.get("learning_verdict"),
                    "recomputed_verdict": r.get("recomputed_verdict"),
                    "notes": r.get("notes"),
                }
                for r in equivalence_rows
            ],
            "recent_matched": recent_matched,
            "recent_total": len(equivalence_rows),
        },
        "manifests": {
            "active": manifest_dicts,
            "active_count": len(manifest_dicts),
        },
        "checkpoint": checkpoint_payload,
    }


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
