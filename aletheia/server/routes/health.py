"""Health and status API routes."""

from fastapi import APIRouter

from aletheia import __version__
from aletheia.config.loader import load_config, save_config
from aletheia.gateway.pidfile import is_running

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health():
    return {"status": "ok", "version": __version__}


@router.get("/status")
async def status():
    cfg = load_config()
    return {
        "gateway_running": is_running(),
        "paused": cfg.runtime.paused,
        "exchange": cfg.exchange.name,
        "exchange_mode": cfg.exchange.mode,
        "live_state": cfg.live.state,
        "permission_mode": cfg.agent.permission_mode,
        "scheduler_enabled": cfg.runtime.scheduler_enabled,
        "version": __version__,
    }


@router.post("/gateway/pause")
async def pause():
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    return {"paused": True}


@router.post("/gateway/resume")
async def resume():
    cfg = load_config()
    cfg.runtime.paused = False
    save_config(cfg)
    return {"paused": False}
