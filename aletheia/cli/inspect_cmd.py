"""Read-only runtime state inspection CLI: `aletheia inspect ...`

All subcommands are safe to run at any time -- they never write to the DB,
modify config, or trigger side effects. Designed for Agent / developer use.
"""

import json
from datetime import datetime, timedelta, timezone
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
    """Show paper trader ranker evaluation -- diagnose why no trades are opening."""
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
            "permission_mode": cfg.operator.permission_mode,
        },
        "exchange": {
            "mode": cfg.exchange.mode,
            "paper_execution_mode": cfg.exchange.paper_execution_mode,
        },
        "risk": {
            "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
            "max_single_trade_risk_pct": cfg.risk.max_single_trade_risk_pct,
            "max_open_positions_demo": cfg.risk.max_open_positions_demo,
            "max_open_positions_live": cfg.risk.max_open_positions_live,
            "starting_capital_usdt": cfg.risk.starting_capital_usdt,
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
    from aletheia.analytics.trade_stats import compute_rolling_trade_stats, compute_order_stats
    from aletheia.data.price_feed import cache_stats
    from aletheia.portfolio.pnl import today_realized_pnl, mark_positions_to_market
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
            portfolio_section["equity_usdt"] = cached.get("equity_usdt")
            portfolio_section["exchange_age_sec"] = cached.get("age_seconds")
    except Exception:
        pass

    # PnL
    today_pnl = today_realized_pnl()
    rolling = compute_rolling_trade_stats(days=days)
    pnl_section = {
        "today_realized": round(today_pnl, 2),
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
    from aletheia.analytics.trade_stats import compute_rolling_trade_stats
    from aletheia.portfolio.pnl import today_realized_pnl, mark_positions_to_market
    from aletheia.storage.repositories import list_positions

    # Today detail
    today_str = datetime.now(timezone.utc).date().isoformat()
    closed = list_positions(status="closed", limit=200)
    today_closed = [
        p for p in closed
        if (p.get("closed_at") or "")[:10] == today_str
    ]
    today_total = today_realized_pnl()
    today_detail = [
        {
            "symbol": p["symbol"], "direction": p["direction"],
            "pnl": round(p.get("realized_pnl", 0), 2),
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
        "today": {"date": today_str, "realized": round(today_total, 2), "trades": today_detail},
        "rolling": rolling,
        "open_positions": positions_out,
    }

    if json_output:
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return

    # Human-readable
    typer.echo(f"=== Today ({today_str} UTC) ===")
    typer.echo(f"  realized: {today_total:.2f} USDT ({len(today_detail)} trades)")
    for t in today_detail:
        typer.echo(f"  {t['symbol']} {t['direction']}: {t['pnl']:+.2f} ({t['close_reason']})")

    typer.echo("")
    typer.echo(f"=== {days}-Day Rolling ===")
    typer.echo(f"  trades: {rolling['total_trades']}  wins: {rolling['wins']}  losses: {rolling['losses']}  win_rate: {rolling['win_rate']}%")
    typer.echo(f"  total: {rolling['total_pnl']:+.2f}  avg: {rolling['avg_pnl']:+.2f}  best: {rolling['best_win']:+.2f}  worst: {rolling['worst_loss']:+.2f}")

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
    """Show signal calibration stats — scanner family × horizon win rates."""
    from aletheia.storage.repositories import calibration_stats
    stats = calibration_stats()
    if not stats:
        typer.echo("(no calibration data)")
        return
    if json_output:
        _output(stats, True)
        return
    typer.echo("=== Signal Calibration (forward outcomes) ===")
    typer.echo(f"  {'family':<20s} {'horizon':<6s} {'n':>4s} {'win%':>6s} {'avg_ret':>8s}")
    typer.echo(f"  {'-'*20} {'-'*6} {'-'*4} {'-'*6} {'-'*8}")
    for row in stats:
        family = row.get("scanner_family") or "unknown"
        horizon = row.get("horizon") or "?"
        n = row.get("total_count") or row.get("n") or 0
        win_count = row.get("win_count", 0)
        win_pct = (win_count / n * 100) if n else 0
        avg_ret = row.get("avg_return") or row.get("avg_return_pct") or 0
        typer.echo(f"  {family:<20s} {horizon:<6s} {n:>4d} {win_pct:>5.1f}% {avg_ret:>+7.2f}%")


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
    from aletheia.storage.repositories import get_active_universe_members
    if symbol:
        symbols = [symbol]
    else:
        members = get_active_universe_members()
        symbols = [m.get("symbol") for m in members if m.get("symbol")][:limit]

    result = []
    for sym in symbols:
        fr = repo.latest_funding_rate(sym)
        oi = repo.latest_open_interest(sym)
        ob = repo.latest_orderbook(sym)
        entry = {"symbol": sym}
        if fr:
            entry["funding_rate"] = fr.get("funding_rate")
            entry["predicted_rate"] = fr.get("predicted_rate")
        if oi:
            entry["oi_usd"] = oi.get("open_interest_usd")
            entry["oi_delta_1h"] = oi.get("oi_change_1h_pct")
        if ob:
            entry["spread_bps"] = ob.get("spread_bps")
            entry["depth_10bps_usd"] = ob.get("total_depth_10bps_usd")
            entry["imbalance"] = ob.get("bid_ask_imbalance")
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
    from aletheia.storage.repositories import list_agent_runs
    rows = list_agent_runs(limit=limit)
    if agent:
        rows = [r for r in rows if r.get("agent_name") == agent]
    display = []
    for r in rows:
        started = r.get("started_at") or ""
        completed = r.get("completed_at") or ""
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
            "error": (r.get("error") or "")[:60],
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
    """Show PI Agent's last trading decision (from reflection state)."""
    from aletheia.agent_shell.reflection import get_last_reflection
    ref = get_last_reflection()
    if not ref:
        typer.echo("(no reflection state — PI agent hasn't made a decision yet)")
        return
    if json_output:
        _output(ref if isinstance(ref, dict) else {"reflection": ref}, True)
        return
    if isinstance(ref, dict):
        for k, v in ref.items():
            typer.echo(f"  {k}: {v}")
    else:
        typer.echo(f"  {ref}")


@inspect_app.command("infra-events")
def infra_events(
    severity: Optional[str] = typer.Option(None, "--severity", help="Filter: info | warning | critical"),
    limit: int = typer.Option(20, "--limit", "-n"),
    json_output: bool = typer.Option(False, "--json"),
):
    """Show in-memory infra event buffer (diagnostic runtime events)."""
    from aletheia.infra.events import list_infra_events
    rows = list_infra_events(limit=limit, min_severity=severity)
    display = [{
        "severity": r.get("severity"),
        "source": r.get("source"),
        "code": r.get("code"),
        "message": (r.get("message") or "")[:100],
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
