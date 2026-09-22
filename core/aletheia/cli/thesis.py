"""`aletheia thesis` - read-only thesis lineage diagnostics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

thesis_app = typer.Typer(no_args_is_help=True)


@thesis_app.command("audit", hidden=True)
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
    typer.echo("Deprecated: use `uv run aletheia opportunity audit --view thesis` instead.\n")
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
    typer.echo(f"mature_missing_shadow: {missing.get('mature_non_traded_missing_shadow_outcome', 0)}")
    typer.echo(f"mature_missing_forward: {missing.get('mature_non_traded_missing_forward_return', 0)}")
    typer.echo(f"non_traded_missing_shadow_outcome: {missing.get('non_traded_missing_shadow_outcome', 0)}")
    typer.echo(f"non_traded_missing_forward_return: {missing.get('non_traded_missing_forward_return', 0)}")


@thesis_app.command("snapshots")
def snapshots(
    limit: int = typer.Option(14, "--limit", help="Maximum saved snapshots to list."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """List saved thesis lineage audit snapshots from data/diagnostics."""
    from aletheia.config.loader import get_data_dir

    data = _load_snapshot_rows(get_data_dir() / "diagnostics", limit)
    if json_output:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
        return
    typer.echo("Thesis Lineage Snapshots")
    if not data:
        typer.echo("  n/a")
        return
    for row in data:
        if row.get("error"):
            typer.echo(f"  {row['file']}: error={row['error']}")
            continue
        typer.echo(
            f"  {row.get('generated_at') or row['file']} file={row['file']} "
            f"window={row.get('window_days')}d total={row.get('total_thesis_count', 0)} "
            f"missing_decision={row.get('missing_decision_action', 0)} "
            f"missing_shadow={row.get('non_traded_missing_shadow_outcome', 0)} "
            f"missing_forward={row.get('non_traded_missing_forward_return', 0)} "
            f"raw_pi_missing_opp={row.get('pi_decisions_missing_opportunity_id', 'n/a')} "
            f"link_required_missing_opp={row.get('pi_decisions_link_required_missing_opportunity_id', 'n/a')}"
        )


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


def _load_snapshot_rows(diagnostics_dir: Path, limit: int) -> list[dict]:
    if not diagnostics_dir.exists():
        return []
    paths = sorted(
        diagnostics_dir.glob("thesis_lineage_audit_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    rows = [_snapshot_row(path) for path in paths[: max(1, int(limit or 14))]]
    return rows


def _snapshot_row(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"file": path.name, "error": str(exc)}
    summary = payload.get("summary") or {}
    completeness = payload.get("data_completeness") or {}
    missing = completeness.get("missing_counts") or {}
    raw = payload.get("raw_table_completeness") or {}
    pi = raw.get("pi_decisions") or {}
    proposals = raw.get("trade_proposals") or {}
    shadows = raw.get("shadow_trades") or {}
    forwards = raw.get("universe_forward_outcomes") or {}
    return {
        "file": path.name,
        "generated_at": payload.get("generated_at"),
        "window_days": (payload.get("window") or {}).get("days"),
        "total_thesis_count": summary.get("total_thesis_count", 0),
        "traded_count": summary.get("traded_count", 0),
        "missed_alpha_count": summary.get("missed_alpha_count", 0),
        "failed_traded_thesis_count": summary.get("failed_traded_thesis_count", 0),
        "poor_expression_count": summary.get("poor_expression_count", 0),
        "missing_decision_action": missing.get("missing_decision_action", 0),
        "missing_decision_reasoning": missing.get("missing_decision_reasoning", 0),
        "mature_non_traded_missing_shadow_outcome": missing.get("mature_non_traded_missing_shadow_outcome", 0),
        "mature_non_traded_missing_forward_return": missing.get("mature_non_traded_missing_forward_return", 0),
        "missing_decision_meta_on_proposed_records": missing.get("missing_decision_meta_on_proposed_records", 0),
        "non_traded_missing_shadow_outcome": missing.get("non_traded_missing_shadow_outcome", 0),
        "non_traded_missing_forward_return": missing.get("non_traded_missing_forward_return", 0),
        "traded_missing_position_id": missing.get("traded_missing_position_id", 0),
        "traded_missing_actual_pnl_pct": missing.get("traded_missing_actual_pnl_pct", 0),
        "pi_decisions_missing_opportunity_id": pi.get("missing_opportunity_id"),
        "pi_decisions_link_required_missing_opportunity_id": pi.get("link_required_missing_opportunity_id"),
        "pi_decisions_proposal_backfillable_missing_opportunity_id": pi.get("proposal_backfillable_missing_opportunity_id"),
        "pi_decisions_global_no_target_count": pi.get("global_no_target_count"),
        "pi_decisions_missing_signal_source": pi.get("missing_signal_source"),
        "trade_proposals_missing_opportunity_id": proposals.get("missing_opportunity_id"),
        "trade_proposals_missing_decision_meta": proposals.get("missing_decision_meta_in_agent_rationale"),
        "shadow_trades_missing_pnl_pct": shadows.get("missing_pnl_pct"),
        "shadow_trades_stale_missing_pnl_pct": shadows.get("stale_missing_pnl_pct"),
        "forward_outcomes_missing_forward_return_pct": forwards.get("missing_forward_return_pct"),
        "forward_outcomes_overdue_pending": forwards.get("overdue_pending"),
        "schema_error_count": len(payload.get("schema_errors") or []),
    }
