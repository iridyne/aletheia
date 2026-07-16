"""Human-triggered software update routes.

Development checkouts keep the explicit Git workflow. Installed packaged runtimes use
signed immutable releases, durable progress state, and tray-controlled activation.
These routes are not agent-callable tools.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.config.loader import is_preview_mode
from aletheia.files.jsonl import write_audit
from aletheia.update import applier

router = APIRouter(prefix="/api/update", tags=["update"])


def _reject_preview_update() -> None:
    if is_preview_mode():
        from fastapi import HTTPException

        raise HTTPException(
            403,
            detail={
                "reason_code": "preview_update_blocked",
                "message": "Software update control is unavailable in preview mode.",
            },
        )


@router.get("/status")
async def update_status():
    return applier.check_for_update()


@router.get("/progress")
async def update_progress():
    """Read durable local update progress without repeating a network check."""

    return applier.update_progress()


class ApplyBody(BaseModel):
    confirm: bool = False


class ContinuityBody(BaseModel):
    confirm: bool = False
    cause: str = "update_continuity_candidate"


@router.post("/apply")
async def update_apply(body: ApplyBody = ApplyBody()):
    _reject_preview_update()
    if not body.confirm:
        return {
            "applied": False,
            "accepted": False,
            "reason": "confirmation_required",
            "message": "Set confirm=true to apply the update.",
        }
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "code_update_requested",
            "entity_type": "system_update",
            "entity_id": "apply",
            "message": "Human confirmed software update via API",
        }
    )
    return applier.apply_update()


@router.post("/rollback")
async def update_rollback(body: ApplyBody = ApplyBody()):
    _reject_preview_update()
    if not body.confirm:
        return {
            "accepted": False,
            "reason": "confirmation_required",
            "message": "Set confirm=true to request rollback.",
        }
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "packaged_update_rollback_requested",
            "entity_type": "system_update",
            "entity_id": "rollback",
            "message": "Human confirmed packaged update rollback via API",
        }
    )
    return applier.rollback_update()


@router.post("/continuity")
async def update_continuity(body: ContinuityBody = ContinuityBody()):
    """Legacy development-checkout candidate materialisation.

    Installed packages already stage their immutable candidate before the tray switch and
    must not create a Git worktree candidate.
    """

    _reject_preview_update()
    if not body.confirm:
        return {
            "created": False,
            "reason": "confirmation_required",
            "message": "Set confirm=true to create a runtime continuity candidate.",
        }
    status = applier.check_for_update()
    if status.get("mode") == "packaged":
        return {
            "created": False,
            "reason": "packaged_candidate_managed_by_tray",
            "message": "Installed releases are staged and promoted by Aletheia Tray.",
        }
    if not status.get("ok"):
        return {"created": False, "reason": status.get("reason", "update_status_unavailable")}
    remote_commit = status.get("remote_commit") or status.get("current_commit") or "unknown"
    branch = str(status.get("branch") or "master")
    runtime_id = f"runtime-{str(remote_commit)[:12].replace('/', '-')}"
    try:
        from aletheia.runtime_continuity import bundles

        artifact_path = str(
            bundles.materialize_candidate_worktree(
                runtime_id=runtime_id,
                commit=str(remote_commit),
                branch=branch,
            )
        )
    except Exception as exc:
        return {
            "created": False,
            "reason": "candidate_materialization_failed",
            "detail": str(exc),
            "update_status": status,
        }
    from aletheia.runtime_continuity.service import RuntimeContinuityService
    from aletheia.config.loader import load_config as _load_cfg

    cfg = _load_cfg()
    candidate = RuntimeContinuityService(
        enabled=cfg.runtime.runtime_continuity_enabled,
    ).prepare_candidate(
        actor="human",
        cause=body.cause,
        code_version=str(remote_commit),
        config_version="update-config",
        schema_version="sqlite-runtime-continuity-v1",
        build_git_sha=str(remote_commit),
        release_bundle_hash=str(remote_commit),
        artifact_path=artifact_path,
        metadata={
            "source": "update_continuity",
            "branch": branch,
            "behind_count": status.get("behind_count"),
            "current_commit": status.get("current_commit"),
            "artifact_path": artifact_path,
        },
    )
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "runtime_candidate_created",
            "entity_type": "runtime",
            "entity_id": candidate["runtime_id"],
            "message": f"Runtime candidate created from update status ({remote_commit})",
        }
    )
    return {"created": True, "candidate": candidate, "update_status": status}
