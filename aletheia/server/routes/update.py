"""Human-triggered software update routes.

Development checkouts keep the explicit Git workflow. Installed packaged runtimes use
signed immutable releases, durable progress state, and tray-controlled activation.
These routes are not agent-callable tools.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from aletheia.config.loader import is_preview_mode
from aletheia.files.jsonl import write_audit
from aletheia.update import applier, continuity

router = APIRouter(prefix="/api/update", tags=["update"])
_LOOPBACK_UI_ORIGINS = (
    "http://127.0.0.1:8787/",
    "http://localhost:8787/",
    "http://[::1]:8787/",
)


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


def _same_origin_dashboard_confirmation(request: Request) -> bool:
    """Accept the legacy Dashboard POST only when browser-controlled headers prove origin.

    The Settings card already presents a human confirmation modal but historically sent an
    empty JSON body. CLI, scripts, agents, and cross-site browser requests still have to send
    ``confirm=true`` explicitly. ``Sec-Fetch-Site`` is a forbidden browser header, so page
    JavaScript from another origin cannot forge the same-origin classification.
    """

    if request.headers.get("sec-fetch-site", "").lower() not in {"same-origin", "same-site"}:
        return False
    referer = request.headers.get("referer", "")
    origin = request.headers.get("origin", "")
    return any(referer.startswith(base) or origin == base.rstrip("/") for base in _LOOPBACK_UI_ORIGINS)


def _is_packaged_update_mode() -> bool:
    from aletheia.update.packaged_commands import is_packaged_mode

    return is_packaged_mode()


@router.get("/status")
def update_status():
    """Return local state only; network checks require an explicit human action."""

    return {
        **applier.update_progress(),
        "ok": False,
        "reason": "update_check_required",
    }


@router.post("/check")
def update_check():
    return applier.check_for_update()


@router.get("/progress")
def update_progress():
    """Read durable local update progress without repeating a network check."""

    return applier.update_progress()


class ApplyBody(BaseModel):
    confirm: bool = False


class ContinuityBody(BaseModel):
    confirm: bool = False
    cause: str = "update_continuity_candidate"


@router.post("/apply")
def update_apply(request: Request, body: ApplyBody = ApplyBody()):
    _reject_preview_update()
    if not body.confirm and not _same_origin_dashboard_confirmation(request):
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

    # Packaged installs keep the signed Tray-owned staging flow. Development
    # checkouts must use the same Observer-aware transaction as `aletheia update`;
    # the Dashboard is not allowed to bypass continuity verification.
    if _is_packaged_update_mode():
        result = applier.apply_update()
    else:
        result = continuity.apply_update_with_continuity(restart=True)

    if result.get("mode") == "packaged" and result.get("accepted") and not result.get("applied"):
        result.setdefault("reason", "update_started_in_background")
    return result


@router.post("/rollback")
def update_rollback(request: Request, body: ApplyBody = ApplyBody()):
    _reject_preview_update()
    if not body.confirm and not _same_origin_dashboard_confirmation(request):
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
def update_continuity(body: ContinuityBody = ContinuityBody()):
    """Legacy development-checkout candidate materialisation.

    Installed packaged updates do not create Git worktrees. They stage signed payloads and
    use the Tray host for activation. Keep this endpoint only for explicit development
    continuity experiments.
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
            "reason": "packaged_runtime_uses_signed_update_flow",
            "update_status": status,
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
