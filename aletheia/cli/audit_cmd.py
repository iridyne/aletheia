"""`aletheia audit` — deterministic audit and economic-accounting commands."""

import json
from typing import Optional

import typer


audit_app = typer.Typer(no_args_is_help=True)


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
    from aletheia.storage.repositories import get_active_universe_members
    from aletheia.storage.sqlite import get_db
    from aletheia.config.loader import load_config
    from datetime import datetime, timezone, timedelta

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
        """SELECT COUNT(*),
                  SUM(CASE WHEN net_return_pct > COALESCE(min_tradable_edge_pct, 0)
                           THEN 1 ELSE 0 END),
                  AVG(net_return_pct)
           FROM universe_forward_outcomes
           WHERE status = 'settled'
             AND horizon = '24h'
             AND settled_at >= ?
             AND net_return_pct IS NOT NULL
             AND ABS(net_return_pct) <= 100""",
        (cutoff,),
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
        status = "healthy" if win_rate and win_rate >= 50 else ("degraded" if win_rate and win_rate < 40 else "neutral")
        typer.echo(f"  Settled: {total_outcomes}  |  Win rate: {win_rate}%  |  Avg return: {round(avg_ret, 4) if avg_ret else 'n/a'}%  |  Status: {status}")


@audit_app.command("pricing-set")
def pricing_set(
    provider: str = typer.Option(..., "--provider", help="Exact runtime provider name, e.g. openai_native."),
    model: str = typer.Option(..., "--model", help="Exact model identifier returned by the provider."),
    input_usd_per_million: float = typer.Option(..., "--input", min=0, help="USD per million input tokens."),
    output_usd_per_million: float = typer.Option(..., "--output", min=0, help="USD per million output tokens."),
    reasoning_usd_per_million: Optional[float] = typer.Option(None, "--reasoning", min=0, help="Optional separate reasoning-token rate."),
    cached_input_usd_per_million: Optional[float] = typer.Option(None, "--cached-input", min=0, help="Optional cached-input rate."),
    effective_from: Optional[str] = typer.Option(None, "--effective-from", help="ISO timestamp; defaults to now."),
    source: str = typer.Option("manual", "--source", help="Price source/audit note."),
    version: str = typer.Option("v1", "--version", help="Immutable price-contract version."),
):
    """Add an explicit immutable model-price snapshot; no internet price is guessed."""
    from aletheia.economics.tracing import add_pricing_snapshot

    row = add_pricing_snapshot(
        provider=provider,
        model=model,
        input_usd_per_million=input_usd_per_million,
        output_usd_per_million=output_usd_per_million,
        reasoning_usd_per_million=reasoning_usd_per_million,
        cached_input_usd_per_million=cached_input_usd_per_million,
        effective_from=effective_from,
        source=source,
        version=version,
    )
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


@audit_app.command("tool-pricing-set")
def tool_pricing_set(
    tool_name: str = typer.Option(..., "--tool", help="Exact tool name from agent_tool_calls."),
    usd_per_call: float = typer.Option(..., "--per-call", min=0, help="USD charged for one call; use 0 for explicitly free local tools."),
    effective_from: Optional[str] = typer.Option(None, "--effective-from", help="ISO timestamp; defaults to now."),
    source: str = typer.Option("manual", "--source", help="Price source/audit note."),
    version: str = typer.Option("v1", "--version", help="Immutable tool-cost contract version."),
):
    """Add an immutable per-call Tool/API cost profile."""
    from aletheia.economics.tracing import add_tool_cost_profile

    row = add_tool_cost_profile(
        tool_name=tool_name,
        usd_per_call=usd_per_call,
        effective_from=effective_from,
        source=source,
        version=version,
    )
    typer.echo(json.dumps(row, ensure_ascii=False, indent=2))


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
            f"{row['window']:<8} {float(row['usd_cap']):>10.4f} {'YES' if row['enabled'] else 'NO':>8}"
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


@audit_app.command("viability")
def viability(
    scope: str = typer.Option("system", "--scope", help="system or operator"),
    days: int = typer.Option(7, "--days", min=1, help="Lookback window in days."),
    execution_mode: str = typer.Option("paper_internal", "--execution-mode"),
    data_cost_usd: Optional[float] = typer.Option(None, "--data-cost", min=0, help="Explicit cost allocated to this window."),
    infrastructure_cost_usd: Optional[float] = typer.Option(None, "--infrastructure-cost", min=0, help="System scope only."),
    other_cost_usd: Optional[float] = typer.Option(None, "--other-cost", min=0, help="System scope only."),
    as_of: Optional[str] = typer.Option(None, "--as-of", help="Fixed ISO window end for reproducible reruns."),
    output_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON."),
):
    """Calculate deterministic System or Operator economic viability."""
    from aletheia.economics.viability import (
        build_operator_viability,
        build_system_viability,
        render_viability,
    )

    normalized = scope.strip().lower()
    if normalized == "system":
        report = build_system_viability(
            days=days,
            execution_mode=execution_mode,
            data_cost_usd=data_cost_usd,
            infrastructure_cost_usd=infrastructure_cost_usd,
            other_cost_usd=other_cost_usd,
            window_end=as_of,
        )
    elif normalized == "operator":
        report = build_operator_viability(
            days=days,
            execution_mode=execution_mode,
            data_cost_usd=data_cost_usd,
            window_end=as_of,
        )
    else:
        raise typer.BadParameter("scope must be 'system' or 'operator'")
    if output_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        return
    typer.echo(render_viability(report))
