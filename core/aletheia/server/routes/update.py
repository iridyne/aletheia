"""Human-triggered software update routes.

A source checkout keeps the explicit Git workflow: these routes report durable local
state and refuse to mutate source. These routes are not agent-callable tools.
"""

import sys
from fastapi import APIRouter, Request
from pydantic import BaseModel

from aletheia.config.loader import is_preview_mode
from aletheia.files.jsonl import write_audit

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


@router.get("/status")
def update_status():
    """Return local state only; network checks require an explicit human action."""
    from ops.update import applier

    state = applier.update_progress()
    if state.get("mode") == "source_release":
        return state
    return {**state, "ok": False, "reason": "update_check_required"}


@router.post("/check")
def update_check():
    from ops.update import applier
    return applier.check_for_update()


@router.get("/progress")
def update_progress():
    """Read durable local update progress without repeating a network check."""
    from ops.update import applier

    return applier.update_progress()


class ApplyBody(BaseModel):
    confirm: bool = False


@router.post("/apply")
def update_apply(request: Request, body: ApplyBody = ApplyBody()):
    from ops.update import applier
    _reject_preview_update()
    if not body.confirm and not _same_origin_dashboard_confirmation(request):
        return {
            "applied": False,
            "accepted": False,
            "reason": "confirmation_required",
            "message": "Set confirm=true to apply the update.",
        }

    # The restart must be owned by the external CLI process. A request handler
    # lives inside the Gateway process tree that do_stop() kills, so running the
    # restart transaction from this route is not a safe alternative entrypoint.
    # `aletheia update` stays the one source mutation command.
    write_audit(
        {
            "actor": "human",
            "source": "api",
            "event_type": "source_update_requires_cli",
            "entity_type": "system_update",
            "entity_id": "apply",
            "message": "Source update was not applied via API; use `aletheia update`",
        }
    )
    return {
        "applied": False,
        "accepted": False,
        "mode": "source_release" if sys.platform == "linux" else "development",
        "reason": "source_update_requires_cli",
        "message": (
            "Source-checkout updates must run through `aletheia update` so the "
            "external updater process can restart Main and verify Observer continuity."
        ),
    }
