"""Health and status API routes."""

import logging
import os
from pathlib import Path
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from aletheia import __version__
from aletheia.config.loader import load_config, save_config
from aletheia.infra.events import list_infra_events

router = APIRouter(prefix="/api", tags=["system"])
infra_router = APIRouter(prefix="/_infra", tags=["infra"])

_boot_time = time.time()
logger = logging.getLogger("aletheia.routes.health")
_health_cache_lock = threading.Lock()
_health_cache: tuple[float, int, dict] | None = None


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


def _llm_model_health_snapshot() -> list[dict]:
    try:
        from aletheia.model_provider.fallback import get_model_health_snapshot
        return get_model_health_snapshot()
    except Exception as e:  # noqa: BLE001 - status endpoint must stay alive
        logger.debug("llm model health snapshot failed: %s", e)
        return []


_head_commit_cache: "tuple[float, str | None] | None" = None
_head_commit_lock = threading.Lock()


def _cached_head_commit(ttl_sec: float = 60.0) -> str | None:
    """Short TTL cache for `git rev-parse HEAD`.

    #6 fix: /api/status polls run every 10s; each one used to shell out to git
    on the event loop. HEAD only changes on restart / self-update, so a 60s
    cache is safe. On a self-update the app restarts anyway (invalidating this
    cache), and the boot_commit field next to head_commit already captures the
    startup value for reference. Returns None on git failure (never raises).
    """
    global _head_commit_cache
    now = time.monotonic()
    with _head_commit_lock:
        if _head_commit_cache is not None:
            ts, cached = _head_commit_cache
            if now - ts < ttl_sec:
                return cached
    try:
        from aletheia.update.git_ops import current_commit
        commit = current_commit() or None
    except Exception:
        commit = None
    with _head_commit_lock:
        _head_commit_cache = (now, commit)
    return commit


def _cached_compute_health(ttl_sec: float = 5.0) -> dict:
    """Short TTL cache for dashboard polling.

    The health rollup touches SQLite and several in-process monitors. Keeping a
    5s cache preserves honest status while preventing concurrent UI polls from
    repeating the same blocking work.
    """
    global _health_cache
    from aletheia.agent_runtime import health as health_mod

    fn = health_mod.compute_health
    fn_id = id(fn)
    now = time.monotonic()
    with _health_cache_lock:
        if _health_cache is not None:
            ts, cached_fn_id, cached = _health_cache
            if cached_fn_id == fn_id and now - ts < ttl_sec:
                return {
                    "healthy": bool(cached.get("healthy")),
                    "status": cached.get("status", "healthy" if cached.get("healthy") else "degraded"),
                    "issues": list(cached.get("issues") or []),
                }

    h = fn()
    snapshot = {
        "healthy": bool(h.get("healthy")),
        "status": h.get("status", "healthy" if h.get("healthy") else "degraded"),
        "issues": list(h.get("issues") or []),
    }
    with _health_cache_lock:
        _health_cache = (now, fn_id, snapshot)
    return {"healthy": snapshot["healthy"], "status": snapshot["status"], "issues": list(snapshot["issues"])}


@router.get("/health")
def health():
    h = _cached_compute_health()
    return {
        "status": h["status"],
        "issues": h["issues"],
        "version": __version__,
    }


@router.get("/infra/events")
def infra_events(limit: int = 20, min_severity: str | None = None):
    """Recent sanitized infra/runtime events for dashboard status surfaces."""
    return {
        "events": list_infra_events(limit=limit, min_severity=min_severity),
    }


@router.get("/ready")
async def ready():
    """Cheap liveness probe for the dashboard reconnect guard.

    This intentionally avoids DB/config reads: if this route responds, the HTTP
    event loop is alive even if deeper health or the sync-worker pool is degraded.
    """
    from aletheia.server.responsiveness import responsiveness_snapshot

    return {
        **_liveness_payload(),
        "responsiveness": responsiveness_snapshot(),
    }


@infra_router.get("/livez")
async def infra_livez():
    """Infra liveness probe: process + HTTP loop are alive.

    No DB/config/health rollup is read here. This endpoint is deliberately safe
    to use from watchdogs, future front-door routing, and frontend reconnect
    guards without conflating degraded business health with process death.
    """
    from aletheia.server.responsiveness import responsiveness_snapshot

    return {
        **_liveness_payload(),
        "responsiveness": responsiveness_snapshot(),
    }


@infra_router.get("/readyz")
def infra_readyz():
    """Infra readiness probe: API is serving and dashboard static is present.

    Degraded trading/model/market health stays visible on `/api/health`; it does
    not make this probe fail. A missing dashboard does fail readiness because a
    user would otherwise get a misleading API-only local UI.
    """
    status_code, payload = _readiness_payload()
    return JSONResponse(status_code=status_code, content=payload)


