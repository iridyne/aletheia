"""Read-only runtime state inspection CLI: `aletheia inspect ...`

All subcommands are safe to run at any time -- they never write to the DB,
modify config, or trigger side effects. Designed for Agent / developer use.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer

inspect_app = typer.Typer(no_args_is_help=True)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _output(data, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return
    if isinstance(data, list):
        if not data:
            typer.echo("(no results)")
            return
        for row in data:
            parts = [f"{k}={v}" for k, v in row.items() if v is not None]
            typer.echo("  ".join(parts))
    elif isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, dict):
                typer.echo(f"[{k}]")
                for sk, sv in v.items():
                    typer.echo(f"  {sk}: {sv}")
            else:
                typer.echo(f"{k}: {v}")


def _with_flow_hint(text: str, hours: int) -> str:
    return (
        f"{text}\n\n"
        "Also summarized in the opportunity ledger:\n"
        f"  uv run aletheia flow audit --hours {max(1, int(hours or 24))}"
    )


def _parse_age(snapshot_at: str) -> tuple:
    """Return (age_seconds, is_stale) for a snapshot_at ISO string."""
    try:
        ts = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age, age > 1800
    except Exception:
        return -1.0, True


# ---------------------------------------------------------------------------
# 1. positions
# ---------------------------------------------------------------------------

@inspect_app.command("positions")
def positions(
    status: str = typer.Option("open", help="Filter: open | closed | all"),
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows to return"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """List positions with PnL."""
    from aletheia.storage.repositories import list_positions
    filter_status = None if status == "all" else status
    rows = list_positions(status=filter_status, limit=limit)
    _output(rows, json_output)


@inspect_app.command("position-path")
def position_path(
    position_id: str = typer.Option(..., "--id", "--position-id"),
    limit: int = typer.Option(10_000, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Inspect append-only five-minute observations for one Position."""

    from aletheia.analytics.position_path_observations import (
        build_position_path_report,
    )

    report = build_position_path_report(position_id, limit=limit)
    _output(report, json_output)


