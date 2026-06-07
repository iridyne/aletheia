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
    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    save_config(cfg)

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_unlocked",
        "entity_type": "live_state",
        "before": {"state": old_state},
        "after": {"state": "live_limited"},
        "message": "Live trading unlocked to live_limited by human confirmation",
    })

    return {"status": "live_limited", "message": "Live-limited trading enabled"}


@router.post("/live-lock")
async def live_lock():
    """Lock live trading back to disabled."""
    cfg = load_config()
    old_state = cfg.live.state
    cfg.live.state = "live_disabled"
    save_config(cfg)

    write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_locked",
        "entity_type": "live_state",
        "before": {"state": old_state},
        "after": {"state": "live_disabled"},
        "message": "Live trading locked",
    })

    return {"status": "live_disabled", "message": "Live trading disabled"}
