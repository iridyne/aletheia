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
