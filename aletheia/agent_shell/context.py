"""AppContextProvider — assembles compact system context for LLM prompts."""

from aletheia.config.loader import load_config
from aletheia.storage.repositories import (
    list_agent_runs,
    list_fills,
    list_knowledge_items,
    list_market_snapshots,
    list_orders,
    list_positions,
    list_strategies,
    list_portfolio_snapshots,
)

ALL_SECTIONS = ("system", "market", "agents", "trading", "risk", "knowledge", "strategies")


def build_app_context(sections: list[str] | None = None) -> dict:
    """Assemble context dict from live DB/config. Sync — no await needed."""
    wanted = set(sections) if sections else set(ALL_SECTIONS)
    ctx: dict = {}

    cfg = load_config()

    if "system" in wanted:
        from aletheia.agent_runtime.health import compute_health
        health = compute_health()
        ctx["system"] = {
            "healthy": health["healthy"],
            "issues": health["issues"],
            "permission_mode": cfg.agent.permission_mode,
            "live_state": cfg.live.state,
            "paused": cfg.runtime.paused,
            "kill_switch": cfg.runtime.kill_switch_active,
            "exchange_mode": cfg.exchange.mode,
        }

    if "market" in wanted:
        snaps = list_market_snapshots(limit=10)
        ctx["market"] = [
            {"symbol": s["symbol"], "price": s["price"],
             "change_24h": s.get("change_pct_24h"), "volume_24h": s.get("volume_24h")}
            for s in snaps
        ]

    if "agents" in wanted:
        runs = list_agent_runs(limit=10)
        failed = sum(1 for r in runs if r.get("status") in ("failed", "error"))
        ctx["agents"] = {
            "recent_runs": [
                {"agent": r["agent_name"], "status": r["status"],
                 "started": r["started_at"], "ended": r.get("ended_at")}
                for r in runs[:5]
            ],
            "total_recent": len(runs),
            "failed_recent": failed,
        }

    if "trading" in wanted:
        positions = list_positions(status="open", limit=10)
        orders = list_orders(limit=5)
        fills = list_fills(limit=5)
        ctx["trading"] = {
            "open_positions": [
                {"symbol": p["symbol"], "direction": p["direction"],
                 "quantity": p["quantity"], "entry_price": p["entry_price"],
                 "unrealized_pnl": p.get("unrealized_pnl", 0)}
                for p in positions
            ],
            "recent_orders": len(orders),
            "recent_fills": len(fills),
        }

    if "risk" in wanted:
        ctx["risk"] = {
            "kill_switch": cfg.runtime.kill_switch_active,
            "max_daily_loss_pct": cfg.risk.max_daily_loss_pct,
            "max_demo_position_usdt": cfg.risk.max_demo_position_notional_usdt,
            "max_open_positions_demo": cfg.risk.max_open_positions_demo,
        }

    if "knowledge" in wanted:
        items = list_knowledge_items(limit=5)
        ctx["knowledge"] = {
            "recent_items": [{"title": i["title"], "type": i["item_type"]} for i in items],
            "total_shown": len(items),
        }

    if "strategies" in wanted:
        strats = list_strategies(limit=10)
        ctx["strategies"] = [
            {"name": s["name"], "state": s["state"]}
            for s in strats
        ]

    return ctx


def context_summary_text(ctx: dict) -> str:
    """One-paragraph text summary of context for LLM prompts."""
    parts = []
    if "system" in ctx:
        s = ctx["system"]
        status = "healthy" if s["healthy"] else f"degraded ({', '.join(s['issues'])})"
        parts.append(f"System: {status}, mode={s['permission_mode']}, paused={s['paused']}")
    if "market" in ctx:
        prices = ", ".join(f"{m['symbol']}=${m['price']}" for m in ctx["market"][:3])
        parts.append(f"Market: {prices}")
    if "agents" in ctx:
        a = ctx["agents"]
        parts.append(f"Agents: {a['total_recent']} recent runs, {a['failed_recent']} failed")
    if "trading" in ctx:
        t = ctx["trading"]
        parts.append(f"Trading: {len(t['open_positions'])} open positions")
    return ". ".join(parts) + "."
