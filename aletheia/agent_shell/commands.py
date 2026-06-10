"""Command handlers — 11 functions that execute shell commands."""

import json
import logging

from aletheia.config.loader import load_config, save_config
from aletheia.storage.repositories import (
    get_agent_run,
    get_trade_proposal,
    list_agent_runs,
    list_fills,
    list_knowledge_items,
    list_market_snapshots,
    list_orders,
    list_positions,
    list_risk_decisions,
    list_run_steps,
    list_strategies,
    list_tool_calls,
)

logger = logging.getLogger("aletheia.agent_shell.commands")


async def get_system_status(params: dict) -> dict:
    from aletheia.agent_runtime.health import compute_health
    cfg = load_config()
    health = compute_health()
    return {
        "health": "healthy" if health["healthy"] else "degraded",
        "issues": health["issues"],
        "permission_mode": cfg.agent.permission_mode,
        "live_state": cfg.live.state,
        "paused": cfg.runtime.paused,
        "kill_switch": cfg.runtime.kill_switch_active,
        "exchange_mode": cfg.exchange.mode,
        "scheduler_enabled": cfg.runtime.scheduler_enabled,
    }


async def get_agent_activity(params: dict) -> dict:
    runs = list_agent_runs(limit=15)
    return {
        "runs": [
            {"id": r["id"], "agent": r["agent_name"], "status": r["status"],
             "started": r["started_at"], "ended": r.get("ended_at"),
             "trigger": r.get("trigger_type", "scheduled")}
            for r in runs
        ],
        "total": len(runs),
    }


async def get_market_snapshot(params: dict) -> dict:
    snaps = list_market_snapshots(limit=10)
    return {
        "snapshots": [
            {"symbol": s["symbol"], "price": s["price"],
             "change_24h_pct": s.get("change_pct_24h"),
             "volume_24h": s.get("volume_24h"),
             "time": s.get("snapshot_at")}
            for s in snaps
        ],
    }


async def get_positions(params: dict) -> dict:
    positions = list_positions(status="open", limit=20)
    return {
        "positions": [
            {"id": p["id"], "symbol": p["symbol"], "direction": p["direction"],
             "quantity": p["quantity"], "entry_price": p["entry_price"],
             "unrealized_pnl": p.get("unrealized_pnl", 0),
             "opened_at": p.get("opened_at")}
            for p in positions
        ],
        "count": len(positions),
    }


async def explain_agent_run(params: dict) -> dict:
    run_id = params.get("run_id")
    if not run_id:
        runs = list_agent_runs(limit=1)
        if not runs:
            return {"error": "No agent runs found."}
        run_id = runs[0]["id"]

    run = get_agent_run(run_id)
    if not run:
        return {"error": f"Agent run '{run_id}' not found."}

    steps = list_run_steps(run_id, limit=20)
    tools = list_tool_calls(run_id, limit=20)
    return {
        "run": {
            "id": run["id"], "agent": run["agent_name"], "status": run["status"],
            "started": run["started_at"], "ended": run.get("ended_at"),
            "errors": run.get("errors", "[]"),
        },
        "steps": [{"action": s["action"], "output": s.get("output_data", "")[:200]} for s in steps],
        "tools_used": [{"tool": t["tool_name"], "permitted": t["permitted"]} for t in tools],
    }


async def explain_proposal(params: dict) -> dict:
    proposal_id = params.get("proposal_id")
    if not proposal_id:
        from aletheia.storage.repositories import list_trade_proposals
        proposals = list_trade_proposals(limit=1)
        if not proposals:
            return {"error": "No proposals found."}
        proposal_id = proposals[0]["id"]

    proposal = get_trade_proposal(proposal_id)
    if not proposal:
        return {"error": f"Proposal '{proposal_id}' not found."}

    decisions = list_risk_decisions(limit=50)
    related = [d for d in decisions if d.get("proposal_id") == proposal_id]

    return {
        "proposal": {
            "id": proposal["id"], "symbol": proposal["symbol"],
            "direction": proposal["direction"], "status": proposal["status"],
            "rationale": proposal.get("agent_rationale", ""),
        },
        "risk_decisions": [
            {"approved": bool(d["approved"]), "risk_score": d["risk_score"],
             "violations": d.get("violations", "[]"), "message": d.get("message", "")}
            for d in related
        ],
    }


async def pause_agents(params: dict) -> dict:
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    try:
        from aletheia.agent_runtime.scheduler import pause_scheduler
        pause_scheduler()
    except Exception:
        pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "agent_shell", "source": "command_center",
                 "event_type": "gateway_paused", "message": "Agents paused via Agent Shell"})
    return {"paused": True, "message": "All scheduled agents have been paused."}


async def resume_agents(params: dict) -> dict:
    cfg = load_config()
    cfg.runtime.paused = False
    save_config(cfg)
    try:
        from aletheia.agent_runtime.scheduler import resume_scheduler
        resume_scheduler()
    except Exception:
        pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "agent_shell", "source": "command_center",
                 "event_type": "gateway_resumed", "message": "Agents resumed via Agent Shell"})
    return {"paused": False, "message": "Scheduled agents have been resumed."}


async def create_research_note(params: dict) -> dict:
    title = params.get("title", "Untitled Research Note")
    content = params.get("content", "")
    if not content:
        return {"error": "Cannot create a research note without content."}

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=title, content=content)
    return {"created": True, "id": item.id, "title": title}


async def create_diagnostic_todo(params: dict) -> dict:
    title = params.get("title", "Diagnostic Todo")
    content = params.get("content", title)

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=f"[DIAG] {title}", content=content,
                   metadata={"type": "diagnostic_todo"})
    return {"created": True, "id": item.id, "title": title}


async def create_improvement_proposal(params: dict) -> dict:
    title = params.get("title", "Improvement Proposal")
    content = params.get("content", title)

    from aletheia.knowledge.manager import create_research_note as _create
    item = _create(title=f"[PROPOSAL] {title}", content=content,
                   metadata={"type": "improvement_proposal"})
    return {"created": True, "id": item.id, "title": title}


HANDLERS: dict[str, callable] = {
    "get_system_status": get_system_status,
    "get_agent_activity": get_agent_activity,
    "get_market_snapshot": get_market_snapshot,
    "get_positions": get_positions,
    "explain_agent_run": explain_agent_run,
    "explain_proposal": explain_proposal,
    "pause_agents": pause_agents,
    "resume_agents": resume_agents,
    "create_research_note": create_research_note,
    "create_diagnostic_todo": create_diagnostic_todo,
    "create_improvement_proposal": create_improvement_proposal,
}
