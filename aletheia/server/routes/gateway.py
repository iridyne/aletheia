"""Gateway autostart + restart API routes — human-triggered control.

GET  /api/gateway/autostart   — live status (read-only; honest about OS support).
POST /api/gateway/autostart   — {enabled: bool} register/remove the scheduled task.
POST /api/gateway/restart     — kill inner uvicorn so supervisor respawns with fresh code.
"""

import asyncio
import os

from fastapi import APIRouter, Request
from pydantic import BaseModel

from aletheia.files.jsonl import write_audit
from aletheia.gateway import autostart

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class AutostartRequest(BaseModel):
    enabled: bool


@router.get("/autostart")
async def get_autostart():
    return autostart.status()


@router.post("/autostart")
async def set_autostart(req: AutostartRequest):
    if req.enabled:
        result = autostart.enable(source="api")
    else:
        result = autostart.disable(source="api")
    write_audit({
        "actor": "human", "source": "api", "event_type": "autostart_changed",
        "entity_type": "gateway_autostart", "entity_id": "global",
        "after": {"enabled": req.enabled},
        "message": f"Gateway autostart {'enabled' if req.enabled else 'disabled'} via API",
    })
    return result


@router.post("/restart")
async def restart_gateway(request: Request):
    """Terminate the uvicorn process so the supervisor (or watchdog/autostart) respawns fresh.

    os._exit(42) kills this process unconditionally. If a supervisor is running,
    it sees a non-zero exit and respawns. If running foreground or bare, the
    autostart scheduled task or manual start will bring it back.
    """
    has_supervisor = not getattr(request.app.state, "_owns_pid", True)
    has_autostart = autostart.status().get("enabled", False)

    if not has_supervisor and not has_autostart:
        return {"mode": "cold", "ok": False,
                "message": "No supervisor or autostart active — restart would kill the gateway permanently."}

    async def _delayed_exit():
        await asyncio.sleep(0.5)
        os._exit(42)

    asyncio.create_task(_delayed_exit())
    write_audit({
        "actor": "human", "source": "dashboard", "event_type": "gateway_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": "Restart requested via dashboard",
    })
    return {"mode": "cold", "ok": True,
            "message": "Restarting. Page will reconnect in ~10s."}
