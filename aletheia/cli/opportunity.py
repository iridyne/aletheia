"""`aletheia opportunity` - read-only opportunity lifecycle analysis."""

from __future__ import annotations

import json
from typing import Any

import typer

opportunity_app = typer.Typer(no_args_is_help=True)

_VIEWS = {"overview", "funnel", "sizing", "alpha", "thesis", "controller"}


@opportunity_app.command("audit")
def audit(
    hours: int = typer.Option(24, "--hours", help="Lookback window in hours."),
    limit: int = typer.Option(200, "--limit", help="Maximum opportunities to reconstruct."),
    stage: str | None = typer.Option(None, "--stage", help="Only show opportunities whose timeline includes this stage."),
    verdict: str | None = typer.Option(None, "--verdict", help="Only show opportunities with this counterfactual verdict."),
    symbol: str | None = typer.Option(None, "--symbol", help="Only show opportunities for this canonical symbol."),
    view: str = typer.Option("overview", "--view", help="View: overview, funnel, sizing, alpha, thesis, or controller."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Analyze the full lifecycle of opportunities and their evidence."""
    _echo_audit(
        hours=hours,
        limit=limit,
        stage=stage,
        verdict=verdict,
        symbol=symbol,
        view=view,
        json_output=json_output,
    )


@opportunity_app.command("trace")
def trace(
    opportunity_id: str = typer.Option(..., "--id", "--opportunity-id", help="Opportunity id to trace."),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Trace one opportunity from creation to final known outcome."""
    _echo_trace(opportunity_id=opportunity_id, json_output=json_output)


def _echo_audit(
    *,
    hours: int = 24,
    limit: int = 200,
    stage: str | None = None,
    verdict: str | None = None,
    symbol: str | None = None,
    view: str = "overview",
    json_output: bool = False,
    deprecated_from: str | None = None,
) -> None:
    report, rendered = _build_audit_view(
        hours=hours,
        limit=limit,
        stage=stage,
        verdict=verdict,
        symbol=symbol,
        view=view,
    )
    if json_output:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if deprecated_from:
        typer.echo(_deprecation_notice(deprecated_from, view))
    typer.echo(rendered)


def _echo_trace(
    *,
    opportunity_id: str,
    json_output: bool = False,
    deprecated_from: str | None = None,
) -> None:
    from aletheia.analytics.opportunity_flow import render_flow_trace, trace_opportunity_flow

    record = trace_opportunity_flow(opportunity_id)
    if json_output:
        typer.echo(json.dumps(record, ensure_ascii=False, indent=2))
        return
    if deprecated_from:
        typer.echo("Deprecated: use `uv run aletheia opportunity trace --id <opportunity_id>` instead.\n")
    typer.echo(render_flow_trace(record))


def _build_audit_view(
    *,
    hours: int,
    limit: int,
    stage: str | None,
    verdict: str | None,
    symbol: str | None,
    view: str,
) -> tuple[dict[str, Any], str]:
    clean_view = str(view or "overview").strip().lower()
    if clean_view not in _VIEWS:
        raise typer.BadParameter(f"view must be one of: {', '.join(sorted(_VIEWS))}")
    if clean_view == "overview":
        from aletheia.analytics.opportunity_flow import build_opportunity_flow_ledger, render_flow_audit

        report = build_opportunity_flow_ledger(
            hours=hours,
            limit=limit,
            stage=stage,
            verdict=verdict,
            symbol=symbol,
        )
        return report, render_flow_audit(report)
    if clean_view == "funnel":
        from aletheia.analytics.trade_funnel import build_trade_funnel_audit, render_trade_funnel_audit

        report = build_trade_funnel_audit(hours=hours, include_ranker_snapshot=False)
        return report, render_trade_funnel_audit(report)
    if clean_view == "sizing":
        from aletheia.analytics.sizing_trace import build_sizing_trace_audit, render_sizing_trace_audit

        report = build_sizing_trace_audit(hours=hours)
        return report, render_sizing_trace_audit(report)
    if clean_view == "controller":
        from aletheia.analytics.filter_controller import (
            build_filter_controller_report,
            render_filter_controller_report,
        )

        report = build_filter_controller_report(hours=hours)
        return report, render_filter_controller_report(report)
    days = max(1, (max(1, int(hours or 24)) + 23) // 24)
    if clean_view == "alpha":
        from aletheia.analytics.alpha_attribution import build_alpha_attribution_audit, render_alpha_audit

        report = build_alpha_attribution_audit(days=days)
        return report, render_alpha_audit(report)
    from aletheia.analytics.thesis_lineage import build_thesis_lineage_audit, render_thesis_lineage_audit

    report = build_thesis_lineage_audit(days=days)
    return report, render_thesis_lineage_audit(report)


def _deprecation_notice(command_name: str, view: str) -> str:
    suffix = "" if view == "overview" else f" --view {view}"
    return (
        f"Deprecated: use `uv run aletheia opportunity audit{suffix}` instead.\n"
        f"This compatibility command remains available as `{command_name}` for now.\n"
    )
