"""AppContextProvider — assembles compact system context for LLM prompts."""

from aletheia.config.loader import load_config
from aletheia.agent_shell.operator_store import (
    get_latest_heartbeat,
    get_session,
    list_messages,
    list_tasks,
)
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

DEFAULT_CONTEXT_PROFILE_BY_SESSION_KIND: dict[str, str] = {
    "command_session": "operational_context",
    "diagnostic_session": "repair_context",
    "dreaming_session": "knowledge_context",
    "strategy_session": "trading_context",
    "review_session": "trading_context",
}

CONTEXT_PROFILES: dict[str, tuple[str, ...]] = {
    "minimal_context": ("system", "operator_state"),
    "operational_context": ("system", "agents", "proposals", "errors", "operator_state"),
    "trading_context": ("system", "market", "trading", "proposals", "strategies", "risk", "errors", "operator_state"),
    "knowledge_context": ("system", "agents", "knowledge", "strategies", "operator_state"),
    "repair_context": ("system", "agents", "trading", "proposals", "strategies", "risk", "errors", "operator_state"),
}


def sections_for_profile(profile: str) -> list[str]:
    return list(CONTEXT_PROFILES.get(profile, CONTEXT_PROFILES["operational_context"]))


def profile_for_session_kind(session_kind: str | None) -> str:
    return DEFAULT_CONTEXT_PROFILE_BY_SESSION_KIND.get(
        str(session_kind or "command_session"),
        "operational_context",
    )


def sections_for_session_kind(session_kind: str | None) -> list[str]:
    return sections_for_profile(profile_for_session_kind(session_kind))


def _message_text(content) -> str | None:
    if isinstance(content, str):
        text = content.strip()
        return text or None
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            text = text.strip()
            return text or None
    return None


def session_trace_summary(
    session_id: str | None,
    *,
    pending_user_input: str | None = None,
    limit: int = 50,
) -> str:
    if not session_id:
        return ""

    latest_context: str | None = None
    latest_assistant: str | None = None
    latest_user: str | None = None
    normalized_pending = (pending_user_input or "").strip()

    for message in reversed(list_messages(session_id, limit=limit)):
        text = _message_text(message.get("content"))
        if not text:
            continue
        if normalized_pending and text == normalized_pending:
            continue

        message_type = str(message.get("message_type") or "message")
        role = str(message.get("role") or "assistant")
        if latest_context is None and message_type == "context":
            latest_context = text
            continue
        if latest_assistant is None and role == "assistant" and message_type != "context":
            latest_assistant = text
            continue
        if latest_user is None and role == "user":
            latest_user = text

        if latest_context and latest_assistant and latest_user:
            break

    parts: list[str] = []
    if latest_context:
        parts.append(f"Session context: {latest_context}")
    if latest_assistant:
        parts.append(f"Recent assistant result: {latest_assistant}")
    if latest_user:
        parts.append(f"Recent user note: {latest_user}")
    return " ".join(parts)


def session_lineage_trace_summary(
    session_id: str | None,
    *,
    max_ancestors: int = 3,
) -> str:
    if not session_id:
        return ""

    parts: list[str] = []
    seen = {session_id}
    current = get_session(session_id)
    parent_session_id = str(current.get("parent_session_id") or "").strip() if current else ""
    depth = 0

    while parent_session_id and depth < max_ancestors and parent_session_id not in seen:
        seen.add(parent_session_id)
        parent_session = get_session(parent_session_id)
        if not parent_session:
            break
        summary = session_trace_summary(parent_session_id, limit=50)
        if summary:
            title = str(parent_session.get("title") or parent_session_id).strip() or parent_session_id
            parts.append(f"Parent branch {title}: {summary}")
        parent_session_id = str(parent_session.get("parent_session_id") or "").strip()
        depth += 1

    return " ".join(parts)


def extend_summary_with_session_trace(
    summary: str,
    session_id: str | None,
    *,
    pending_user_input: str | None = None,
) -> str:
    traces = [
        session_trace_summary(session_id, pending_user_input=pending_user_input),
        session_lineage_trace_summary(session_id),
    ]
    trace = " ".join(item for item in traces if item)
    if not trace:
        return summary
    base = summary.strip()
    return f"{base} {trace}".strip()


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
        strats = list_strategies(limit=20)
        ctx["strategies"] = [
            {
                "name": s["name"],
                "state": s["state"],
                "symbol": (s.get("parameters") or {}).get("symbol") if isinstance(s.get("parameters"), dict) else None,
                "paused": s.get("paper_trading_paused", False),
                "under_review": s.get("under_review", False),
                "total_pnl": s.get("total_pnl"),
                "win_rate": s.get("win_rate"),
            }
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
