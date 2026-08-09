"""Runtime continuity operator routes."""

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from aletheia.config.loader import load_config
from aletheia.runtime_continuity.processes import spawn_shadow_gateway
from aletheia.runtime_continuity.service import RuntimeContinuityService, RuntimeTransitionError

router = APIRouter(prefix="/api/operator/runtime", tags=["operator-runtime"])


def _service() -> RuntimeContinuityService:
    cfg = load_config()
    shadow_gateway_launcher = None
    if cfg.runtime.runtime_continuity_enabled:
        shadow_gateway_launcher = lambda runtime: spawn_shadow_gateway(cfg, runtime)
    return RuntimeContinuityService(
        lock_ttl_sec=cfg.runtime.runtime_continuity_lock_ttl_sec,
        enabled=cfg.runtime.runtime_continuity_enabled,
        shadow_gateway_launcher=shadow_gateway_launcher,
    )


def _sse_block(event_name: str, data: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _handle_transition(fn):
    try:
        return fn()
    except RuntimeTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _with_status_fields(
    payload: dict[str, Any],
    *,
    svc: RuntimeContinuityService,
    actor: str,
    cause: str,
) -> dict[str, Any]:
    status = svc.status()
    phase = payload.get("phase") or payload.get("state") or status.get("phase")
    return {
        **payload,
        "phase": phase,
        "actor": actor,
        "cause": cause,
        "started_at": payload.get("started_at") or status.get("started_at"),
        "expires_at": status.get("expires_at"),
        "lock_owner": status.get("lock_owner"),
        "can_promote": status.get("can_promote", False),
        "can_rollback": status.get("can_rollback", False),
        "can_drain": status.get("can_drain", False),
    }


# #83 fix: request models no longer accept `actor` — audit `actor` is server-derived
# (constant "human" for operator routes; automation must use the update/continuity
# route which hardcodes actor/source itself). Callers can still supply `cause` for
# free-form context. `confirm` is now required for the state-changing transitions
# (promote, rollback, forced drain) — the routes reject without it as 400.
_ROUTE_ACTOR = "human"
_ROUTE_SOURCE = "operator_runtime_route"


class RuntimeCandidateRequest(BaseModel):
    cause: str = "operator_runtime_candidate"
    code_version: str | None = None
    config_version: str | None = None
    schema_version: str | None = None
    build_git_sha: str | None = None
    release_bundle_hash: str | None = None
    artifact_path: str | None = None
    metadata: dict[str, Any] | None = None


class RuntimeTransitionRequest(BaseModel):
    runtime_id: str | None = None
    cause: str = "operator_runtime_transition"
    force: bool = False
    confirm: bool = False


def _require_confirm(payload: RuntimeTransitionRequest, op: str) -> None:
    """#83 fix: promote / rollback / forced drain change the current-runtime pointer
    and are at least as stateful as update.apply — mirror update.update_continuity's
    confirm=True requirement. Non-forced drain stays permissive (it only observes an
    existing drain progression, no state pivot)."""
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail=f"runtime_{op}_requires_confirm",
        )


@router.get("")
async def runtime_status():
    return _service().status()


@router.post("/candidate")
async def runtime_candidate(payload: RuntimeCandidateRequest):
    svc = _service()
    return _handle_transition(
        lambda: _with_status_fields(
            svc.prepare_candidate(
                actor=_ROUTE_ACTOR,
                cause=payload.cause,
                code_version=payload.code_version,
                config_version=payload.config_version,
                schema_version=payload.schema_version,
                build_git_sha=payload.build_git_sha,
                release_bundle_hash=payload.release_bundle_hash,
                artifact_path=payload.artifact_path,
                metadata=payload.metadata,
            ),
            svc=svc,
            actor=_ROUTE_ACTOR,
            cause=payload.cause,
        )
    )


