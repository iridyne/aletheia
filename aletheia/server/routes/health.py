"""Health and status API routes."""

from fastapi import APIRouter

from aletheia import __version__
from aletheia.config.loader import load_config, save_config
from aletheia.gateway.pidfile import is_running

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health():
    from aletheia.agent_runtime.health import compute_health
    h = compute_health()
    return {
        "status": "healthy" if h["healthy"] else "degraded",
        "issues": h["issues"],
        "version": __version__,
    }


@router.get("/stability")
async def stability():
    """Quantified stability report — orphan counts, strategy_id drift, 24h failure
    ratio, uptime, 0-100 score. Read-only; safe for dashboards. Closes the gap
    between 'process alive' (health) and 'data internally consistent' (north-star
    stage 0). Operator can poll this through 7-14 day stage-0 run."""
    from aletheia.agent_runtime.stability_monitor import compute_stability
    return compute_stability()


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
    try:
        from aletheia.agent_runtime.scheduler import pause_scheduler
        pause_scheduler()
    except Exception:
        pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "gateway_paused",
                 "message": "Gateway paused"})
    return {"paused": True}


@router.post("/gateway/resume")
async def resume():
    cfg = load_config()
    cfg.runtime.paused = False
    save_config(cfg)
    try:
        from aletheia.agent_runtime.scheduler import resume_scheduler
        resume_scheduler()
    except Exception:
        pass
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "gateway_resumed",
                 "message": "Gateway resumed"})
    return {"paused": False}
