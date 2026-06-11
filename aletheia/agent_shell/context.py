"""AppContextProvider — assembles compact system context for LLM prompts."""

from aletheia.config.loader import load_config
from aletheia.agent_shell.operator_store import get_latest_heartbeat, list_tasks
from aletheia.storage.repositories import (
    list_agent_runs,
    list_trade_proposals,
    list_fills,
    list_knowledge_items,
    list_market_snapshots,
    list_orders,
    list_positions,
    list_strategies,
)

ALL_SECTIONS = (
    "system",
    "market",
    "agents",
    "trading",
    "proposals",
    "risk",
    "knowledge",
    "strategies",
    "errors",
    "operator_state",
)

CONTEXT_PROFILES: dict[str, tuple[str, ...]] = {
    "minimal_context": ("system", "operator_state"),
    "operational_context": ("system", "agents", "proposals", "errors", "operator_state"),
    "trading_context": ("system", "market", "trading", "proposals", "strategies", "risk", "errors", "operator_state"),
    "knowledge_context": ("system", "agents", "knowledge", "strategies", "operator_state"),
    "repair_context": ("system", "agents", "trading", "proposals", "strategies", "risk", "errors", "operator_state"),
}


def sections_for_profile(profile: str) -> list[str]:
    return list(CONTEXT_PROFILES.get(profile, CONTEXT_PROFILES["operational_context"]))


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

    if "proposals" in wanted:
        proposals = list_trade_proposals(limit=10)
        ctx["proposals"] = {
            "recent": [
                {
                    "id": p["id"],
                    "symbol": p["symbol"],
                    "direction": p["direction"],
                    "status": p["status"],
                    "strategy_id": p.get("strategy_id"),
                    "created_at": p.get("created_at"),
                }
                for p in proposals
            ],
            "pending_count": sum(1 for p in proposals if p.get("status") == "pending"),
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

    if "errors" in wanted:
        from aletheia.agent_runtime.health import compute_health

        health = compute_health()
        failed_runs = [
            r
            for r in list_agent_runs(limit=10)
            if r.get("status") in {"failed", "error"}
        ]
        failed_tasks = [
            {
                "id": task["id"],
                "task_type": task["task_type"],
                "error": task.get("error"),
                "completed_at": task.get("completed_at"),
            }
            for task in list_tasks(limit=10, status="failed")
        ]
        ctx["errors"] = {
            "health_issues": health["issues"],
            "recent_failed_runs": [
                {
                    "agent": run["agent_name"],
                    "status": run["status"],
                    "started_at": run["started_at"],
                    "ended_at": run.get("ended_at"),
                }
                for run in failed_runs[:5]
            ],
            "recent_failed_operator_tasks": failed_tasks,
        }

    if "operator_state" in wanted:
        heartbeat = get_latest_heartbeat()
        running_tasks = list_tasks(limit=10, status="running")
        ctx["operator_state"] = {
            "heartbeat": (
                {
                    "status": heartbeat["status"],
                    "mode": heartbeat["mode"],
                    "permission_mode": heartbeat["permission_mode"],
                    "current_intent": heartbeat.get("current_intent"),
                    "queue_size": heartbeat["queue_size"],
                    "last_seen_at": heartbeat.get("last_seen_at"),
                    "session_id": heartbeat.get("session_id"),
                    "active_branch_id": heartbeat.get("active_branch_id"),
                    "active_branch_kind": heartbeat.get("active_branch_kind"),
                    "active_branch_title": heartbeat.get("active_branch_title"),
                }
                if heartbeat
                else None
            ),
            "running_tasks": [
                {
                    "id": task["id"],
                    "task_type": task["task_type"],
                    "assigned_to": task["assigned_to"],
                    "started_at": task.get("started_at"),
                }
                for task in running_tasks
            ],
        }

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
    if "proposals" in ctx:
        p = ctx["proposals"]
        parts.append(f"Proposals: {p['pending_count']} pending")
    if "errors" in ctx:
        e = ctx["errors"]
        issues = e.get("health_issues") or []
        failed_tasks = e.get("recent_failed_operator_tasks") or []
        parts.append(
            f"Errors: {len(issues)} health issue(s), {len(failed_tasks)} failed operator task(s)"
        )
    if "operator_state" in ctx:
        operator_state = ctx["operator_state"]
        heartbeat = operator_state.get("heartbeat") if isinstance(operator_state, dict) else None
        if heartbeat:
            parts.append(
                "Operator: "
                f"status={heartbeat.get('status')}, "
                f"mode={heartbeat.get('mode')}, "
                f"intent={heartbeat.get('current_intent') or 'monitoring system state'}"
            )
    return ". ".join(parts) + "."
