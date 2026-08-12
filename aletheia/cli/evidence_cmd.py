"""Evidence epoch CLI: anchor and inspect observation windows."""
from __future__ import annotations

import json

import typer

evidence_app = typer.Typer(no_args_is_help=True, help="Evidence epoch management.")
epoch_app = typer.Typer(no_args_is_help=True, help="Policy epoch commands.")
evidence_app.add_typer(epoch_app, name="epoch")


@epoch_app.command("register")
def register_epoch(
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON."),
    commit_sha: str | None = typer.Option(
        None, "--commit", help="Override commit SHA (default: current HEAD)."
    ),
):
    """Register evidence epochs for exit_trailing_evidence and entry_correction_v2.

    Anchors the current deployed commit SHA as the epoch boundary so that
    PF, MFE→realized capture, tail, and regime slices are comparable across
    deploys.  Idempotent: re-running on the same build is a no-op.
    """
    from aletheia.learning.evidence_epoch import ensure_evidence_epochs, evidence_epoch_summary

    result = ensure_evidence_epochs(commit_sha=commit_sha)
    summary = evidence_epoch_summary()

    if json_output:
        typer.echo(json.dumps({"registered": result, "summary": summary}, indent=2, ensure_ascii=False))
        return

    sha = summary.get("current_commit") or "unknown"
    typer.echo(f"Commit: {sha[:16] if sha else 'unknown'}")
    typer.echo(f"Registered {len(result)} evidence epoch(s):")
    for path_id, epoch_id in result.items():
        typer.echo(f"  [{path_id}] epoch_id={epoch_id[:12] if epoch_id else '?'}...")
    if not result:
        typer.echo("  (none registered — check logs for errors)")

    for path in summary.get("paths", []):
        sha_ok = path.get("sha_consistent")
        flag = "✓" if sha_ok else ("!" if sha_ok is False else "?")
        status_label = path.get("status", "unknown")
        typer.echo(
            f"  {flag} {path['path_id']}: {status_label} "
            f"(started_at={path.get('started_at', 'none')[:19] if path.get('started_at') else 'none'})"
        )


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
