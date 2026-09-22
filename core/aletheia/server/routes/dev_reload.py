"""Dev-reload API routes — only mounted when ALETHEIA_DEV_RELOAD=1.

GET  /api/dev/reload/status  — current reload state
GET  /api/dev/reload/events  — SSE stream (frontend-built / backend-ready)
"""

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/dev/reload", tags=["dev-reload"])

_static_dir = Path(__file__).resolve().parent.parent.parent / "dashboard_static"
_manifest_path = _static_dir / ".vite" / "manifest.json"

_last_manifest_mtime: float = 0.0
_event_id: int = 0


@router.get("/status")
async def reload_status():
    from aletheia.server.dev_reload import get_status
    return get_status()


@router.get("/events")
async def reload_events():
    """SSE stream: emits frontend-built when dashboard_static is rebuilt,
    and backend-ready on reconnect (client can use this as a health signal)."""

    async def _generate():
        global _last_manifest_mtime, _event_id
        # Emit backend-ready on first connect
        _event_id += 1
        yield f"id: {_event_id}\nevent: backend-ready\ndata: {json.dumps({'ts': time.time()})}\n\n"

        # Seed with current mtime
        if _manifest_path.exists():
            _last_manifest_mtime = _manifest_path.stat().st_mtime

        while True:
            await asyncio.sleep(0.5)
            try:
                if _manifest_path.exists():
                    mtime = _manifest_path.stat().st_mtime
                    if mtime > _last_manifest_mtime:
                        _last_manifest_mtime = mtime
                        _event_id += 1
                        data = json.dumps({"type": "frontend-built", "mtime": mtime})
                        yield f"id: {_event_id}\nevent: frontend-built\ndata: {data}\n\n"
            except (OSError, IOError):
                pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
