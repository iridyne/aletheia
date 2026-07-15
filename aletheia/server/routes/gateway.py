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

from aletheia.config.loader import is_preview_mode
from aletheia.files.jsonl import write_audit
from aletheia.gateway import autostart

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class AutostartRequest(BaseModel):
    enabled: bool


class RestartRequest(BaseModel):
    reason: str = "manual"
    changed_files: list[str] = []


def _reject_preview_gateway_control() -> None:
    if is_preview_mode():
        from fastapi import HTTPException
        raise HTTPException(
            403,
            detail={
                "reason_code": "preview_gateway_control_blocked",
                "message": "Gateway lifecycle and autostart control are unavailable in preview mode.",
            },
        )


@router.get("/autostart")
async def get_autostart():
    return autostart.status()


@router.post("/autostart")
async def set_autostart(req: AutostartRequest):
    _reject_preview_gateway_control()
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
    """Touch the sentinel .py file so uvicorn's file-watcher triggers a reload."""
    from aletheia.server.dev_reload import touch_reload_sentinel
    touch_reload_sentinel("restart_endpoint")


def _restart_audit_actor_source(reason: str) -> tuple[str, str]:
    """Map a restart trigger to (actor, source). Automated callers (git post-merge
    hook, dev file-watcher) must NOT be recorded as actor='human' (#132)."""
    if reason == "git_hook":
        return "system", "git_hook"
    if reason == "file_change":
        return "dev_watcher", "dev_watcher"
    return "human", "dashboard"


@router.post("/restart")
async def restart_gateway(request: Request, body: Optional[RestartRequest] = Body(default=None)):
    """Restart the gateway worker.

    Strategy depends on the runtime mode:
    - Under --reload (dev mode): touch a sentinel .py file to trigger uvicorn's
      file-watcher reload. Worker stays alive until the reloader kills it cleanly.
    - Without --reload: os._exit(42) kills this process; the supervisor or
      autostart watchdog respawns fresh.
    """
    _reject_preview_gateway_control()
    has_supervisor = not getattr(request.app.state, "_owns_pid", True)
    has_autostart = autostart.status().get("enabled", False)
    reload_mode = _is_reload_mode()

    if not reload_mode and not has_supervisor and not has_autostart:
        return {"mode": "cold", "ok": False,
                "message": "No supervisor or autostart active — restart would kill the gateway permanently."}

    reason = body.reason if body else "manual"
    changed = body.changed_files[:10] if body and body.changed_files else []
    # Derive actor/source from the trigger, not a hardcoded "human" (#132).
    actor, source = _restart_audit_actor_source(reason)

    write_audit({
        "actor": actor, "source": source, "event_type": "gateway_restart",
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
