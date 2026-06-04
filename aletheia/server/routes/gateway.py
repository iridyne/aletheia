"""Gateway autostart API routes — human-triggered Windows Task Scheduler control.

GET  /api/gateway/autostart   — live status (read-only; honest about OS support).
POST /api/gateway/autostart   — {enabled: bool} register/remove the scheduled task.

These call the same autostart helpers the CLI uses. They are NOT agent-callable
tools. The task runs the SAME gated `gateway start` a human runs.
"""

from fastapi import APIRouter
from pydantic import BaseModel

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
        return autostart.enable(source="api")
    return autostart.disable(source="api")
