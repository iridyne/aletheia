"""Gateway autostart + restart API routes — human-triggered control.

GET  /api/gateway/autostart   — live status (read-only; honest about OS support).
POST /api/gateway/autostart   — {enabled: bool} register/remove the scheduled task.
POST /api/gateway/restart     — kill inner uvicorn so supervisor respawns with fresh code.
"""

import asyncio
import os

from fastapi import APIRouter
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
async def restart_gateway():
    """Terminate the uvicorn process tree so the supervisor respawns fresh.

    The supervisor (run_supervised in _gateway_runner.py) owns proc.wait() on
    the uvicorn subprocess. When that subprocess exits non-zero, the supervisor
    respawns it — loading fresh code from disk. We schedule os._exit after a
    short delay so the HTTP response reaches the client first.
    """
    from aletheia.gateway.pidfile import read_pid

    supervisor_pid = read_pid()
    if not supervisor_pid:
        return {"mode": "cold", "ok": False, "message": "No supervisor PID found"}

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
