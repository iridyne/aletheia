"""Self-upgrade API routes — human-triggered code update (guide: not a daemon).

GET /api/update/status   — check whether an update is available (read-only fetch).
POST /api/update/apply   — pull latest, reinstall deps if needed, signal restart.

These call the same applier the CLI uses. They are NOT agent-callable tools.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from aletheia.files.jsonl import write_audit
from aletheia.update import applier

router = APIRouter(prefix="/api/update", tags=["update"])


@router.get("/status")
async def update_status():
    return applier.check_for_update()


class ApplyBody(BaseModel):
    confirm: bool = False


class ContinuityBody(BaseModel):
    confirm: bool = False
    cause: str = "update_continuity_candidate"


@router.post("/apply")
async def update_apply(body: ApplyBody = ApplyBody()):
    if not body.confirm:
        return {"applied": False, "reason": "confirmation_required",
                "message": "Set confirm=true to apply the update."}
    write_audit({
        "actor": "human", "source": "api", "event_type": "code_update_requested",
        "entity_type": "system_update", "entity_id": "apply",
        "message": "Human confirmed code update via API",
    })
    return applier.apply_update()


@router.post("/continuity")
async def update_continuity(body: ContinuityBody = ContinuityBody()):
    if not body.confirm:
        return {
            "created": False,
            "reason": "confirmation_required",
            "message": "Set confirm=true to create a runtime continuity candidate.",
        }
    status = applier.check_for_update()
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

    candidate = RuntimeContinuityService().prepare_candidate(
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
    write_audit({
        "actor": "human", "source": "api", "event_type": "runtime_candidate_created",
        "entity_type": "runtime", "entity_id": candidate["runtime_id"],
        "message": f"Runtime candidate created from update status ({remote_commit})",
    })
    return {"created": True, "candidate": candidate, "update_status": status}
