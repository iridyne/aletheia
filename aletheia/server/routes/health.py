"""Health and status API routes."""

import logging
import os
from pathlib import Path
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from aletheia import __version__
from aletheia.config.loader import load_config, save_config

router = APIRouter(prefix="/api", tags=["system"])
infra_router = APIRouter(prefix="/_infra", tags=["infra"])

_boot_time = time.time()
logger = logging.getLogger("aletheia.routes.health")


def _uptime_sec() -> float:
    return round(time.time() - _boot_time, 1)


def _liveness_payload() -> dict:
    return {
        "ok": True,
        "version": __version__,
        "pid": os.getpid(),
        "uptime_sec": _uptime_sec(),
    }


def _dashboard_static_dir() -> Path:
    return Path(__file__).resolve().parent.parent.parent / "dashboard_static"


def _static_dashboard_ready() -> tuple[bool, Path]:
    static_dir = _dashboard_static_dir()
    return (static_dir.exists() and (static_dir / "index.html").exists(), static_dir)


def _readiness_payload() -> tuple[int, dict]:
    static_index, static_dir = _static_dashboard_ready()
    ok = bool(static_index)
    status_code = 200 if ok else 503
    payload = {
        **_liveness_payload(),
        "ok": ok,
        "api": True,
        "static_index": static_index,
        "static_dir": str(static_dir),
    }
    return status_code, payload


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
    return _liveness_payload()


@infra_router.get("/livez")
async def infra_livez():
    """Infra liveness probe: process + HTTP loop are alive.

    No DB/config/health rollup is read here. This endpoint is deliberately safe
    to use from watchdogs, future front-door routing, and frontend reconnect
    guards without conflating degraded business health with process death.
    """
    return _liveness_payload()


@infra_router.get("/readyz")
async def infra_readyz():
    """Infra readiness probe: API is serving and dashboard static is present.

    Degraded trading/model/market health stays visible on `/api/health`; it does
    not make this probe fail. A missing dashboard does fail readiness because a
    user would otherwise get a misleading API-only local UI.
    """
    status_code, payload = _readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)


@infra_router.get("/status")
async def infra_status():
    """Cheap infra snapshot for diagnostics and future stable front-door use."""
    cfg = load_config()
    status_code, readiness = _readiness_payload()
    return JSONResponse(
        status_code=status_code,
        content={
            **readiness,
            "host": cfg.app.bind_host,
            "port": cfg.app.dashboard_port,
            "runtime_mode": os.environ.get("ALETHEIA_RUNTIME_MODE", "current"),
            "runtime_state": os.environ.get("ALETHEIA_RUNTIME_STATE", "current"),
            "runtime_id": os.environ.get("ALETHEIA_RUNTIME_ID", "runtime-local-current"),
        },
    )


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