@infra_router.get("/status")
def infra_status():
    """Cheap infra snapshot for diagnostics and future stable front-door use."""
    cfg = load_config()
    runtime_port = os.environ.get("ALETHEIA_RUNTIME_PORT")
    try:
        effective_port = int(runtime_port) if runtime_port else cfg.app.dashboard_port
    except ValueError:
        effective_port = cfg.app.dashboard_port
    from aletheia.gateway.network import frontdoor_bind_hosts, local_frontdoor_host
    status_code, readiness = _readiness_payload()
    return JSONResponse(
        status_code=status_code,
        content={
            **readiness,
            "host": local_frontdoor_host(),
            "remote_control_hosts": frontdoor_bind_hosts(cfg) if cfg.remote_control.enabled else [],
            "port": effective_port,
            "runtime_mode": os.environ.get("ALETHEIA_RUNTIME_MODE", "current"),
            "runtime_state": os.environ.get("ALETHEIA_RUNTIME_STATE", "current"),
            "runtime_id": os.environ.get("ALETHEIA_RUNTIME_ID", "runtime-local-current"),
        },
    )


@infra_router.get("/doctor")
def infra_doctor():
    """Machine-readable doctor report for AI agents / scripts querying the LIVE
    gateway (no CLI subprocess needed). Same JSON contract as `aletheia doctor --json`.

    Pure read — build_report() changes nothing. Declared as a sync def so FastAPI
    runs the blocking probes (SQLite, git, localhost HTTP) in a threadpool rather
    than stalling the event loop.
    """
    from aletheia.diagnostics.doctor import build_report
    return build_report().to_dict()


@router.get("/version")
def version():
    from aletheia.agent_runtime.build_info import get_boot_commit
    return {
        "version": __version__,
        "boot_commit": get_boot_commit(),
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
    }


@router.get("/stability")
def stability():
    """Quantified stability report — orphan counts, strategy_id drift, 24h failure
    ratio, uptime, 0-100 score. Read-only; safe for dashboards. Closes the gap
    between 'process alive' (health) and 'data internally consistent' (north-star
    stage 0). Operator can poll this through 7-14 day stage-0 run."""
    from aletheia.agent_runtime.stability_monitor import compute_stability
    return compute_stability()


@router.get("/status")
def status():
    cfg = load_config()
    from aletheia.agent_runtime.pause_ownership import active_pause_owners

    paper_execution_mode = cfg.exchange.paper_execution_mode
    uses_external_exchange = paper_execution_mode != "paper_internal"
    from aletheia.config.exchange_alignment import exchange_alignment
    alignment = exchange_alignment(cfg)
    try:
        h = _cached_compute_health()
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
        from aletheia.agent_runtime.connectivity import get_status as connectivity_status

        external_connectivity = connectivity_status().get("external_connectivity")
    except Exception as e:
        logger.debug("status connectivity snapshot failed: %s", e)
        external_connectivity = {
            "state": "unknown",
            "severity": "info",
            "priority": 3,
            "message": "External connectivity status is unavailable.",
            "action": "Wait for the next connectivity probe.",
            "observed_at": None,
        }

    # #6 fix: /api/status is polled by the topbar every 10s. `current_commit()`
    # shells out to `git rev-parse HEAD` which blocks the event loop while the
    # subprocess runs. In a heavy-scheduler moment that ~30ms shell has been
    # observed to spike to 500ms+ and starve other async tasks. HEAD only
    # changes on restart / self-update, so a 60s TTL cache is more than fresh
    # enough — the boot_commit next to it never changes at all.
    head_commit = _cached_head_commit()

    return {
        "gateway_running": True,  # if we can respond, we are running
        "paused": cfg.runtime.paused,
        "pause_owners": active_pause_owners(cfg),
        "exchange": cfg.exchange.name,
        "exchange_mode": cfg.exchange.mode,
        "paper_execution_mode": paper_execution_mode,
        "execution_target": "external_exchange" if uses_external_exchange else "local_paper",
        "uses_external_exchange": uses_external_exchange,
        "execution_exchange": alignment.execution_exchange,
        "market_data_exchange": alignment.data_exchange,
        "exchange_alignment": alignment.status,
        "exchange_diverged": alignment.diverged,
        "live_state": cfg.live.state,
        "permission_mode": cfg.agent.permission_mode,
        "scheduler_enabled": cfg.runtime.scheduler_enabled,
        "scheduler_running": sched["running"],
        "scheduler_state": sched["state"],
        "scheduler_job_count": sched["job_count"],
        "model_provider": cfg.models.default_provider,
        "external_connectivity": external_connectivity,
        "health_status": h.get("status", "healthy" if h["healthy"] else "degraded"),
        "health_issues": h["issues"],
        "infra_events": list_infra_events(limit=5, min_severity="warning"),
        "llm_model_health": _llm_model_health_snapshot(),
        "boot_commit": boot_commit,
        "current_commit": head_commit,
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
        "version": __version__,
    }


@router.post("/gateway/pause")
def pause():
    from aletheia.agent_runtime.pause_ownership import MANUAL_OWNER, acquire_pause

    acquire_pause(
        MANUAL_OWNER,
        reason="dashboard_pause",
        actor="human",
        source="dashboard",
    )
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
