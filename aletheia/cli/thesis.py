"""`aletheia thesis` - read-only thesis lineage diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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


@thesis_app.command("snapshot")
def snapshot(
    days: int = typer.Option(7, "--days", help="Lookback window in days."),
    output: Path | None = typer.Option(None, "--output", help="Optional JSON output path."),
):
    """Write a read-only thesis lineage audit snapshot under data/diagnostics."""
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit
    from aletheia.config.loader import get_data_dir

    report = build_thesis_lineage_audit(days=days)
    out_path = _snapshot_path(days, output, get_data_dir())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report.get("summary") or {}
    completeness = report.get("data_completeness") or {}
    missing = completeness.get("missing_counts") or {}
    typer.echo(f"snapshot: {out_path}")
    typer.echo(f"total_thesis_count: {summary.get('total_thesis_count', 0)}")
    typer.echo(f"missing_decision_action: {missing.get('missing_decision_action', 0)}")
    typer.echo(f"non_traded_missing_shadow_outcome: {missing.get('non_traded_missing_shadow_outcome', 0)}")
    typer.echo(f"non_traded_missing_forward_return: {missing.get('non_traded_missing_forward_return', 0)}")


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


def _snapshot_path(days: int, output: Path | None, data_dir: Path) -> Path:
    if output is not None:
        if output.suffix:
            return output
        return output / _snapshot_filename(days)
    return data_dir / "diagnostics" / _snapshot_filename(days)


def _snapshot_filename(days: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"thesis_lineage_audit_{max(1, int(days or 7))}d_{stamp}.json"
