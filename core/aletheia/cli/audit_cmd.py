"""`aletheia audit` — deterministic audit and economic-accounting commands."""

import json
from typing import Optional

import typer

audit_app = typer.Typer(no_args_is_help=True)


def _display_decimal(value: object, precision: int) -> str:
    try:
        return f"{float(str(value)):.{precision}f}"
    except (TypeError, ValueError):
        return "unavailable"


@audit_app.command("verify")
def verify():
    """Verify the integrity of the audit.jsonl hash chain."""
    from aletheia.files.jsonl import verify_audit_chain

    result = verify_audit_chain()
    typer.echo(f"Records: {result['records']}")

    if result["valid"]:
        typer.echo("Hash chain: VALID ✓")
        raise typer.Exit(0)
    else:
        typer.echo(f"Hash chain: BROKEN ({len(result['errors'])} error(s))")
        for err in result["errors"][:20]:
            typer.echo(f"  • {err}")
        if len(result["errors"]) > 20:
            typer.echo(f"  … and {len(result['errors']) - 20} more")
        raise typer.Exit(1)


@audit_app.command("data")
def data(
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Check OHLCV coverage and signal effectiveness for all active members."""
    from datetime import datetime, timedelta, timezone

    from aletheia.config.loader import load_config
    from aletheia.storage.repositories import (
        complete_outcome_economics_sql,
        net_clears_tradable_edge_sql,
        get_active_universe_members,
    )
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    cfg = load_config()
    min_bars = cfg.historical_data.min_bars_for_backtest
    db = get_db()

    symbol_coverage: list[dict] = []
    members = get_active_universe_members()
    for m in members:
        sym = m.get("symbol", "")
        if not sym:
            continue
        for tf in ("1h", "4h", "1d"):
            row = db.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts) FROM market_ohlcv WHERE symbol=? AND timeframe=?",
                (sym, tf),
            ).fetchone()
            count, oldest_ts, newest_ts = row if row else (0, None, None)
            symbol_coverage.append({
                "symbol": sym,
                "timeframe": tf,
                "bars": count,
                "ready": count >= min_bars,
                "oldest_ts": oldest_ts,
                "newest_ts": newest_ts,
            })

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    row = db.execute(
        f"""SELECT COUNT(*),
                  SUM(CASE WHEN {net_clears_tradable_edge_sql()}
                           THEN 1 ELSE 0 END),
                  AVG(net_return_pct)
           FROM filter_candidate_outcomes
           WHERE status = 'settled'
             AND horizon = '24h'
             AND settled_at >= ?
             AND cost_model_version = ?
             AND {complete_outcome_economics_sql()}""",
        (cutoff, COST_MODEL_VERSION),
    ).fetchone()
    total_outcomes, wins, avg_ret = row if row else (0, None, None)
    win_rate = round((wins or 0) / total_outcomes * 100, 1) if total_outcomes else None

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "min_bars_for_backtest": min_bars,
        "total_active_members": len(members),
        "symbol_coverage": symbol_coverage,
        "signal_effectiveness_7d": {
            "horizon": "24h",
            "cost_model_version": COST_MODEL_VERSION,
            "settled_outcomes": total_outcomes,
            "win_rate_pct": win_rate,
            "avg_net_return_pct": round(avg_ret, 4) if avg_ret is not None else None,
        },
    }

    if output_json:
        typer.echo(json.dumps(report, indent=2))
        return

    typer.echo(f"\nData Audit — {report['generated_at']}")
    typer.echo(f"Active members: {len(members)}  |  min_bars_for_backtest: {min_bars}")
    typer.echo("")

    by_symbol: dict[str, list] = {}
    for entry in symbol_coverage:
        by_symbol.setdefault(entry["symbol"], []).append(entry)

    typer.echo(f"{'Symbol':<16} {'1h bars':>9} {'4h bars':>9} {'1d bars':>9} {'Ready?':>7}")
    typer.echo("-" * 56)
    for sym, entries in by_symbol.items():
        by_tf = {e["timeframe"]: e for e in entries}
        b1h = by_tf.get("1h", {}).get("bars", 0)
        b4h = by_tf.get("4h", {}).get("bars", 0)
        b1d = by_tf.get("1d", {}).get("bars", 0)
        ready = "YES" if b1h >= min_bars else "NO"
        typer.echo(f"{sym:<16} {b1h:>9} {b4h:>9} {b1d:>9} {ready:>7}")

    typer.echo("")
    typer.echo("Signal Effectiveness (last 7d, 24h horizon):")
    if not total_outcomes:
        typer.echo("  No settled outcomes yet.")
    else:
        status = "healthy" if win_rate is not None and win_rate >= 50 else ("degraded" if win_rate is not None and win_rate < 40 else "neutral")
        typer.echo(f"  Settled: {total_outcomes}  |  Win rate: {win_rate}%  |  Avg return: {round(avg_ret, 4) if avg_ret is not None else 'n/a'}%  |  Status: {status}")


@audit_app.command("pricing-list")
def pricing_list(
    provider: Optional[str] = typer.Option(None, "--provider", help="Filter by exact provider name."),
    model: Optional[str] = typer.Option(None, "--model", help="Filter by exact model id."),
    limit: int = typer.Option(50, "--limit", min=1, max=5_000),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """List currently persisted pricing snapshots (immutable table)."""
    from aletheia.economics.tracing import list_pricing_snapshots

    rows = list_pricing_snapshots(provider=provider, model=model, limit=limit)
    if output_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("No pricing snapshots recorded.")
        return
    header = f"{'PROVIDER':<24} {'MODEL':<28} {'EFFECTIVE_FROM':<26} {'IN $/M':>10} {'OUT $/M':>10} {'SOURCE':<12}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in rows:
        typer.echo(
            f"{str(row['provider'])[:24]:<24} {str(row['model'])[:28]:<28} "
            f"{str(row['effective_from'])[:26]:<26} "
            f"{_display_decimal(row['input_usd_per_million'], 4):>10} "
            f"{_display_decimal(row['output_usd_per_million'], 4):>10} "
            f"{str(row['source'])[:12]:<12}"
        )


@audit_app.command("budget-set")
def budget_set(
    scope: str = typer.Option(..., "--scope", help="One of: system, agent_run, logical_agent, provider, provider_tier."),
    usd_cap: float = typer.Option(..., "--usd-cap", min=0.000001, help="USD ceiling for the window."),
    window: str = typer.Option("daily", "--window", help="One of: daily, weekly, session."),
    subject: Optional[str] = typer.Option(None, "--subject", help="Required unless scope=system. e.g. run_id / logical_agent name / provider name / tier."),
    disabled: bool = typer.Option(False, "--disabled", help="Create the policy but leave it disabled. Defaults to enabled."),
    source: str = typer.Option("cli:budget-set", "--source", help="Audit-trail label for who declared this policy."),
):
    """Declare a budget policy for the tokenomics harness (Phase 2 shadow mode)."""
    from aletheia.economics.budgets import add_budget

    row = add_budget(
        scope=scope,
        subject=subject,
        usd_cap=usd_cap,
        window=window,
        enabled=not disabled,
        source=source,
    )
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@audit_app.command("budget-list")
def budget_list(
    scope: Optional[str] = typer.Option(None, "--scope", help="Restrict output to one scope."),
    enabled_only: bool = typer.Option(False, "--enabled-only", help="Skip disabled policies."),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """List declared budget policies."""
    from aletheia.economics.budgets import list_budgets

    rows = list_budgets(scope=scope, enabled_only=enabled_only)
    if output_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("No budget policies declared.")
        return
    header = f"{'ID':<24} {'SCOPE':<16} {'SUBJECT':<28} {'WINDOW':<8} {'USD_CAP':>10} {'ENABLED':>8}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in rows:
        typer.echo(
            f"{row['id']:<24} {row['scope']:<16} {str(row['subject'] or '-'): <28} "
            f"{row['window']:<8} {_display_decimal(row['usd_cap'], 4):>10} {'YES' if row['enabled'] else 'NO':>8}"
        )


@audit_app.command("budget-disable")
def budget_disable(
    budget_id: str = typer.Argument(..., help="Budget policy id from `audit budget-list`."),
):
    """Disable a budget policy without deleting it (history is preserved)."""
    from aletheia.economics.budgets import set_budget_enabled

    row = set_budget_enabled(budget_id=budget_id, enabled=False)
    if row is None:
        typer.echo(f"Unknown budget policy: {budget_id}")
        raise typer.Exit(1)
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@audit_app.command("budget-enable")
def budget_enable(
    budget_id: str = typer.Argument(..., help="Budget policy id from `audit budget-list`."),
):
    """Re-enable a previously disabled budget policy."""
    from aletheia.economics.budgets import set_budget_enabled

    row = set_budget_enabled(budget_id=budget_id, enabled=True)
    if row is None:
        typer.echo(f"Unknown budget policy: {budget_id}")
        raise typer.Exit(1)
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@audit_app.command("budget-remove")
def budget_remove(
    budget_id: str = typer.Argument(..., help="Budget policy id from `audit budget-list`."),
    force: bool = typer.Option(False, "--force", help="Skip the confirmation prompt."),
):
    """Hard-delete a budget policy.

    Historic would-block observations in the runtime_cost_events ledger keep
    the referenced policy_id in place and are NOT rewritten.
    """
    from aletheia.economics.budgets import remove_budget

    if not force:
        confirm = typer.confirm(
            f"Delete budget policy {budget_id}? Historic observations will keep the reference."
        )
        if not confirm:
            typer.echo("Aborted.")
            raise typer.Exit(1)
    row = remove_budget(budget_id=budget_id)
    if row is None:
        typer.echo(f"Unknown budget policy: {budget_id}")
        raise typer.Exit(1)
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@audit_app.command("viability-list")
def viability_list(
    scope: Optional[str] = typer.Option(None, "--scope", help="Restrict to system or operator."),
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    offset: int = typer.Option(0, "--offset", min=0),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """List persisted viability reports."""
    from legacy.historical_viability import list_viability_reports

    rows = list_viability_reports(scope=scope, limit=limit, offset=offset)
    if output_json:
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        typer.echo("No viability reports persisted.")
        return
    header = f"{'ID':<28} {'SCOPE':<10} {'WINDOW_END':<26} {'VERDICT':<18} {'AMOUNT_USD':>14}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for row in rows:
        amount = row.get("viability_amount_usd")
        typer.echo(
            f"{str(row['id'])[:28]:<28} {str(row['scope'])[:10]:<10} "
            f"{str(row['window_end'])[:26]:<26} {str(row['verdict'])[:18]:<18} "
            f"{('' if amount is None else _display_decimal(amount, 6)):>14}"
        )


@audit_app.command("viability-show")
def viability_show(
    report_id: str = typer.Argument(..., help="Report id from `audit viability-list`."),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Show one persisted viability report."""
    from legacy.historical_viability import get_viability_report, render_viability

    row = get_viability_report(report_id)
    if row is None:
        typer.echo(f"Unknown viability report: {report_id}")
        raise typer.Exit(1)
    if output_json:
        typer.echo(json.dumps(row, ensure_ascii=False, indent=2))
        return
    typer.echo(render_viability(row))


@audit_app.command("costs")
def costs(
    days: int = typer.Option(7, "--days", min=1, help="Lookback window in days."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Restrict to one AgentRun."),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Inspect append-only runtime cost events and unknown-cost gaps."""
    from datetime import datetime, timedelta, timezone

    from aletheia.economics.tracing import list_cost_summary

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    report = list_cost_summary(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
    )
    if output_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo("Runtime Cost Audit")
    typer.echo(f"Window: {report['window_start']} → {report['window_end']}")
    typer.echo(f"Events: {report['event_count']}")
    typer.echo(f"Known subtotal: ${report['known_cost_subtotal_usd']:.8f}")
    typer.echo(f"Unknown events: {report['unknown_event_count']}")
    for kind, bucket in sorted(report["by_type"].items()):
        typer.echo(
            f"  {kind}: events={bucket['event_count']} "
            f"known=${bucket['known_cost_usd']:.8f} unknown={bucket['unknown_count']}"
        )


@audit_app.command("tokens")
def tokens(
    days: int = typer.Option(7, "--days", min=1, help="Lookback window in days."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Restrict to one AgentRun."),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Inspect provider-reported input/output/cache token telemetry."""
    from datetime import datetime, timedelta, timezone

    from aletheia.economics.tracing import list_token_summary

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    report = list_token_summary(
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        run_id=run_id,
    )
    if output_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    token_totals = report["tokens"]
    typer.echo("Runtime Token Audit")
    typer.echo(f"Window: {report['window_start']} → {report['window_end']}")
    typer.echo(f"LLM attempts: {report['llm_attempt_count']}")
    typer.echo(
        "Input/output coverage: "
        f"{report['input_output_available_attempt_count']}/"
        f"{report['llm_attempt_count']}"
    )
    typer.echo(f"Input tokens: {token_totals['input_tokens']}")
    typer.echo(f"Output tokens: {token_totals['output_tokens']}")
    typer.echo(f"Cached input tokens: {token_totals['cached_input_tokens']}")
    typer.echo(
        "Provider cache read/creation: "
        f"{token_totals['provider_cache_read_input_tokens']}/"
        f"{token_totals['provider_cache_creation_input_tokens']}"
    )
    observations = report["token_integrity_observation_counts"]
    summary = ", ".join(f"{rule_id}={count}" for rule_id, count in sorted(observations.items()))
    typer.echo(f"Token integrity observations: {summary or 'none'}")


@audit_app.command("context")
def context(
    days: int = typer.Option(7, "--days", min=1, help="Lookback window in days."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Restrict to one AgentRun."),
    include_artifacts: bool = typer.Option(False, "--include-artifacts"),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """List prompt-free LLM context-consumption metadata."""
    from aletheia.economics.context_artifacts import (
        list_context_artifacts,
        summarize_context_artifacts,
    )

    report = summarize_context_artifacts(days=days, run_id=run_id)
    if include_artifacts:
        report["items"] = list_context_artifacts(days=days, run_id=run_id)
    if output_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo("LLM Context Artifact Audit")
    typer.echo(f"Artifacts: {report['artifacts']}")
    for kind, bucket in sorted(report["by_kind"].items()):
        typer.echo(f"  {kind}: artifacts={bucket['artifacts']} rendered_chars={bucket['rendered_chars']}")
    freshness = ", ".join(f"{key}={value}" for key, value in sorted(report["freshness"].items()))
    typer.echo(f"Freshness: {freshness or 'none'}")


