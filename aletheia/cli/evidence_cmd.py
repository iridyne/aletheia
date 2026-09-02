"""Evidence epoch CLI: anchor and inspect observation windows."""
from __future__ import annotations

import json

import typer

evidence_app = typer.Typer(no_args_is_help=True, help="Evidence epoch management.")
epoch_app = typer.Typer(no_args_is_help=True, help="Policy epoch commands.")
evidence_app.add_typer(epoch_app, name="epoch")
universe_discovery_app = typer.Typer(
    no_args_is_help=True,
    help="Frozen Universe candidate-selection benchmark.",
)
evidence_app.add_typer(universe_discovery_app, name="universe-discovery")
candidate_selection_app = typer.Typer(
    no_args_is_help=True,
    help="Candidate Selection Utility Shadow evidence.",
)
evidence_app.add_typer(candidate_selection_app, name="candidate-selection")


@candidate_selection_app.command("status")
def candidate_selection_status(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show frozen estimator and prospective fixed-look qualification state."""
    from aletheia.universe.candidate_selection_evaluation import (
        evaluate_candidate_selection_policy,
    )

    result = evaluate_candidate_selection_policy()
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    for key in (
        "verdict", "policy_epoch_id", "training_cutoff", "expected_cluster_n",
        "complete_cluster_n", "fixed_look_cluster_n", "full_arm_outcome_coverage",
        "paired_utility_delta", "paired_utility_lower_bound", "qualification_eligible",
        "promotion_eligible", "owner_admission_required",
    ):
        if key in result:
            typer.echo(f"{key}: {result[key]}")
    for key in ("blocking_reasons", "invalid_reasons"):
        if result.get(key):
            typer.echo(f"{key}: {', '.join(result[key])}")


def _emit_benchmark(result: dict, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return
    for key in (
        "id", "status", "phase", "contract_hash", "code_commit", "started_at",
        "capture_ends_at", "finalize_not_before", "batch_count", "utc_date_count",
        "outcome_counts", "artifact_path", "artifact_sha256",
    ):
        if key in result and result[key] is not None:
            typer.echo(f"{key}: {result[key]}")


@universe_discovery_app.command("start")
def universe_discovery_start(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Open the single preregistered 14-day prospective window."""
    from aletheia.universe.discovery_benchmark import start_benchmark, status_benchmark

    try:
        window = start_benchmark()
        result = status_benchmark(str(window["id"]))
    except (KeyError, RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    _emit_benchmark(result, json_output)


@universe_discovery_app.command("status")
def universe_discovery_status(
    window_id: str | None = typer.Option(None, "--id", help="Window id; default active."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show frozen identity, capture progress, and settlement state."""
    from aletheia.universe.discovery_benchmark import status_benchmark

    try:
        result = status_benchmark(window_id)
    except KeyError as exc:
        typer.echo("universe_discovery_benchmark_not_found", err=True)
        raise typer.Exit(1) from exc
    _emit_benchmark(result, json_output)


@universe_discovery_app.command("abort")
def universe_discovery_abort(
    reason: str = typer.Option(..., "--reason", help="Required audit reason."),
    window_id: str | None = typer.Option(None, "--id", help="Window id; default active."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Abort an active window without deleting its evidence."""
    from aletheia.universe.discovery_benchmark import abort_benchmark, status_benchmark

    try:
        target = window_id or str(status_benchmark()["id"])
        result = abort_benchmark(target, reason=reason)
    except (KeyError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    _emit_benchmark(result, json_output)


@universe_discovery_app.command("finalize")
def universe_discovery_finalize(
    window_id: str = typer.Option(..., "--id", help="Window id."),
    output: str = typer.Option(..., "--output", help="Destination .json.gz path."),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Finalize after the fixed deadline and write one canonical result package."""
    from aletheia.universe.discovery_benchmark import finalize_benchmark

    try:
        result = finalize_benchmark(window_id, output)
    except (KeyError, RuntimeError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    _emit_benchmark(result, json_output)



@epoch_app.command("register")
def register_epoch(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    commit_sha: str | None = typer.Option(
        None,
        "--commit",
        help="Override commit SHA for legacy evidence paths only (default: current runtime).",
    ),
):
    """Register durable observation-only evidence epochs.

    Anchors the current deployed commit SHA as the epoch boundary so that
    PF, MFE→realized capture, tail, and regime slices are comparable across
    deploys. Profit-loop identity always comes from the durable Runtime Session;
    ``--commit`` cannot override it. Idempotent on the same identity.
    """
    from aletheia.learning.evidence_epoch import (
        EXPECTED_EVIDENCE_PATH_IDS,
        ensure_evidence_epochs,
        evidence_epoch_summary,
    )

    result = ensure_evidence_epochs(commit_sha=commit_sha)
    summary = evidence_epoch_summary()
    missing_paths = sorted(EXPECTED_EVIDENCE_PATH_IDS - set(result))
    complete = not missing_paths

    if json_output:
        typer.echo(json.dumps({
            "registered": result,
            "summary": summary,
            "complete": complete,
            "missing_paths": missing_paths,
        }, indent=2, ensure_ascii=False))
        if not complete:
            raise typer.Exit(1)
        return

    sha = summary.get("current_commit") or "unknown"
    typer.echo(f"Commit: {sha[:16] if sha else 'unknown'}")
    typer.echo(f"Registered {len(result)} evidence epoch(s):")
    for path_id, epoch_id in result.items():
        typer.echo(f"  [{path_id}] epoch_id={epoch_id[:12] if epoch_id else '?'}...")
    if not result:
        typer.echo("  (none registered — check logs for errors)")
    if missing_paths:
        typer.echo(
            "Missing required evidence path(s): " + ", ".join(missing_paths),
            err=True,
        )

    for path in summary.get("paths", []):
        sha_ok = path.get("sha_consistent")
        flag = "✓" if sha_ok else ("!" if sha_ok is False else "?")
        status_label = path.get("status", "unknown")
        typer.echo(
            f"  {flag} {path['path_id']}: {status_label} "
            f"(started_at={path.get('started_at', 'none')[:19] if path.get('started_at') else 'none'})"
        )
    if not complete:
        raise typer.Exit(1)


@epoch_app.command("status")
def epoch_status(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
):
    """Show current evidence epoch anchoring status."""
    from aletheia.learning.evidence_epoch import evidence_epoch_summary

    summary = evidence_epoch_summary()
    if json_output:
        typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
        return

    sha = summary.get("current_commit") or "unknown"
    typer.echo(f"Commit  : {sha[:16] if sha else 'not available'}")
    typer.echo(f"Anchored: {summary.get('all_anchored')}")
    typer.echo(f"SHA OK  : {summary.get('all_sha_consistent')}")
    typer.echo("")
    for path in summary.get("paths", []):
        sha_ok = path.get("sha_consistent")
        flag = "✓" if sha_ok else ("!" if sha_ok is False else "?")
        deploy_sha = path.get("deploy_commit") or "none"
        typer.echo(
            f"  {flag} {path['path_id']}\n"
            f"      epoch_id  : {path.get('epoch_id', 'not_registered')}\n"
            f"      started_at: {path.get('started_at', 'none')}\n"
            f"      sha       : {deploy_sha[:16] if deploy_sha and deploy_sha != 'none' else deploy_sha}\n"
            f"      status    : {path.get('status', 'unknown')}"
        )
