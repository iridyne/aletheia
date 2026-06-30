"""Live unlock API route — requires explicit human action."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config
from aletheia.files.jsonl import write_audit

router = APIRouter(prefix="/api/settings", tags=["live"])


class LiveUnlockRequest(BaseModel):
    confirm: bool = False
    acknowledge_first_unlock: bool = False


@router.post("/live-unlock")
async def live_unlock(req: LiveUnlockRequest):
    """Unlock live-limited trading. Requires explicit confirmation."""
    if not req.confirm:
        raise HTTPException(400, "Must pass confirm=true to unlock live trading")

    cfg = load_config()
    if cfg.live.state != "live_disabled":
        return {"status": cfg.live.state, "message": "Already unlocked"}

    if not cfg.live.first_live_confirmation_done and not req.acknowledge_first_unlock:
        raise HTTPException(400, "First-time live unlock requires acknowledge_first_unlock=true")

    old_state = cfg.live.state
    old_permission_mode = cfg.operator.permission_mode
    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    # §4b: PI Agent gets full autonomy (Auto=4) when live is unlocked.
    # This is the only path that can elevate operator.permission_mode to Auto;
    # it must be symmetric with live_lock() which resets it to ReadOnly.
    cfg.operator.permission_mode = "Auto"
    save_config(cfg)

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_unlocked",
        "entity_type": "live_state",
        "before": {"state": old_state, "operator_permission_mode": old_permission_mode},
        "after": {"state": "live_limited", "operator_permission_mode": "Auto"},
        "message": "Live trading unlocked to live_limited by human confirmation; PI operator elevated to Auto",
    })

    return {"status": "live_limited", "message": "Live-limited trading enabled"}


@router.post("/live-lock")
async def live_lock():
    """Lock live trading back to disabled."""
    cfg = load_config()
    old_state = cfg.live.state
    old_permission_mode = cfg.operator.permission_mode
    cfg.live.state = "live_disabled"
    # §4b symmetry: locking live must reset PI operator back to ReadOnly so the
    # Auto authority granted by live_unlock does not persist after locking.
    cfg.operator.permission_mode = "ReadOnly"
    save_config(cfg)

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_locked",
        "entity_type": "live_state",
        "before": {"state": old_state, "operator_permission_mode": old_permission_mode},
        "after": {"state": "live_disabled", "operator_permission_mode": "ReadOnly"},
        "message": "Live trading locked; PI operator reset to ReadOnly",
    })

    return {"status": "live_disabled", "message": "Live trading disabled"}