@router.post("/shadow")
async def runtime_shadow(payload: RuntimeTransitionRequest):
    svc = _service()
    runtime_id = payload.runtime_id or (svc.status().get("candidate") or {}).get("runtime_id")
    if not runtime_id:
        raise HTTPException(status_code=404, detail="candidate_runtime_missing")

    def _run():
        started = svc.start_shadow(runtime_id, actor=_ROUTE_ACTOR, cause=payload.cause)
        launch = {"attempted": False, "started": False}
        try:
            from aletheia.gateway.operator_shell import spawn_shadow_operator_shell

            proc = spawn_shadow_operator_shell(load_config(), started["runtime_id"])
            launch = {
                "attempted": True,
                "started": proc is not None,
                "pid": getattr(proc, "pid", None) if proc is not None else None,
            }
        except Exception as exc:
            launch = {"attempted": True, "started": False, "error": str(exc)}
        validated = svc.validate_shadow(started["runtime_id"], actor=_ROUTE_ACTOR, cause=payload.cause)
        validated["shadow_process"] = launch
        return _with_status_fields(validated, svc=svc, actor=_ROUTE_ACTOR, cause=payload.cause)

    return _handle_transition(_run)


@router.post("/promote")
async def runtime_promote(payload: RuntimeTransitionRequest):
    _require_confirm(payload, "promote")
    svc = _service()
    runtime_id = payload.runtime_id or (svc.status().get("candidate") or {}).get("runtime_id")
    if not runtime_id:
        raise HTTPException(status_code=404, detail="candidate_runtime_missing")
    return _handle_transition(
        lambda: _with_status_fields(
            svc.promote(runtime_id, actor=_ROUTE_ACTOR, cause=payload.cause),
            svc=svc,
            actor=_ROUTE_ACTOR,
            cause=payload.cause,
        )
    )


@router.post("/drain")
async def runtime_drain(payload: RuntimeTransitionRequest):
    if payload.force:
        _require_confirm(payload, "drain_force")
    svc = _service()
    runtime_id = payload.runtime_id or (svc.status().get("draining") or {}).get("runtime_id")
    if not runtime_id:
        raise HTTPException(status_code=404, detail="draining_runtime_missing")
    return _handle_transition(
        lambda: _with_status_fields(
            svc.drain(runtime_id, actor=_ROUTE_ACTOR, cause=payload.cause, force=payload.force),
            svc=svc,
            actor=_ROUTE_ACTOR,
            cause=payload.cause,
        )
    )


@router.post("/rollback")
async def runtime_rollback(payload: RuntimeTransitionRequest):
    _require_confirm(payload, "rollback")
    svc = _service()
    return _handle_transition(
        lambda: _with_status_fields(
            svc.rollback(actor=_ROUTE_ACTOR, cause=payload.cause),
            svc=svc,
            actor=_ROUTE_ACTOR,
            cause=payload.cause,
        )
    )


@router.get("/events")
async def runtime_events(limit: int = 100):
    return _service().list_events(limit=limit)


@router.get("/events/stream")
async def runtime_events_stream(limit: int = 100, once: bool = False):
    async def _stream():
        # See operator.py: rowid cursor instead of an ever-growing seen_ids set.
        idle_poll_secs = 1.0
        max_idle_poll_secs = 8.0
        backlog, cursor = _service().list_events_after_rowid(None, limit=limit)
        for event in backlog:
            yield _sse_block("runtime_event", event)
        if once:
            return

        while True:
            await asyncio.sleep(idle_poll_secs)
            fresh, cursor = _service().list_events_after_rowid(cursor, limit=limit)
            for event in fresh:
                yield _sse_block("runtime_event", event)
            if not fresh:
                idle_poll_secs = min(max_idle_poll_secs, idle_poll_secs * 2)
                yield ": keep-alive\n\n"
            else:
                idle_poll_secs = 1.0

    return StreamingResponse(_stream(), media_type="text/event-stream")
