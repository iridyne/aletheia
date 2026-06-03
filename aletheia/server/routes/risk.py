"""Risk API routes — kill switch + risk boundaries display (guide §21, §23.12)."""

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.risk import kill_switch

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/boundaries")
async def get_risk_boundaries():
    """Return the hard risk limits for display. Read-only over the API."""
    return load_config().risk.model_dump()


@router.get("/kill-switch")
async def kill_switch_status():
    return {"active": kill_switch.is_active()}


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""


@router.post("/kill-switch")
async def set_kill_switch(req: KillSwitchRequest):
    """Engage or release the global trading brake (human action)."""
    if req.active:
        kill_switch.activate(reason=req.reason, actor="human")
    else:
        kill_switch.deactivate(actor="human")
    return {"active": kill_switch.is_active()}
