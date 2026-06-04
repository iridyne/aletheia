"""Self-upgrade API routes — human-triggered code update (guide: not a daemon).

GET /api/update/status   — check whether an update is available (read-only fetch).
POST /api/update/apply   — pull latest, reinstall deps if needed, signal restart.

These call the same applier the CLI uses. They are NOT agent-callable tools.
"""

from fastapi import APIRouter

from aletheia.update import applier

router = APIRouter(prefix="/api/update", tags=["update"])


@router.get("/status")
async def update_status():
    return applier.check_for_update()


@router.post("/apply")
async def update_apply():
    return applier.apply_update()
