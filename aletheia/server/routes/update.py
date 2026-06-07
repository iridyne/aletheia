"""Self-upgrade API routes — human-triggered code update (guide: not a daemon).

GET /api/update/status   — check whether an update is available (read-only fetch).
POST /api/update/apply   — pull latest, reinstall deps if needed, signal restart.

These call the same applier the CLI uses. They are NOT agent-callable tools.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.files.jsonl import write_audit
from aletheia.update import applier

router = APIRouter(prefix="/api/update", tags=["update"])


@router.get("/status")
async def update_status():
    return applier.check_for_update()


class ApplyBody(BaseModel):
    confirm: bool = False


@router.post("/apply")
async def update_apply(body: ApplyBody = ApplyBody()):
    if not body.confirm:
        return {"applied": False, "reason": "confirmation_required",
                "message": "Set confirm=true to apply the update."}
    write_audit({
        "actor": "human", "source": "api", "event_type": "code_update_requested",
        "entity_type": "system_update", "entity_id": "apply",
        "message": "Human confirmed code update via API",
    })
    return applier.apply_update()
