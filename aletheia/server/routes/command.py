"""Command Center API — structured command/query interface for the dashboard."""

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.files.jsonl import write_audit
from aletheia.gateway.pidfile import is_running

router = APIRouter(prefix="/api", tags=["command"])


class CommandRequest(BaseModel):
    text: str
    context: dict | None = None


class CommandAction(BaseModel):
    label: str
    action: str
    params: dict | None = None


class CommandResponse(BaseModel):
    type: str  # answer | command_result | rejected
    message: str
    cards: list[dict] | None = None
    actions: list[dict] | None = None


FORBIDDEN_PATTERNS = ["enable live", "bypass risk", "disable risk", "force trade", "delete all"]


@router.post("/command")
async def handle_command(req: CommandRequest) -> dict:
    text = req.text.strip().lower()

    for pattern in FORBIDDEN_PATTERNS:
        if pattern in text:
            write_audit({
                "actor": "human", "source": "command_center",
                "event_type": "command_rejected",
                "entity_type": "command", "entity_id": text[:50],
                "message": f"Rejected forbidden command: {text[:100]}",
            })
            return {
                "type": "rejected",
                "message": f"Command rejected: '{pattern}' is not allowed via Command Center.",
                "cards": None,
                "actions": None,
            }

    if text in ("status", "/status", "what is the status", "system status"):
        return _handle_status()

    if text in ("pause", "pause all", "pause agents", "pause all agents"):
        return await _handle_pause()

    if text in ("resume", "resume all", "resume agents", "resume all agents"):
        return await _handle_resume()

    if text.startswith("latest") or text.startswith("what") or text.startswith("show"):
        return _handle_query(req.text)

    return {
        "type": "answer",
        "message": f"I understood your input: \"{req.text}\". Full natural language processing will be available in a future update. For now, try: status, pause, resume.",
        "cards": None,
        "actions": [
            {"label": "Status", "action": "status"},
            {"label": "Pause", "action": "pause"},
            {"label": "Resume", "action": "resume"},
        ],
    }


def _handle_status() -> dict:
    cfg = load_config()
    running = is_running()
    return {
        "type": "command_result",
        "message": "System status retrieved.",
        "cards": [{
            "card_type": "status",
            "title": "System Status",
            "fields": {
                "gateway": "running" if running else "stopped",
                "paused": cfg.runtime.paused,
                "exchange": f"{cfg.exchange.name} ({cfg.exchange.mode})",
                "permission_mode": cfg.agent.permission_mode,
                "live_state": cfg.live.state,
            },
        }],
        "actions": [
            {"label": "Pause", "action": "pause"},
            {"label": "Resume", "action": "resume"},
        ],
    }


async def _handle_pause() -> dict:
    cfg = load_config()
    if cfg.runtime.paused:
        return {"type": "command_result", "message": "System is already paused.", "cards": None, "actions": None}

    from aletheia.config.loader import save_config
    cfg.runtime.paused = True
    save_config(cfg)
    write_audit({
        "actor": "human", "source": "command_center",
        "event_type": "gateway_paused",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": "Paused via Command Center",
    })
    return {
        "type": "command_result",
        "message": "All agents paused.",
        "cards": [{"card_type": "status", "title": "Paused", "fields": {"state": "paused"}}],
        "actions": [{"label": "Resume", "action": "resume"}],
    }


async def _handle_resume() -> dict:
    cfg = load_config()
    if not cfg.runtime.paused:
        return {"type": "command_result", "message": "System is already running.", "cards": None, "actions": None}

    from aletheia.config.loader import save_config
    cfg.runtime.paused = False
    save_config(cfg)
    write_audit({
        "actor": "human", "source": "command_center",
        "event_type": "gateway_resumed",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": "Resumed via Command Center",
    })
    return {
        "type": "command_result",
        "message": "All agents resumed.",
        "cards": [{"card_type": "status", "title": "Resumed", "fields": {"state": "running"}}],
        "actions": [{"label": "Pause", "action": "pause"}],
    }


def _handle_query(text: str) -> dict:
    from aletheia.storage import repositories as repo
    from aletheia.storage.migrations import run_migrations
    run_migrations()

    cards = []
    runs = repo.list_agent_runs(limit=3)
    if runs:
        cards.append({
            "card_type": "agent_runs",
            "title": "Recent Agent Runs",
            "items": [{"agent": r.get("agent_name"), "status": r.get("status"), "time": r.get("started_at")} for r in runs],
        })

    proposals = repo.list_trade_proposals(limit=3)
    if proposals:
        cards.append({
            "card_type": "proposals",
            "title": "Recent Proposals",
            "items": [{"symbol": p.get("symbol"), "direction": p.get("direction"), "status": p.get("status")} for p in proposals],
        })

    return {
        "type": "answer",
        "message": f"Here's what's recent in the system:",
        "cards": cards if cards else None,
        "actions": None,
    }
