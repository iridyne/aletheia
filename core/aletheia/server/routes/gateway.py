"""Gateway restart API route — human-triggered control."""

import asyncio
import os
from typing import Optional

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel

from aletheia.config.loader import is_preview_mode
from aletheia.files.jsonl import write_audit

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


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
                "message": "Gateway lifecycle control is unavailable in preview mode.",
            },
        )


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
    - Without --reload: a controlled restart (#1454) runs the graceful lifespan
      shutdown, then exits 42; the supervisor respawns it outside the crash budget.
    """
    _reject_preview_gateway_control()
    has_supervisor = not getattr(request.app.state, "_owns_pid", True)
    reload_mode = _is_reload_mode()

    if not reload_mode and not has_supervisor:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=409,
            detail={
                "error": "gateway_restart_supervisor_unavailable",
                "reason_code": "supervisor_unavailable",
                "message": "No supervisor active — restart would kill the gateway permanently.",
            },
        )

    if reload_mode:
        from aletheia.server.dev_reload import is_dev_reload_enabled

        # The env var alone is not the gate (DECISIONS: dev reload requires
        # ALETHEIA_DEV_RELOAD=1 AND live_disabled, re-read now — live may have been
        # enabled after boot). Never fall through to a process exit under --reload.
        if not is_dev_reload_enabled():
            from fastapi import HTTPException

            raise HTTPException(
                status_code=409,
                detail={
                    "error": "dev_reload_blocked",
                    "reason_code": "dev_reload_blocked",
                    "message": "Dev reload is blocked: live trading is not disabled or config is unreadable.",
                },
            )

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

    async def _delayed_restart():
        await asyncio.sleep(0.5)  # let this response reach the client first
        from aletheia.server.controlled_restart import request_controlled_restart

        request_controlled_restart()

    # Keep a strong reference so the pending task cannot be collected.
    request.app.state.controlled_restart_task = asyncio.create_task(_delayed_restart())
    return {"mode": "cold", "ok": True,
            "message": "Restarting. Page will reconnect in ~10s."}
