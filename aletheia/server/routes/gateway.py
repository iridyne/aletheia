"""Gateway autostart + restart API routes — human-triggered control.

GET  /api/gateway/autostart   — live status (read-only; honest about OS support).
POST /api/gateway/autostart   — {enabled: bool} register/remove the scheduled task.
POST /api/gateway/restart     — kill inner uvicorn so supervisor respawns with fresh code.
"""

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel

from aletheia.files.jsonl import write_audit
from aletheia.gateway import autostart

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class AutostartRequest(BaseModel):
    enabled: bool


class RestartRequest(BaseModel):
    reason: str = "manual"
    changed_files: list[str] = []


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


def _is_reload_mode() -> bool:
    """True if running inside uvicorn's --reload worker process."""
    return os.environ.get("ALETHEIA_DEV_RELOAD") == "1"


def _trigger_reload_via_touch() -> None:
    """Touch a sentinel .py file so uvicorn's file-watcher triggers a reload.

    Under --reload mode, os._exit(42) kills the worker but the reloader process
    doesn't notice (it only respawns on file changes). Touching a watched .py
    file makes the reloader detect a change and do a proper worker restart."""
    from pathlib import Path
    sentinel = Path(__file__).resolve().parent.parent / "_reload_sentinel.py"
    sentinel.write_text(f"# touched by restart endpoint at {__import__('time').time()}\n")


@router.post("/restart")
async def restart_gateway(request: Request, body: Optional[RestartRequest] = Body(default=None)):
    """Restart the gateway worker.

    Strategy depends on the runtime mode:
    - Under --reload (dev mode): touch a sentinel .py file to trigger uvicorn's
      file-watcher reload. Worker stays alive until the reloader kills it cleanly.
    - Without --reload: os._exit(42) kills this process; the supervisor or
      autostart watchdog respawns fresh.
    """
    has_supervisor = not getattr(request.app.state, "_owns_pid", True)
    has_autostart = autostart.status().get("enabled", False)
    reload_mode = _is_reload_mode()

    if not reload_mode and not has_supervisor and not has_autostart:
        return {"mode": "cold", "ok": False,
                "message": "No supervisor or autostart active — restart would kill the gateway permanently."}

    reason = body.reason if body else "manual"
    changed = body.changed_files[:10] if body and body.changed_files else []
    source = "dev_watcher" if reason == "file_change" else "dashboard"

    write_audit({
        "actor": "human", "source": source, "event_type": "gateway_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": f"Restart requested ({reason})",
        "after": {"reason": reason, "changed_files": changed} if changed else {"reason": reason},
    })

    if reload_mode:
        _trigger_reload_via_touch()
        return {"mode": "warm", "ok": True,
                "message": "Reload triggered. Worker restarting in ~1s."}

    async def _delayed_exit():
        await asyncio.sleep(0.5)
        os._exit(42)

    asyncio.create_task(_delayed_exit())
    return {"mode": "cold", "ok": True,
            "message": "Restarting. Page will reconnect in ~10s."}
