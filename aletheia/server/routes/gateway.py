"""Gateway autostart + restart API routes — human-triggered control.

GET  /api/gateway/autostart   — live status (read-only; honest about OS support).
POST /api/gateway/autostart   — {enabled: bool} register/remove the scheduled task.
POST /api/gateway/restart     — warm-reload uvicorn worker (or cold restart if
                                runtime.auto_reload is off). Used by the dashboard
                                "↻ Restart" button when a code-drift health issue
                                appears (build_info.code_drift_issue).
"""

import asyncio
import os
import signal
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.config.loader import load_config
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
    """Reload the running gateway so source changes take effect.

    Two paths, picked by runtime.auto_reload:
      - WARM (auto_reload=True, the default): nudge aletheia/__init__.py mtime so
        the watchfiles reloader picks it up and gracefully restarts the inner
        uvicorn worker. The supervisor process and PID file are unchanged, so the
        single-instance mutex (find_gateway_orphans / pidfile) is preserved and
        APScheduler keeps its in-memory job state.
      - COLD (auto_reload=False): SIGTERM ourselves. The supervisor's respawn
        loop (run_supervised in _gateway_runner.py) will start a fresh inner.
        Used in production / live-mode where the watcher is intentionally off.
    """
    cfg = load_config()
    warm = bool(getattr(cfg.runtime, "auto_reload", True))

    if warm:
        # Touch a file inside the watched dir so watchfiles fires. We pick
        # aletheia/__init__.py specifically because it's tiny, always exists,
        # and is unambiguously part of the package being reloaded.
        pkg_init = Path(__file__).resolve().parent.parent.parent / "__init__.py"
        try:
            pkg_init.touch()
        except Exception as e:
            return {"mode": "warm", "ok": False, "error": str(e)}
        write_audit({
            "actor": "human", "source": "dashboard", "event_type": "gateway_warm_restart",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": "Warm restart requested via dashboard (watchfiles will reload worker)",
        })
        return {"mode": "warm", "ok": True,
                "message": "Worker reload triggered. Page will reconnect in ~3s."}

    # Cold path — schedule SIGTERM AFTER returning the response, so the client
    # gets a 200 before the process dies.
    async def _delayed_kill():
        await asyncio.sleep(0.3)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except Exception:
            pass

    asyncio.create_task(_delayed_kill())
    write_audit({
        "actor": "human", "source": "dashboard", "event_type": "gateway_cold_restart",
        "entity_type": "gateway", "entity_id": "gateway",
        "message": "Cold restart requested via dashboard (auto_reload disabled — supervisor will respawn inner)",
    })
    return {"mode": "cold", "ok": True,
            "message": "Full restart in progress. Page will reconnect in ~10s."}
