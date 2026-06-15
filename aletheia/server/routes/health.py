"""Health and status API routes."""

import logging
import os
import time

from fastapi import APIRouter, HTTPException

from aletheia import __version__
from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api", tags=["system"])

_boot_time = time.time()
logger = logging.getLogger("aletheia.routes.health")


@router.get("/health")
async def health():
    from aletheia.agent_runtime.health import compute_health
    h = compute_health()
    return {
        "status": "healthy" if h["healthy"] else "degraded",
        "issues": h["issues"],
        "version": __version__,
    }


@router.get("/ready")
async def ready():
    """Cheap liveness probe for the dashboard reconnect guard.

    This intentionally avoids DB/config reads: if this route responds, the HTTP
    worker is alive even if deeper health is degraded.
    """
    return {
        "ok": True,
        "version": __version__,
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
    }


@router.get("/version")
async def version():
    from aletheia.agent_runtime.build_info import get_boot_commit
    return {
        "version": __version__,
        "boot_commit": get_boot_commit(),
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
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
    try:
        from aletheia.agent_runtime.health import compute_health
        h = compute_health()
    except Exception as e:
        logger.warning("status health rollup failed: %s", e)
        h = {"healthy": False, "issues": [f"health_check_error:{type(e).__name__}"]}

    try:
        from aletheia.agent_runtime.scheduler import status_snapshot
        sched = status_snapshot()
    except Exception as e:
        logger.warning("status scheduler snapshot failed: %s", e)
        sched = {"running": False, "state": f"error:{type(e).__name__}", "job_count": 0}

    try:
        from aletheia.agent_runtime.build_info import get_boot_commit
        boot_commit = get_boot_commit()
    except Exception:
        boot_commit = None

    try:
        from aletheia.update.git_ops import current_commit
        head_commit = current_commit() or None
    except Exception:
        head_commit = None

    return {
        "gateway_running": True,  # if we can respond, we are running
        "paused": cfg.runtime.paused,
        "exchange": cfg.exchange.name,
        "exchange_mode": cfg.exchange.mode,
        "live_state": cfg.live.state,
        "permission_mode": cfg.agent.permission_mode,
        "scheduler_enabled": cfg.runtime.scheduler_enabled,
        "scheduler_running": sched["running"],
        "scheduler_state": sched["state"],
        "scheduler_job_count": sched["job_count"],
        "model_provider": cfg.models.default_provider,
        "health_status": "healthy" if h["healthy"] else "degraded",
        "health_issues": h["issues"],
        "boot_commit": boot_commit,
        "current_commit": head_commit,
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
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
    from aletheia.agent_runtime import resume_gate

    result = await resume_gate.resume_if_llm_available(
        actor="human",
        source="dashboard",
        cause="gateway_resume_api",
    )
    if not result["allowed"]:
        raise HTTPException(status_code=409, detail=result)
    from aletheia.files.jsonl import write_audit
    write_audit({"actor": "human", "source": "dashboard", "event_type": "gateway_resumed",
                 "message": "Gateway resumed"})
    return {"paused": False}