@inspect_app.command("position-replay")
def position_replay(
    position_id: str = typer.Option(..., "--id", "--position-id"),
    as_of_at: Optional[str] = typer.Option(None, "--as-of"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Replay exact immutable facts for one Position without writes."""

    from aletheia.analytics.position_replay import build_position_replay

    report = build_position_replay(position_id, as_of_at=as_of_at)
    _output(report, json_output)


@inspect_app.command("candidate-facts")
def candidate_facts(
    horizon: str = typer.Option("24h", "--horizon"),
    as_of_at: Optional[str] = typer.Option(None, "--as-of"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Inspect the complete candidate funnel at one fixed horizon."""

    from aletheia.analytics.candidate_facts import build_candidate_facts

    report = build_candidate_facts(horizon=horizon, as_of_at=as_of_at)
    _output(report, json_output)


@inspect_app.command("position-diagnosis")
def position_diagnosis(
    position_id: str = typer.Option(..., "--id", "--position-id"),
    as_of_at: Optional[str] = typer.Option(None, "--as-of"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Derive read-only diagnostics from one exact Position replay."""

    from aletheia.analytics.position_replay import build_position_replay
    from aletheia.analytics.profit_loop_diagnosis import diagnose_position_fact

    fact = build_position_replay(position_id, as_of_at=as_of_at)
    _output(diagnose_position_fact(fact), json_output)


@inspect_app.command("candidate-diagnosis")
def candidate_diagnosis(
    horizon: str = typer.Option("24h", "--horizon"),
    as_of_at: Optional[str] = typer.Option(None, "--as-of"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Derive candidate-denominator diagnostics at one fixed horizon."""

    from aletheia.analytics.candidate_facts import build_candidate_facts
    from aletheia.analytics.profit_loop_diagnosis import diagnose_candidate_report

    facts = build_candidate_facts(horizon=horizon, as_of_at=as_of_at)
    _output(diagnose_candidate_report(facts), json_output)


@inspect_app.command("profit-counterfactual")
def profit_counterfactual(
    position_id: str = typer.Option(..., "--id", "--position-id"),
    cost_model_file: Path = typer.Option(
        ..., "--cost-model", exists=True, file_okay=True, dir_okay=False
    ),
    as_of_at: Optional[str] = typer.Option(None, "--as-of"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Evaluate the fixed exploratory registry with an explicit cost contract."""

    from aletheia.analytics.position_replay import build_position_replay
    from aletheia.analytics.profit_loop_counterfactual import (
        CostModel,
        evaluate_counterfactual_registry,
        profit_loop_registration_cutoffs,
        resolve_profit_loop_registration,
    )

    payload = json.loads(cost_model_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.BadParameter("cost model must be a JSON object")
    cost_model = CostModel(**payload)
    if as_of_at is not None:
        fact = build_position_replay(position_id, as_of_at=as_of_at)
    else:
        fact = None
        first_failure = None
        for cutoff in profit_loop_registration_cutoffs():
            candidate = build_position_replay(position_id, as_of_at=cutoff)
            if first_failure is None:
                first_failure = candidate
            registration = resolve_profit_loop_registration(candidate) or {}
            if (
                registration.get("status") == "available"
                and registration.get("cutoff_at") == candidate.get("as_of_at")
            ):
                fact = candidate
                break
        fact = fact or first_failure or {
            "fact_id": position_id,
            "producer": {"status": "unavailable"},
        }
    _output(
        evaluate_counterfactual_registry(
            fact,
            cost_model=cost_model,
            as_of_at=as_of_at,
        ),
        json_output,
    )


@inspect_app.command("profit-gradient")
def profit_gradient(
    evidence_file: Path = typer.Option(
        ..., "--evidence", exists=True, file_okay=True, dir_okay=False
    ),
    json_output: bool = typer.Option(False, "--json"),
):
    """Apply seven-layer unique-winner gates to a frozen evidence artifact."""

    from aletheia.analytics.profit_gradient import select_unique_max_direction

    payload = json.loads(evidence_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise typer.BadParameter("evidence must be a JSON object")
    layer_samples = payload.get("layer_samples")
    evidence_context = payload.get("evidence_context")
    if not isinstance(layer_samples, dict) or not isinstance(evidence_context, dict):
        raise typer.BadParameter(
            "evidence requires layer_samples and evidence_context objects"
        )
    _output(
        select_unique_max_direction(
            layer_samples,
            evidence_context=evidence_context,
        ),
        json_output,
    )


@inspect_app.command("stop-loss-path")
def stop_loss_path(
    hours: int = typer.Option(168, "--hours", help="Closed-position lookback"),
    limit: int = typer.Option(200, "--limit", "-n", help="Max stop-loss rows"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Audit fail-closed stop-loss path attribution for a recent cohort."""

    from aletheia.analytics.position_path_observations import (
        build_stop_loss_path_audit,
    )

    report = build_stop_loss_path_audit(hours=hours, limit=limit)
    _output(report, json_output)


# ---------------------------------------------------------------------------
# 2. proposals
# ---------------------------------------------------------------------------

@inspect_app.command("proposals")
def proposals(
    status: str = typer.Option("all", help="Filter: pending | approved | rejected | executed | all"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List trade proposals with approval status."""
    from aletheia.storage.repositories import list_trade_proposals
    rows = list_trade_proposals(limit=limit)
    if status != "all":
        rows = [r for r in rows if r.get("status") == status]
    display_keys = ["id", "symbol", "direction", "execution_mode", "status",
                    "agent_rationale", "strategy_id", "opportunity_id", "created_at"]
    rows = [{k: r.get(k) for k in display_keys} for r in rows]
    _output(rows, json_output)


# ---------------------------------------------------------------------------
# 3. risk
# ---------------------------------------------------------------------------

@inspect_app.command("risk")
def risk(
    rejected_only: bool = typer.Option(False, "--rejected-only", help="Show only rejected decisions"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List risk decisions (enriched with proposal symbol/direction)."""
    from aletheia.storage.repositories import list_risk_decisions, get_trade_proposal
    rows = list_risk_decisions(limit=limit)
    if rejected_only:
        rows = [r for r in rows if not r.get("approved")]
    result = []
    for r in rows:
        violations = r.get("violations") or "[]"
        try:
            violations = json.loads(violations)
        except Exception:
            pass
        entry = {
            "id": r.get("id"),
            "proposal_id": r.get("proposal_id"),
            "approved": r.get("approved"),
            "risk_score": r.get("risk_score"),
            "violations": violations,
            "message": r.get("message"),
            "created_at": r.get("created_at"),
        }
        proposal = get_trade_proposal(r["proposal_id"]) if r.get("proposal_id") else None
        if proposal:
            entry["symbol"] = proposal.get("symbol")
            entry["direction"] = proposal.get("direction")
        result.append(entry)
    _output(result, json_output)


# ---------------------------------------------------------------------------
# 4. orders
# ---------------------------------------------------------------------------

@inspect_app.command("orders")
def orders(
    status: str = typer.Option("all", help="Filter: pending | filled | cancelled | failed | all"),
    stats: bool = typer.Option(False, "--stats", help="Show aggregate fill rate + error breakdown"),
    days: int = typer.Option(7, "--days", "-d", help="Lookback window for --stats mode"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List orders with execution details. Use --stats for aggregated view."""
    if stats:
        from aletheia.analytics.trade_stats import compute_order_stats
        result = compute_order_stats(days=days)
        if json_output:
            typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
            return
        typer.echo(f"=== {days}-Day Order Stats ===")
        typer.echo(f"  total: {result['total']}  filled: {result['filled']}  errors: {result['errors']}  fill_rate: {result['fill_rate_pct']}%")
        if result["error_breakdown"]:
            typer.echo("")
            typer.echo("=== Error Breakdown ===")
            for e in result["error_breakdown"]:
                typer.echo(f"  \"{e['pattern']}\": x{e['count']} ({', '.join(e['symbols'][:5])})")
        return

    from aletheia.storage.repositories import list_orders
    rows = list_orders(limit=limit)
    if status != "all":
        rows = [r for r in rows if r.get("status") == status]
    display_keys = ["id", "symbol", "direction", "order_type", "quantity", "price",
                    "execution_mode", "status", "exchange_order_id", "filled_at", "error_reason", "created_at"]
    rows = [{k: r.get(k) for k in display_keys} for r in rows]
    _output(rows, json_output)


# ---------------------------------------------------------------------------
# 5. universe
# ---------------------------------------------------------------------------

@inspect_app.command("universe")
def universe(
    limit: int = typer.Option(30, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List active universe members by rank."""
    from aletheia.storage.repositories import get_active_universe_members
    rows = get_active_universe_members(limit=limit)
    display_keys = ["symbol", "rank", "total_score", "opportunity_score", "tradability_score",
                    "scanner_family", "execution_eligible", "expires_at", "risk_flags",
                    "late_entry_penalty", "crowding_risk"]
    result = []
    for r in rows:
        entry = {k: r.get(k) for k in display_keys}
        rf = entry.get("risk_flags")
        if isinstance(rf, str):
            try:
                entry["risk_flags"] = json.loads(rf)
            except Exception:
                pass
        result.append(entry)
    _output(result, json_output)


# ---------------------------------------------------------------------------
# 6. opportunities
# ---------------------------------------------------------------------------

@inspect_app.command("opportunities")
def opportunities(
    status: str = typer.Option("open", help="Filter: open | proposed | expired | all"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List opportunities with confidence and eligibility."""
    from aletheia.storage.repositories import list_opportunities
    rows = list_opportunities(limit=limit)
    if status != "all":
        rows = [r for r in rows if r.get("status") == status]
    display_keys = ["id", "symbol", "direction_bias", "confidence", "confidence_score",
                    "scanner_family", "status", "execution_eligible", "source_agent",
                    "valid_until", "created_at"]
    rows = [{k: r.get(k) for k in display_keys} for r in rows]
    _output(rows, json_output)


# ---------------------------------------------------------------------------
# 7. strategies
# ---------------------------------------------------------------------------

@inspect_app.command("strategies")
def strategies(
    state: str = typer.Option("all", help="Filter: active | paper_running | draft | retired | all"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List strategy records with key parameters."""
    from aletheia.storage.repositories import list_strategies
    rows = list_strategies(limit=limit)
    if state != "all":
        rows = [r for r in rows if r.get("state") == state]
    result = []
    for r in rows:
        params = {}
        try:
            params = json.loads(r.get("parameters") or "{}")
        except Exception:
            pass
        result.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "state": r.get("state"),
            "direction_affinity": r.get("direction_affinity"),
            "applicable_regimes": r.get("applicable_regimes"),
            "strategy_type": r.get("strategy_type"),
            "paper_trading_paused": r.get("paper_trading_paused"),
            "under_review": r.get("under_review"),
            "symbol": params.get("symbol"),
            "has_ruleset": bool(params.get("ruleset")),
            "created_at": r.get("created_at"),
        })
    _output(result, json_output)


# ---------------------------------------------------------------------------
# 8. snapshots
# ---------------------------------------------------------------------------

@inspect_app.command("snapshots")
def snapshots(
    symbol: Optional[str] = typer.Option(None, "--symbol", help="Filter by symbol (e.g. BTC/USDT)"),
    stale_only: bool = typer.Option(False, "--stale-only", help="Show only stale snapshots (age > 30min)"),
    limit: int = typer.Option(50, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show market snapshot coverage and freshness per symbol."""
    from aletheia.storage.repositories import list_market_snapshots

    if symbol:
        rows = list_market_snapshots(symbol=symbol, limit=limit)
        result = []
        for r in rows:
            age, is_stale = _parse_age(r.get("snapshot_at") or "")
            result.append({
                "symbol": r.get("symbol"),
                "price": r.get("price"),
                "volume_24h": r.get("volume_24h"),
                "change_pct_24h": r.get("change_pct_24h"),
                "exchange": r.get("exchange"),
                "snapshot_at": r.get("snapshot_at"),
                "age_seconds": round(age, 0),
                "is_stale": is_stale,
            })
        _output(result, json_output)
        return

    # Coverage mode: latest snapshot per symbol
    rows = list_market_snapshots(limit=500)
    latest: dict = {}
    for r in rows:
        sym = r.get("symbol") or ""
        if sym not in latest:
            latest[sym] = r

    result = []
    for sym, r in sorted(latest.items()):
        age, is_stale = _parse_age(r.get("snapshot_at") or "")
        if stale_only and not is_stale:
            continue
        result.append({
            "symbol": sym,
            "price": r.get("price"),
            "exchange": r.get("exchange"),
            "snapshot_at": r.get("snapshot_at"),
            "age_seconds": round(age, 0),
            "is_stale": is_stale,
        })

    result.sort(key=lambda x: x["age_seconds"], reverse=True)
    _output(result[:limit], json_output)


# ---------------------------------------------------------------------------
# 9. candidates
# ---------------------------------------------------------------------------

@inspect_app.command("candidates")
def candidates(
    eligible_only: bool = typer.Option(False, "--eligible-only", help="Show only tradeable candidates"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Recompute a Ranker preview (not historical evidence)."""
    from aletheia.agents.paper_trader_ranker import build_paper_candidates
    all_candidates = build_paper_candidates(limit=50)
    display_keys = [
        "symbol", "direction", "trade_score", "skip_reason", "confidence_score",
        "scanner_family", "strategy_id", "universe_rank", "factor_status",
        "evidence_status", "guard_blocked", "guard_reasons",
    ]
    result = []
    for c in all_candidates:
        if eligible_only and c.get("skip_reason") is not None:
            continue
        entry = {k: c.get(k) for k in display_keys}
        result.append(entry)
    _output(result[:limit], json_output)


@inspect_app.command("entry-status")
def entry_status(
    hours: int = typer.Option(1, "--hours", "-h", help="Recent Opportunity window"),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON"),
):
    """Show the exact current entry stage from durable lifecycle/filter facts."""
    from aletheia.server.routes.analytics import get_opportunity_funnel

    current = get_opportunity_funnel(hours=max(1, int(hours))).get("current") or {}
    if json_output:
        _output(current, True)
        return

    typer.echo(f"state: {current.get('state') or 'unknown'}")
    typer.echo(f"stage: {current.get('primary_stage') or 'unknown'}")
    typer.echo(f"headline: {current.get('headline') or 'not available'}")
    typer.echo(
        "limiting reason: "
        f"{current.get('reason_code') or 'none'} — "
        f"{current.get('reason') or 'not available'}"
    )
    typer.echo(f"next trigger: {current.get('next_trigger') or 'not available'}")

    counts = current.get("counts") or {}
    typer.echo(
        "last window: "
        f"setups={counts.get('unique_setups', 'not available')} "
        f"generations={counts.get('discovered_generations', 'not available')} "
        f"superseded={counts.get('superseded_generations', 'not available')} "
        f"ttl_expired={counts.get('ttl_expired', 'not available')}"
    )

    ranker = current.get("ranker") or {}
    last_run = ranker.get("last_run") or {}
    wake = ranker.get("wake") or {}
    typer.echo(
        "ranker: "
        f"last={last_run.get('status') or 'not available'} "
        f"trigger={last_run.get('trigger_type') or 'not available'} "
        f"wake_pending={bool(wake.get('pending'))}"
    )
    if wake.get("pending_sources"):
        typer.echo(f"wake sources: {', '.join(wake['pending_sources'])}")

    candidates = current.get("candidates") or []
    if not candidates:
        typer.echo("candidates: none")
        return
    typer.echo("candidates:")
    for candidate in candidates:
        retry_in = candidate.get("retry_in_sec")
        retry_text = f" retry_in={retry_in}s" if retry_in is not None else ""
        passed = candidate.get("passed_filter_count")
        passed_text = f" passed={passed}" if passed is not None else ""
        typer.echo(
            f"  {candidate.get('symbol')} {candidate.get('direction')} "
            f"stage={candidate.get('stage')} status={candidate.get('status')} "
            f"blocker={candidate.get('blocking_filter') or candidate.get('reason_code')}"
            f"{passed_text}{retry_text}"
        )


# ---------------------------------------------------------------------------
# 10. config
# ---------------------------------------------------------------------------

@inspect_app.command("config")
def config(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show runtime config summary (safe fields only -- no secrets)."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    summary = {
        "runtime": {
            "kill_switch_active": cfg.runtime.kill_switch_active,
            "paused": cfg.runtime.paused,
            "scheduler_enabled": cfg.runtime.scheduler_enabled,
        },
        "agent": {
            # #180 fix: the "agent" block was printing cfg.operator.permission_mode
            # instead of the trading-agent's cfg.agent.permission_mode — the
            # operator namespace is a separate permission surface for the human
            # System Operator LLM, so this misled every `aletheia inspect config`
            # reader about which mode was gating trading. Print both explicitly
            # so no future reader has to remember the namespace boundary.
            "permission_mode": cfg.agent.permission_mode,
            "autonomous_learning": cfg.agent.autonomous_learning,
            "bypass_distress_gate": cfg.agent.bypass_distress_gate,
        },
        "operator": {
            "permission_mode": cfg.operator.permission_mode,
        },
        "exchange": {
            "mode": cfg.exchange.mode,
            "paper_execution_mode": cfg.exchange.paper_execution_mode,
        },
        "risk": {
            "demo": cfg.risk.demo.model_dump(mode="json"),
            "live": cfg.risk.live.model_dump(mode="json"),
        },
        "trading": {
            "min_confidence_to_trade": cfg.trading.min_confidence_to_trade,
            "default_stop_loss_pct": cfg.trading.default_stop_loss_pct,
            "default_take_profit_pct": cfg.trading.default_take_profit_pct,
        },
        "universe": {
            "enabled": cfg.universe.enabled,
            "max_candidates": cfg.universe.max_candidates,
        },
        "guard": {
            "enabled": cfg.guard.enabled,
            "enforce_in_paper_ranker": cfg.guard.enforce_in_paper_ranker,
        },
    }
    _output(summary, json_output)


# ---------------------------------------------------------------------------
# 11. guards
# ---------------------------------------------------------------------------

@inspect_app.command("guards")
def guards(
    scope: Optional[str] = typer.Option(None, "--scope", help="Filter: paper_entry | opportunity | all"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List active trading guards."""
    from aletheia.knowledge.guards import load_trading_guards
    filter_scope = None if (not scope or scope == "all") else scope
    guard_list = load_trading_guards(scope=filter_scope, status="active")
    result = []
    for g in guard_list:
        result.append({
            "guard_id": g.guard_id,
            "title": g.title,
            "scope": g.scope.value if hasattr(g.scope, "value") else str(g.scope),
            "enforcement": g.enforcement.value if hasattr(g.enforcement, "value") else str(g.enforcement),
            "severity": g.severity.value if hasattr(g.severity, "value") else str(g.severity),
            "score_penalty": g.score_penalty,
            "block_candidate": g.block_candidate,
            "tags": list(g.tags) if g.tags else [],
        })
    _output(result, json_output)


# ---------------------------------------------------------------------------
# 11b. guard-impact
# ---------------------------------------------------------------------------

@inspect_app.command("guard-impact")
def guard_impact(
    hours: int = typer.Option(24, "--hours", "-h", help="Lookback window for recent open opportunities"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Read-only audit of active blocking guard impact on recent candidates."""
    from aletheia.analytics.guard_impact import build_guard_impact_audit, render_guard_impact_audit

    report = build_guard_impact_audit(hours=hours)
    if json_output:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return
    typer.echo(_with_flow_hint(render_guard_impact_audit(report), hours))


# ---------------------------------------------------------------------------
# 11c. debate-impact
# ---------------------------------------------------------------------------

@inspect_app.command("debate-impact")
def debate_impact(
    hours: int = typer.Option(24, "--hours", "-h", help="Lookback window for System Operator decisions"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Read-only audit of debated skips versus existing forward outcomes."""
    from aletheia.analytics.debate_impact import build_debate_impact_audit, render_debate_impact_audit

    report = build_debate_impact_audit(hours=hours)
    if json_output:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False, default=str))
        return
    typer.echo(_with_flow_hint(render_debate_impact_audit(report), hours))


# ---------------------------------------------------------------------------
# 12. scorecard
# ---------------------------------------------------------------------------

@inspect_app.command("scorecard")
def scorecard(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show trading performance by scanner family."""
    from aletheia.analytics.family_scorecard import compute_family_scorecard
    rows = compute_family_scorecard()
    _output(rows, json_output)


# ---------------------------------------------------------------------------
# 13. status (aggregated dashboard)
# ---------------------------------------------------------------------------

@inspect_app.command("status")
def status(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window for PnL/orders"),
    json_output: bool = typer.Option(False, "--json"),
):
    """One-shot system dashboard: portfolio, PnL, orders, universe, data quality."""
    from aletheia.analytics.outcome_integrity import build_outcome_cohort_summary
    from aletheia.analytics.trade_stats import compute_rolling_trade_stats, compute_order_stats
    from aletheia.data.price_feed import cache_stats
    from aletheia.portfolio.pnl import mark_positions_to_market
    from aletheia.maintenance.historical_accounting import historical_accounting_summary
    from aletheia.storage.repositories import (
        get_active_universe_snapshot, get_active_universe_members,
    )

    # Portfolio
    positions = mark_positions_to_market(persist=False)
    open_pos = [p for p in positions if p.get("status") == "open"]

    portfolio_section = {
        "open_positions": len(open_pos),
        "positions": [
            {
                "symbol": p["symbol"],
                "direction": p["direction"],
                "quantity": p["quantity"],
                "entry_price": p["entry_price"],
                "unrealized_pnl": round(p.get("unrealized_pnl", 0), 4),
            }
            for p in open_pos[:5]
        ],
    }

    # Exchange equity (best-effort)
    try:
        from aletheia.portfolio.exchange_sync import get_cached_state, status_for
        cached = get_cached_state()
        if cached and status_for(cached) in ("ok", "stale"):
            # get_cached_state may return the unified (multi-broker) cache, which
            # uses total_equity_usdt instead of the Bitget-specific equity_usdt —
            # fall back so non-Bitget paper accounts show real equity, not None.
            portfolio_section["equity_usdt"] = cached.get("equity_usdt") or cached.get("total_equity_usdt")
            portfolio_section["exchange_age_sec"] = cached.get("age_seconds")
    except Exception:
        pass

    # PnL
    today_start = datetime.now(timezone.utc).date().isoformat()
    today_cohort = build_outcome_cohort_summary(closed_after=today_start)
    today_pnl = today_cohort["eligible_realized_pnl"]
    rolling = compute_rolling_trade_stats(days=days)
    pnl_section = {
        "today_realized": round(today_pnl, 2) if today_pnl is not None else None,
        "cohort": build_outcome_cohort_summary(
            closed_after=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        ),
        "historical_accounting": historical_accounting_summary(),
        **{k: rolling[k] for k in (
            "days", "total_trades", "wins", "losses", "win_rate",
            "total_pnl", "avg_pnl", "best_win", "worst_loss",
        )},
    }

    # Orders
    order_section = compute_order_stats(days=days)

    # Universe
    snap = get_active_universe_snapshot()
    universe_section: dict = {"snapshot": None}
    if snap:
        age_sec = (datetime.now(timezone.utc) - datetime.fromisoformat(
            snap["created_at"].replace("Z", "+00:00")
        )).total_seconds()
        members = get_active_universe_members(limit=5)
        universe_section = {
            "snapshot_id": snap["id"][:12],
            "created_at": snap["created_at"],
            "age_hours": round(age_sec / 3600, 1),
            "stale": age_sec > 86400,
            "top_members": [f"{m['symbol']}({m.get('total_score', 0):.0f})" for m in members],
        }

    # Data quality
    data_section = cache_stats()

    result = {
        "portfolio": portfolio_section,
        "pnl": pnl_section,
        "orders": order_section,
        "universe": universe_section,
        "data_quality": data_section,
    }

    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    # Human-readable output
    typer.echo("=== Portfolio ===")
    eq = portfolio_section.get("equity_usdt")
    typer.echo(f"  equity: {eq or '?'} USDT  |  open_positions: {portfolio_section['open_positions']}")
    for p in portfolio_section["positions"]:
        typer.echo(f"  {p['symbol']} {p['direction']} qty={p['quantity']} entry={p['entry_price']} uPnL={p['unrealized_pnl']}")

    typer.echo("")
    typer.echo(f"=== PnL ({days}d) ===")
    cohort = pnl_section["cohort"]
    typer.echo(
        "  raw closes: "
        f"{cohort['raw_event_count']}  verified: {cohort['eligible_outcome_count']}  "
        f"excluded: {cohort['excluded_outcome_count']}"
    )
    if rolling["total_pnl"] is None:
        typer.echo("  insufficient verified outcomes")
    else:
        typer.echo(f"  today: {pnl_section['today_realized']} USDT  |  {days}d: {rolling['total_pnl']} USDT ({rolling['total_trades']} trades, {rolling['win_rate']}% win)")
        typer.echo(f"  best: {rolling['best_win']}  worst: {rolling['worst_loss']}  avg: {rolling['avg_pnl']}")

    typer.echo("")
    typer.echo(f"=== Orders ({days}d) ===")
    typer.echo(f"  filled: {order_section['filled']}  errors: {order_section['errors']}  fill_rate: {order_section['fill_rate_pct']}%")
    for e in order_section["error_breakdown"][:3]:
        typer.echo(f"  {e['pattern']}: x{e['count']} ({', '.join(e['symbols'][:3])})")

    typer.echo("")
    typer.echo("=== Universe ===")
    if universe_section.get("snapshot_id"):
        stale_flag = " (STALE!)" if universe_section["stale"] else ""
        typer.echo(f"  age: {universe_section['age_hours']}h{stale_flag}  snapshot: {universe_section['snapshot_id']}")
        typer.echo(f"  top: {' '.join(universe_section['top_members'])}")
    else:
        typer.echo("  (no active snapshot)")

    typer.echo("")
    typer.echo("=== Data Quality ===")
    oldest = data_section.get("oldest_sec")
    oldest_str = f"{oldest}s" if oldest is not None else "empty"
    typer.echo(f"  price_feed: {data_section.get('entries', 0)} symbols cached, oldest {oldest_str}")


# ---------------------------------------------------------------------------
# 14. pnl (detailed PnL analysis)
# ---------------------------------------------------------------------------

@inspect_app.command("pnl")
def pnl(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Detailed PnL breakdown: today, rolling window, per-symbol, open positions."""
    from aletheia.analytics.outcome_integrity import (
        build_outcome_cohort_summary,
        eligible_strategy_outcomes,
    )
    from aletheia.analytics.trade_stats import compute_rolling_trade_stats
    from aletheia.portfolio.pnl import mark_positions_to_market
    from aletheia.maintenance.historical_accounting import historical_accounting_summary

    # Today detail
    today_str = datetime.now(timezone.utc).date().isoformat()
    closed = eligible_strategy_outcomes(closed_after=today_str, limit=200)
    today_closed = [
        p for p in closed
        if (p.get("closed_at") or "")[:10] == today_str
    ]
    today_cohort = build_outcome_cohort_summary(closed_after=today_str)
    today_total = today_cohort["eligible_realized_pnl"]
    today_detail = [
        {
            "symbol": p["symbol"], "direction": p["direction"],
            "pnl": round(float(p["realized_pnl"]), 2),
            "close_reason": p.get("close_reason", "?"),
        }
        for p in today_closed
    ]

    # Rolling stats
    rolling = compute_rolling_trade_stats(days=days)

    # Open positions
    open_pos = mark_positions_to_market(persist=False)
    positions_out = [
        {
            "symbol": p["symbol"], "direction": p["direction"],
            "entry_price": p["entry_price"],
            "unrealized_pnl": round(p.get("unrealized_pnl", 0), 4),
            "hold_hours": _hold_hours_since(p.get("opened_at")),
        }
        for p in open_pos
    ]

    result = {
        "today": {"date": today_str, "realized": round(today_total, 2) if today_total is not None else None, "trades": today_detail},
        "rolling": rolling,
        "cohort": build_outcome_cohort_summary(
            closed_after=(datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        ),
        "historical_accounting": historical_accounting_summary(),
        "open_positions": positions_out,
    }

    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    # Human-readable
    typer.echo(f"=== Today ({today_str} UTC) ===")
    if today_total is None:
        typer.echo("  realized: unavailable (insufficient verified outcomes)")
    else:
        typer.echo(f"  realized: {today_total:.2f} USDT ({len(today_detail)} verified outcomes)")
    for t in today_detail:
        typer.echo(f"  {t['symbol']} {t['direction']}: {t['pnl']:+.2f} ({t['close_reason']})")

    typer.echo("")
    typer.echo(f"=== {days}-Day Rolling ===")
    cohort = result["cohort"]
    typer.echo(
        "  raw closes: "
        f"{cohort['raw_event_count']}  verified: {cohort['eligible_outcome_count']}  "
        f"excluded: {cohort['excluded_outcome_count']}"
    )
    if cohort["outcome_exclusion_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in cohort["outcome_exclusion_reasons"].items()
        )
        typer.echo(f"  exclusions: {reasons}")
    if rolling["total_pnl"] is None:
        typer.echo("  insufficient verified outcomes")
    else:
        typer.echo(f"  trades: {rolling['total_trades']}  wins: {rolling['wins']}  losses: {rolling['losses']}  win_rate: {rolling['win_rate']}%")
        typer.echo(f"  total: {rolling['total_pnl']:+.2f}  avg: {rolling['avg_pnl']:+.2f}  best: {rolling['best_win']:+.2f}  worst: {rolling['worst_loss']:+.2f}")

    historical = result["historical_accounting"]
    typer.echo("")
    typer.echo("=== Historical local-paper reconciliation (separate from verified PnL) ===")
    if historical["historical_reconciled_pnl"] is None:
        typer.echo(f"  reconstructed: none  unavailable: {historical['historical_reconciliation_unavailable_count']}")
    else:
        typer.echo(f"  reconstructed: {historical['historical_reconciled_pnl']:+.2f} ({historical['historical_reconciled_count']} closes)  unavailable: {historical['historical_reconciliation_unavailable_count']}")

    if rolling["per_symbol"]:
        typer.echo("")
        typer.echo(f"=== Per-Symbol ({days}d) ===")
        for s in rolling["per_symbol"]:
            typer.echo(f"  {s['symbol']:12s} {s['trades']} trades  {s['wins']}W/{s['trades']-s['wins']}L  pnl={s['pnl']:+.2f}")

    if positions_out:
        typer.echo("")
        typer.echo("=== Open Positions ===")
        for p in positions_out:
            typer.echo(f"  {p['symbol']} {p['direction']}  entry={p['entry_price']}  uPnL={p['unrealized_pnl']}  hold={p['hold_hours']}h")


def _hold_hours_since(iso_str: str | None) -> float | None:
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None


# ===========================================================================
# HIGH PRIORITY — regime, fills, liquidations, calibration
# ===========================================================================


@inspect_app.command("regime")
def regime(
    history: int = typer.Option(1, "--history", "-n", help="Number of recent regime records"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show current market regime (state, confidence, bias, signals)."""
    from aletheia.storage.repositories import list_market_regimes
    rows = list_market_regimes(limit=history)
    if not rows:
        typer.echo("(no regime data)")
        return
    result = []
    for r in rows:
        signals = r.get("signals") or "{}"
        if isinstance(signals, str):
            try:
                signals = json.loads(signals)
            except Exception:
                pass
        result.append({
            "state": r.get("state"),
            "confidence": r.get("confidence"),
            "long_bias": r.get("long_bias"),
            "short_bias": r.get("short_bias"),
            "detected_at": r.get("detected_at"),
            "signals": signals,
        })
    if json_output:
        _output(result, True)
        return
    for rec in result:
        typer.echo(f"  {rec['state'].upper()} conf={rec['confidence']:.0%} long={rec['long_bias']:.0%} short={rec['short_bias']:.0%}  @{rec['detected_at']}")
        if isinstance(rec["signals"], dict) and rec["signals"]:
            for k, v in rec["signals"].items():
                typer.echo(f"    {k}: {v}")


@inspect_app.command("fills")
def fills(
    order_id: Optional[str] = typer.Option(None, "--order-id", help="Filter by order ID"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """List trade fills with execution details and fees."""
    from aletheia.storage.repositories import list_fills
    rows = list_fills(limit=limit)
    if order_id:
        rows = [r for r in rows if r.get("order_id") == order_id]
    display = []
    for r in rows:
        display.append({
            "id": r.get("id"),
            "order_id": r.get("order_id"),
            "symbol": r.get("symbol"),
            "quantity": r.get("quantity"),
            "fill_price": r.get("fill_price"),
            "fee_usdt": r.get("fee_usdt"),
            "execution_mode": r.get("execution_mode"),
            "filled_at": r.get("filled_at"),
        })
    _output(display, json_output)


@inspect_app.command("liquidations")
def liquidations(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
    hours: int = typer.Option(4, "--hours", help="Lookback window in hours"),
    limit: int = typer.Option(30, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show recent liquidation events from Binance Futures."""
    from aletheia.storage.repositories import recent_liquidations
    from aletheia.storage.repositories import get_active_universe_members
    if symbol:
        rows = recent_liquidations(symbol, hours=hours)[:limit]
    else:
        members = get_active_universe_members()
        rows = []
        for m in members:
            sym = m.get("symbol", "")
            if sym:
                rows.extend(recent_liquidations(sym, hours=hours))
        rows.sort(key=lambda r: r.get("event_time", ""), reverse=True)
        rows = rows[:limit]
    display = []
    for r in rows:
        display.append({
            "symbol": r.get("symbol"),
            "side": r.get("side"),
            "quantity": r.get("quantity"),
            "price": r.get("price"),
            "event_time": r.get("event_time"),
            "exchange": r.get("exchange"),
        })
    _output(display, json_output)


@inspect_app.command("calibration")
def calibration(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show net calibration by family, direction, market and horizon."""
    from aletheia.storage.repositories import calibration_stats
    stats = calibration_stats()
    if not stats:
        typer.echo("(no calibration data)")
        return
    if json_output:
        _output(stats, True)
        return
    typer.echo("=== Net Signal Calibration (cost-aware outcomes) ===")
    typer.echo(
        f"  {'family':<18s} {'side':<6s} {'market':<7s} "
        f"{'horizon':<6s} {'n':>4s} {'win%':>6s} {'avg_net':>8s}"
    )
    for row in stats:
        family = row.get("scanner_family") or "unknown"
        direction = row.get("direction") or "?"
        market_type = row.get("market_type") or "spot"
        horizon = row.get("horizon") or "?"
        n = row.get("total_count") or row.get("n") or 0
        win_count = row.get("win_count", 0)
        win_pct = (win_count / n * 100) if n else 0
        avg_ret = row.get("avg_return") or row.get("avg_return_pct") or 0
        typer.echo(
            f"  {family:<18s} {direction:<6s} {market_type:<7s} "
            f"{horizon:<6s} {n:>4d} {win_pct:>5.1f}% {avg_ret:>+7.2f}%"
        )


# ===========================================================================
# MEDIUM PRIORITY — factor-feedback, microstructure, serenity, shadow-trades,
#                   agent-runs, equity, reflection, infra-events
# ===========================================================================


@inspect_app.command("factor-feedback")
def factor_feedback(
    status_filter: Optional[str] = typer.Option(None, "--status", help="Filter: preferred | active | watch | blocked_candidate"),
    limit: int = typer.Option(30, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show scanner factor feedback classification (win_rate, expectancy, status)."""
    from aletheia.analytics.factor_feedback import build_factor_feedback
    rows = build_factor_feedback(limit=200, min_trades=2)
    if status_filter:
        rows = [r for r in rows if r.get("status") == status_filter]
    rows = rows[:limit]
    if json_output:
        _output(rows, True)
        return
    if not rows:
        typer.echo("(no factor feedback data — need closed trades)")
        return
    typer.echo(f"  {'key':<30s} {'trades':>6s} {'win%':>5s} {'expect':>7s} {'PF':>5s} {'status':<18s}")
    for r in rows:
        key = (r.get("factor_key") or r.get("scanner") or "?")[:30]
        trades = r.get("total_trades") or r.get("n") or 0
        wr = r.get("win_rate", 0)
        exp = r.get("expectancy", 0)
        pf = r.get("profit_factor", 0)
        st = r.get("status", "?")
        typer.echo(f"  {key:<30s} {trades:>6d} {wr:>4.0f}% {exp:>+6.2f} {pf:>5.2f} {st:<18s}")


@inspect_app.command("microstructure")
def microstructure(
    symbol: Optional[str] = typer.Option(None, "--symbol", "-s", help="Show single symbol details"),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show latest funding rate, OI, and orderbook data per symbol."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.repositories import get_active_universe_members, get_universe_asset
    if symbol:
        instruments = [(symbol, "", "")]
    else:
        members = get_active_universe_members()
        instruments = []
        for member in members[:limit]:
            asset = get_universe_asset(member.get("asset_id")) or {}
            if member.get("symbol"):
                instruments.append((
                    member["symbol"],
                    asset.get("venue", ""),
                    member.get("market_type") or asset.get("market_type", ""),
                ))

    result = []
    for sym, venue, market_type in instruments:
        from aletheia.data.freshness import assess_freshness

        deriv_exchange = venue or None
        fr = repo.latest_funding_rate(sym, exchange=deriv_exchange)
        oi = repo.latest_open_interest(sym, exchange=deriv_exchange)
        ob = repo.latest_orderbook(sym, exchange=venue, market_type=market_type)
        entry = {"symbol": sym, "exchange": venue or None, "market_type": market_type or None}
        if fr:
            entry["funding_rate"] = fr.get("funding_rate")
            entry["funding_exchange"] = fr.get("exchange")
            entry["funding_observed_at"] = fr.get("observed_at")
            entry["funding_state"] = assess_freshness(fr, "funding").state
        if oi:
            entry["oi_usd"] = oi.get("open_interest_usd")
            entry["oi_delta_1h"] = oi.get("oi_change_1h_pct")
            entry["oi_exchange"] = oi.get("exchange")
            entry["oi_observed_at"] = oi.get("observed_at")
            entry["oi_state"] = assess_freshness(oi, "open_interest").state
        if ob:
            entry["spread_bps"] = ob.get("spread_bps")
            entry["depth_10bps_usd"] = ob.get("total_depth_10bps_usd")
            entry["imbalance"] = ob.get("imbalance_ratio")
            entry["orderbook_observed_at"] = ob.get("observed_at")
            entry["orderbook_state"] = assess_freshness(ob, "orderbook").state
        result.append(entry)
    _output(result, json_output)


@inspect_app.command("serenity")
def serenity(
    what: str = typer.Argument("opps", help="What to show: themes | runs | opps"),
    status_filter: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show Serenity research data (themes, runs, or opportunities)."""
    from aletheia.storage import repositories as repo
    if what == "themes":
        rows = repo.list_serenity_themes(status=status_filter or "active", limit=limit)
        display = [{
            "id": r.get("id", "")[:8],
            "name": r.get("name"),
            "status": r.get("status"),
            "confidence": r.get("confidence"),
            "created_at": r.get("created_at"),
        } for r in rows]
    elif what == "runs":
        rows = repo.list_serenity_research_runs(limit=limit)
        display = [{
            "id": r.get("id", "")[:8],
            "status": r.get("status"),
            "trigger_type": r.get("trigger_type"),
            "mode": r.get("mode"),
            "stage": r.get("stage"),
            "started_at": r.get("started_at"),
        } for r in rows]
    else:
        rows = repo.list_serenity_opportunities(status=status_filter, limit=limit)
        display = [{
            "id": r.get("id", "")[:8],
            "symbol": r.get("symbol"),
            "direction": r.get("direction"),
            "confidence_score": r.get("confidence_score"),
            "evidence_tier": r.get("evidence_tier"),
            "thesis": (r.get("thesis") or "")[:80],
            "status": r.get("status"),
        } for r in rows]
    _output(display, json_output)


@inspect_app.command("shadow-trades")
def shadow_trades(
    scanner: Optional[str] = typer.Option(None, "--scanner", help="Filter by scanner_family"),
    resolved: bool = typer.Option(False, "--resolved", help="Only show resolved trades"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show shadow trades (counterfactual: what if we traded #2/#3 candidate)."""
    from aletheia.storage.repositories import list_shadow_trades
    rows = list_shadow_trades(
        scanner_family=scanner,
        resolved=resolved or None,
        limit=limit,
    )
    display = [{
        "batch_id": r.get("batch_id", "")[:8],
        "symbol": r.get("symbol"),
        "direction": r.get("direction"),
        "scanner_family": r.get("scanner_family"),
        "rank": r.get("rank"),
        "trade_score": r.get("trade_score"),
        "was_selected": r.get("was_selected"),
        "pnl_pct": r.get("pnl_pct"),
        "resolved_at": r.get("resolved_at"),
    } for r in rows]
    _output(display, json_output)


@inspect_app.command("agent-runs")
def agent_runs(
    agent: Optional[str] = typer.Option(None, "--agent", help="Filter by agent name"),
    limit: int = typer.Option(15, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show agent run history with status and duration."""
    from aletheia.agent_runtime.run_view import finished_at, summarize_run_errors
    from aletheia.storage.repositories import list_agent_runs
    rows = list_agent_runs(limit=limit)
    if agent:
        rows = [r for r in rows if r.get("agent_name") == agent]
    display = []
    for r in rows:
        started = r.get("started_at") or ""
        completed = finished_at(r) or ""
        duration_s = None
        if started and completed:
            try:
                s = datetime.fromisoformat(started.replace("Z", "+00:00"))
                e = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                duration_s = round((e - s).total_seconds(), 1)
            except Exception:
                pass
        display.append({
            "id": r.get("id", "")[:8],
            "agent": r.get("agent_name"),
            "status": r.get("status"),
            "duration_s": duration_s,
            "error": summarize_run_errors(r.get("errors") or r.get("error"))[:60],
            "started_at": started,
        })
    _output(display, json_output)


@inspect_app.command("equity")
def equity(
    days: int = typer.Option(7, "--days", "-d", help="Lookback window in days"),
    limit: int = typer.Option(50, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show portfolio equity time series (from portfolio_snapshots)."""
    from aletheia.storage.repositories import list_portfolio_snapshots
    rows = list_portfolio_snapshots(days=days, limit=limit)
    display = [{
        "snapshot_at": r.get("snapshot_at"),
        "equity_usdt": r.get("equity_usdt"),
        "available_usdt": r.get("available_usdt"),
        "unrealized_pnl": r.get("unrealized_pnl"),
        "realized_pnl_today": r.get("realized_pnl_today"),
        "open_positions": r.get("open_positions"),
    } for r in rows]
    if json_output:
        _output(display, True)
        return
    if not display:
        typer.echo("(no portfolio snapshots)")
        return
    typer.echo(f"=== Equity Curve ({days}d, {len(display)} snapshots) ===")
    for d in display[:30]:
        eq = d.get("equity_usdt") or 0
        upnl = d.get("unrealized_pnl") or 0
        pos = d.get("open_positions") or 0
        typer.echo(f"  {d['snapshot_at']}  eq=${eq:.0f}  uPnL={upnl:+.2f}  pos={pos}")


@inspect_app.command("reflection")
def reflection(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show System Operator's last trading decision (from reflection state)."""
    from aletheia.agent_shell.reflection import get_last_reflection
    ref = get_last_reflection()
    if not ref:
        typer.echo("(no reflection state — System Operator hasn't made a decision yet)")
        return
    if json_output:
        _output(ref if isinstance(ref, dict) else {"reflection": ref}, True)
        return
    if isinstance(ref, dict):
        for k, v in ref.items():
            typer.echo(f"  {k}: {v}")
    else:
        typer.echo(f"  {ref}")


# ---------------------------------------------------------------------------
# Gateway HTTP helpers (alerts + infra-events share these)
# ---------------------------------------------------------------------------

def _gateway_base() -> str:
    from aletheia.gateway.frontdoor_route import resolve_backend_base_url
    return resolve_backend_base_url()


def _gateway_fetch(path: str, *, base: str | None = None, timeout: int = 5) -> dict:
    """Fetch a gateway JSON endpoint; raises typer.Exit(1) if unreachable."""
    import urllib.request
    import urllib.error
    b = base or _gateway_base()
    url = f"{b}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — local-only URL
            return json.loads(resp.read())
    except urllib.error.URLError as exc:
        typer.echo(f"Gateway unreachable ({b}): {exc.reason}", err=True)
        raise typer.Exit(1)


_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

_HEALTH_ISSUE_HINTS: dict[str, tuple[str, str]] = {
    "kill_switch_active":              ("critical", "All trading blocked. Go to Risk & Controls → Kill Switch to release."),
    "trading_paused":                  ("warning",  "Click Resume in the top bar to restart the trading loop."),
    "llm_unavailable":                 ("critical", "Check Settings → Model Provider, then click Resume."),
    "agent_failure_ratio":             ("critical", "Multiple agents failing. Check gateway logs for errors."),
    "market_data_stale":               ("warning",  "Feed will refresh on next 5-min cycle. If stuck >30 min, restart the gateway."),
    "code_drift":                      ("warning",  "Cold restart to load new code: aletheia restart."),
    "health_check_error":              ("critical", "Internal health check threw an exception; check gateway logs."),
}

_RISK_VIOLATION_HINTS: dict[str, tuple[str, str]] = {
    "kill_switch_active":                   ("critical", "Kill switch is engaged — all trading blocked."),
    "live_disabled":                        ("warning",  "Live trading is disabled in config."),
    "live_disabled_while_exchange_live":    ("warning",  "Exchange is in live mode but live_state is disabled."),
    "permission_mode_not_live_eligible":    ("warning",  "Permission mode does not allow live trading."),
    "max_daily_loss_exceeded":              ("critical", "Daily loss limit hit — trading blocked until reset."),
    "account_mode_mismatch":               ("critical", "Account mode mismatch between config and exchange."),
    "stale_market_data":                   ("warning",  "Market data is stale; risk checks may be unreliable."),
    "symbol_blocked":                      ("warning",  "Symbol is on the blocked list."),
    "symbol_not_allowed":                  ("warning",  "Symbol is not in the allowed whitelist."),
    "permission_mode_insufficient":        ("warning",  "Permission mode is insufficient for this action."),
    "exceeds_max_position_notional":       ("warning",  "Position size exceeds the notional cap."),
    "max_live_positions_reached":          ("warning",  "Maximum number of open live positions reached."),
    "exceeds_max_leverage":                ("warning",  "Leverage exceeds the configured cap."),
    "max_total_live_exposure_exceeded":    ("warning",  "Total live exposure exceeds the cap."),
    "exceeds_single_trade_risk":           ("warning",  "Single-trade risk exceeds the cap."),
    "spot_market_disabled":                ("warning",  "Spot market trading is disabled."),
    "futures_market_disabled":             ("warning",  "Futures market trading is disabled."),
}


def _normalize_health_issue(raw: str) -> dict:
    """Convert a /api/health issues[] string to a normalized alert dict."""
    if ":" in raw:
        code, _, entity_id = raw.partition(":")
        if code == "code_updated_pending_restart":
            code = "code_drift"
    else:
        code, entity_id = raw, ""
    severity, action = _HEALTH_ISSUE_HINTS.get(code, ("warning", ""))
    return {
        "category": "health_issue",
        "severity": severity,
        "code": code,
        "entity_id": entity_id or None,
        "message": raw,
        "action": action or None,
    }


def _normalize_risk_violation(raw: str) -> dict:
    """Convert a /api/risk/status violations[] string to a normalized alert dict."""
    code = raw.split(":")[0] if ":" in raw else raw
    entity_id = raw[len(code) + 1:] if ":" in raw else None
    severity, action = _RISK_VIOLATION_HINTS.get(code, ("warning", "Check Risk & Controls for details."))
    return {
        "category": "risk_violation",
        "severity": severity,
        "code": code,
        "entity_id": entity_id or None,
        "message": raw,
        "action": action,
    }


def _build_framework_status(status_data: dict, risk_data: dict, operator_data: dict) -> dict:
    """Derive the concise framework state summary shown in the Web UI Status Bar."""
    # Kill switch
    kill_switch = "engaged" if (
        risk_data.get("kill_switch_active") or status_data.get("kill_switch_active")
    ) else "inactive"

    # Gateway / scheduler
    paused = bool(status_data.get("paused"))
    scheduler_running = bool(status_data.get("scheduler_running"))
    if paused:
        gateway = "paused"
        scheduler = "paused"
    else:
        gateway = "running"
        scheduler = "running" if scheduler_running else "not_running"

    # Operator
    op_status = str(operator_data.get("status") or "offline").lower()
    op_mode = str(operator_data.get("mode") or "")
    if op_status in ("offline", ""):
        operator_state = "offline"
    elif op_status == "stale":
        operator_state = "stale"
    elif op_mode in ("Repairing", "Degraded", "Paused"):
        operator_state = f"blocked:{op_mode.lower()}"
    else:
        operator_state = "running"

    # Risk engine
    risk_status = str(risk_data.get("status") or "not_evaluated")

    return {
        "kill_switch": kill_switch,
        "gateway": gateway,
        "scheduler": scheduler,
        "operator": operator_state,
        "risk_engine": risk_status,
        "exchange_diverged": bool(status_data.get("exchange_diverged")),
        "live_state": status_data.get("live_state"),
        "permission_mode": status_data.get("permission_mode"),
        "scheduler_state": status_data.get("scheduler_state"),
    }


# ---------------------------------------------------------------------------
# alerts — aggregated view of all Web UI warning sources + framework status
# ---------------------------------------------------------------------------

@inspect_app.command("alerts")
def alerts(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show all active alerts and framework status from the running gateway.

    Equivalent to everything the Web UI Status Bar warns about. Aggregates:
      1. /api/status        → gateway/scheduler paused, exchange diverged
      2. /api/risk/status   → kill switch, risk violations (blocked/warning)
      3. /api/health        → health issues (llm_unavailable, market_data_stale, …)
      4. /api/health/infra/events → collector problems and runtime warnings
      5. /api/operator/status → operator offline/stale/blocked, last_error

    Also outputs a framework_status snapshot (kill switch, scheduler, operator,
    risk engine) so agents can read basic system state without parsing alerts.
    """
    base = _gateway_base()
    status_data   = _gateway_fetch("/api/status",                                        base=base)
    risk_data     = _gateway_fetch("/api/risk/status",                                   base=base)
    health_data   = _gateway_fetch("/api/health",                                        base=base)
    infra_data    = _gateway_fetch("/api/health/infra/events?limit=20&min_severity=warning", base=base)
    operator_data = _gateway_fetch("/api/operator/status",                               base=base)

    rows: list[dict] = []

    # 1. Gateway / scheduler paused
    if status_data.get("paused"):
        rows.append({
            "category": "framework",
            "severity": "warning",
            "code":     "gateway_paused",
            "entity_id": None,
            "message":  "Gateway is paused — trading loop suspended.",
            "action":   "Click Resume in the top bar or run: aletheia gateway resume",
        })

    if not status_data.get("paused") and not status_data.get("scheduler_running"):
        rows.append({
            "category": "framework",
            "severity": "critical",
            "code":     "scheduler_not_running",
            "entity_id": None,
            "message":  f"Scheduler is not running (state={status_data.get('scheduler_state')}).",
            "action":   "Restart the gateway.",
        })

    # 2. Exchange config diverged
    if status_data.get("exchange_diverged"):
        rows.append({
            "category": "framework",
            "severity": "warning",
            "code":     "exchange_diverged",
            "entity_id": status_data.get("exchange"),
            "message":  "Exchange config diverged — execution and market-data exchanges do not match.",
            "action":   "Check Settings → Execution to align exchange config.",
        })

    # 3. Kill switch
    if risk_data.get("kill_switch_active"):
        rows.append({
            "category": "risk",
            "severity": "critical",
            "code":     "kill_switch_active",
            "entity_id": None,
            "message":  "Kill switch is ENGAGED — all trading blocked.",
            "action":   "Go to Risk & Controls → Kill Switch to release.",
        })

    # 4. Risk violations (deduplicate kill_switch already added above)
    for raw in risk_data.get("violations") or []:
        code = raw.split(":")[0] if ":" in raw else raw
        if code == "kill_switch_active":
            continue  # already added above
        rows.append(_normalize_risk_violation(str(raw)))

    # 5. health.issues (may overlap with kill_switch — deduplicate by code)
    existing_codes = {r["code"] for r in rows}
    for raw in health_data.get("issues") or []:
        norm = _normalize_health_issue(str(raw))
        if norm["code"] not in existing_codes:
            rows.append(norm)
            existing_codes.add(norm["code"])

    # 6. Infra events (warning+)
    for e in infra_data.get("events") or []:
        rows.append({
            "category":  "infra_event",
            "severity":  e.get("severity") or "warning",
            "code":      e.get("code"),
            "entity_id": e.get("entity_id"),
            "message":   e.get("message") or "",
            "action":    e.get("action"),
        })

    # 7. Operator state
    op_status = str(operator_data.get("status") or "offline").lower()
    op_mode   = str(operator_data.get("mode") or "")
    if op_status in ("offline", ""):
        rows.append({
            "category": "operator",
            "severity": "critical",
            "code":     "operator_offline",
            "entity_id": None,
            "message":  "Operator is offline — no heartbeat received.",
            "action":   "Check gateway logs. If stuck, restart the gateway.",
        })
    elif op_status == "stale":
        rows.append({
            "category": "operator",
            "severity": "warning",
            "code":     "operator_stale",
            "entity_id": None,
            "message":  "Operator heartbeat is delayed — usually self-recovers in 1–2 min.",
            "action":   "If stuck >5 min, restart the gateway.",
        })
    elif op_mode in ("Repairing", "Degraded", "Paused"):
        _blocked_msgs = {
            "Repairing": "Operator is repairing — trading loop suspended.",
            "Degraded":  "Operator is degraded — trading loop may be suspended.",
            "Paused":    "Operator is paused — click Resume to restart.",
        }
        rows.append({
            "category": "operator",
            "severity": "warning",
            "code":     f"operator_blocked:{op_mode.lower()}",
            "entity_id": None,
            "message":  _blocked_msgs.get(op_mode, f"Operator mode: {op_mode}"),
            "action":   "Click Resume in the top bar or check Command Center.",
        })

    last_error = operator_data.get("last_error")
    if last_error:
        rows.append({
            "category":  "operator",
            "severity":  "critical",
            "code":      "operator_last_error",
            "entity_id": None,
            "message":   str(last_error),
            "action":    "Check gateway logs or restart the operator loop.",
        })

    rows.sort(key=lambda r: _SEVERITY_ORDER.get(str(r.get("severity")), 99))

    fw = _build_framework_status(status_data, risk_data, operator_data)

    if json_output:
        typer.echo(json.dumps(
            {"framework_status": fw, "alerts": rows, "alert_count": len(rows)},
            indent=2, ensure_ascii=False, default=str,
        ))
        return

    # Text output — framework status first, then alerts
    w = 14
    typer.echo("=== Framework Status ===")
    typer.echo(f"  {'Kill Switch':<{w}}: {fw['kill_switch']}")
    typer.echo(f"  {'Gateway':<{w}}: {fw['gateway']}")
    typer.echo(f"  {'Scheduler':<{w}}: {fw['scheduler']}  (state={fw['scheduler_state']})")
    typer.echo(f"  {'Operator':<{w}}: {fw['operator']}")
    typer.echo(f"  {'Risk Engine':<{w}}: {fw['risk_engine']}")
    typer.echo(f"  {'Live State':<{w}}: {fw['live_state']}")
    typer.echo(f"  {'Permission':<{w}}: {fw['permission_mode']}")
    if fw["exchange_diverged"]:
        typer.echo(f"  {'Exchange':<{w}}: DIVERGED")
    typer.echo("")

    if not rows:
        typer.echo("=== Active Alerts: none ===")
        return

    typer.echo(f"=== Active Alerts ({len(rows)}) ===")
    for r in rows:
        sev = str(r.get("severity") or "warning").upper()
        code = r.get("code") or ""
        entity = f"  entity={r['entity_id']}" if r.get("entity_id") else ""
        cat = r.get("category", "")
        typer.echo(f"[{sev}] {code}{entity}  ({cat})")
        typer.echo(f"  {r.get('message') or ''}")
        if r.get("action"):
            typer.echo(f"  → {r['action']}")


@inspect_app.command("infra-events")
def infra_events(
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter: info | warning | critical"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show live infra events from the running gateway (same data as Web UI)."""
    base = _gateway_base()
    url = f"/api/health/infra/events?limit={limit}"
    if severity:
        url += f"&min_severity={severity}"
    payload = _gateway_fetch(url, base=base)
    rows = payload.get("events") or []
    display = [{
        "severity": r.get("severity"),
        "source": r.get("source"),
        "code": r.get("code"),
        "entity_id": r.get("entity_id"),
        "message": r.get("message") or "",
        "action": r.get("action"),
        "created_at": r.get("created_at") or r.get("ts"),
    } for r in rows]
    _output(display, json_output)


# ===========================================================================
# LOW PRIORITY — audit-events, predictions, agent-status
# ===========================================================================


@inspect_app.command("audit-events")
def audit_events(
    event_type: Optional[str] = typer.Option(None, "--event-type", help="Filter by event_type"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show audit trail entries (security/trust events)."""
    from aletheia.storage.repositories import list_audit_events
    rows = list_audit_events(limit=limit)
    if event_type:
        rows = [r for r in rows if r.get("event_type") == event_type]
    display = [{
        "id": r.get("id", "")[:8],
        "event_type": r.get("event_type"),
        "entity_type": r.get("entity_type"),
        "actor": r.get("actor"),
        "message": (str(r.get("details") or ""))[:80],
        "created_at": r.get("created_at"),
    } for r in rows]
    _output(display, json_output)


@inspect_app.command("predictions")
def predictions(
    what: str = typer.Argument("events", help="What to show: events | runs"),
    limit: int = typer.Option(10, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show prediction market data (events or runs)."""
    from aletheia.storage import repositories as repo
    if what == "runs":
        rows = repo.list_prediction_runs(limit=limit)
        display = [{
            "id": r.get("id", "")[:8],
            "status": r.get("status"),
            "events_found": r.get("events_found"),
            "started_at": r.get("started_at"),
        } for r in rows]
    else:
        rows = repo.list_prediction_events(limit=limit)
        display = [{
            "id": r.get("id", "")[:8],
            "title": (r.get("title") or "")[:60],
            "category": r.get("category"),
            "status": r.get("status"),
            "source_url": r.get("source_url"),
        } for r in rows]
    _output(display, json_output)


@inspect_app.command("agent-status")
def agent_status(
    json_output: bool = typer.Option(False, "--json"),
):
    """Show operator agent heartbeat and queue state."""
    from aletheia.agent_shell.operator_store import get_latest_heartbeat
    hb = get_latest_heartbeat()
    if not hb:
        typer.echo("(no heartbeat — operator agent has not run yet)")
        return
    display = {
        "status": hb.get("status"),
        "mode": hb.get("mode"),
        "queue_size": hb.get("queue_size"),
        "current_intent": hb.get("current_intent"),
        "last_error": hb.get("last_error"),
        "heartbeat_at": hb.get("heartbeat_at") or hb.get("updated_at"),
    }
    _output(display, json_output)


# ---------------------------------------------------------------------------

@inspect_app.command("decisions")
def decisions(
    hours: int = typer.Option(24, "--hours", "-h", help="Rolling window in hours (1–168)"),
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows (capped at 200)"),
    outcome: str = typer.Option(
        "",
        "--outcome",
        help="Filter by outcome: trade, operator_skip, operator_watch, user_reject, auto_reject, portfolio_guard_reject, conviction_gate_defer (comma-separated)",
    ),
    symbol: str = typer.Option("", "--symbol", "-s", help="Filter to a single symbol"),
    summary: bool = typer.Option(False, "--summary", help="Show counts + top reasons instead of rows"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Candidate Ledger: recent terminal decisions (trade / skip / auto-reject).

    Mirrors the Blotter's Candidate Ledger table so operators can audit
    skip and rejection patterns without opening the browser UI.

    Examples:
      aletheia inspect decisions                      # last 24h, all outcomes
      aletheia inspect decisions --hours 168          # last 7 days
      aletheia inspect decisions --outcome operator_skip --limit 20
      aletheia inspect decisions --outcome auto_reject --summary
      aletheia inspect decisions --symbol BTC/USDT --hours 48
    """
    from aletheia.server.routes.analytics import get_operator_decisions

    hours_i = max(1, min(int(hours), 168))
    limit_i = max(1, min(int(limit), 200))
    outcome_arg: str | None = outcome.strip() or None
    symbol_arg: str | None = symbol.strip() or None

    data = get_operator_decisions(
        hours=hours_i,
        limit=limit_i,
        outcome=outcome_arg,
        symbol=symbol_arg,
    )

    counts = data.get("counts") or {}
    items = data.get("items") or []
    total = data.get("total_count", 0)
    matched = data.get("matched_count", total)

    if summary:
        from collections import Counter
        if json_output:
            _output({"window_hours": hours_i, "total_count": total, "counts": counts}, True)
            return
        typer.echo(f"window: {hours_i}h  total: {total}  matched: {matched}")
        typer.echo("")
        label_map = {
            "trade": "Filled",
            "operator_skip": "Op Skip",
            "operator_watch": "Watch",
            "user_reject": "User Reject",
            "auto_reject": "Auto Reject",
            "reject_unknown": "Reject (?)",
            "portfolio_guard_reject": "Guard Reject",
            "conviction_gate_defer": "Conv Defer",
        }
        for oc in ("trade", "operator_skip", "portfolio_guard_reject", "operator_watch", "auto_reject", "user_reject", "reject_unknown", "conviction_gate_defer"):
            n = counts.get(oc, 0)
            if n == 0:
                continue
            typer.echo(f"  {label_map.get(oc, oc):12}  {n}")
        # Top reasons per outcome bucket
        for bucket, label in [
            ("auto_reject", "auto_reject top reasons"),
            ("operator_skip", "operator_skip top reasons"),
            ("portfolio_guard_reject", "portfolio_guard_reject top reasons"),
            ("conviction_gate_defer", "conviction_gate_defer top reasons"),
        ]:
            bucket_items = [it for it in items if it.get("outcome") == bucket]
            if not bucket_items:
                continue
            reason_counts: Counter = Counter()
            for it in bucket_items:
                r = it.get("one_line_reason") or ""
                if "Rejected: " in r:
                    after = r.split("Rejected: ", 1)[1]
                    for chunk in after.split(","):
                        tok = chunk.strip().split(":")[0].split("[")[0].strip()
                        if tok and not tok.endswith("…"):
                            reason_counts[tok] += 1
                elif "terminal skip:" in r:
                    after = r.split("terminal skip:", 1)[1]
                    for chunk in after.split(";"):
                        tok = chunk.strip().split(":")[0].strip()
                        if tok and not tok.endswith("…"):
                            reason_counts[tok] += 1
            if reason_counts:
                typer.echo(f"\n  {label}:")
                for tok, cnt in reason_counts.most_common(8):
                    typer.echo(f"    {cnt:4d}  {tok}")
        return

    if json_output:
        display = [
            {
                "decided_at": it.get("decided_at", "")[:19],
                "symbol": it.get("symbol"),
                "direction": it.get("direction"),
                "outcome": it.get("outcome"),
                "conviction": it.get("conviction"),
                "reason": it.get("one_line_reason"),
            }
            for it in items
        ]
        _output(display, True)
        return

    if not items:
        typer.echo(f"(no decisions in the last {hours_i}h)")
        return

    header = f"{'Time':19}  {'Symbol':15}  {'Side':5}  {'Outcome':14}  Reason"
    typer.echo(header)
    typer.echo("-" * min(len(header) + 40, 120))
    for it in items:
        ts = (it.get("decided_at") or "")[:19]
        sym = (it.get("symbol") or "")[:15]
        side = (it.get("direction") or "")[:5]
        oc = (it.get("outcome") or "")[:14]
        reason = (it.get("one_line_reason") or "")
        # Trim reason to fit terminal width reasonably
        reason_short = reason[:70] + "…" if len(reason) > 70 else reason
        typer.echo(f"{ts:19}  {sym:15}  {side:5}  {oc:14}  {reason_short}")
