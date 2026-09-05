"""Risk API routes: kill switch, status, and boundaries display."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from aletheia.agent_shell.operator_recommendations import build_risk_summary
from aletheia.config.loader import load_config
from aletheia.config.schema import RiskConfig
from aletheia.risk import kill_switch
from aletheia.storage.repositories import list_risk_decisions


class KillSwitchStatusResponse(BaseModel):
    active: bool


class BrakesStatusResponse(BaseModel):
    global_active: bool
    demo_active: bool
    live_active: bool


class RiskStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    kill_switch_active: bool
    demo_brake_active: bool = False
    live_brake_active: bool = False
    live_state: str = ""
    permission_mode: str = ""
    latest_risk_decision: dict[str, Any] | None = None
    status: str = "not_evaluated"
    violations: list[str] = []
    pause_owners: list[dict[str, Any]] = []
    pause_blockers: list[str] = []
    resume_blocked_reason: str | None = None


class RiskSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    recent_rejected_decisions: list[dict[str, Any]] = []
    needs_operator_risk_boundary_proposals: list[dict[str, Any]] = []

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/boundaries", response_model=RiskConfig)
def get_risk_boundaries():
    """Return the hard risk limits for display. Read-only over the API."""
    return load_config().risk


@router.get("/kill-switch", response_model=KillSwitchStatusResponse)
def kill_switch_status():
    return {"active": kill_switch.is_active()}


def _brake_status() -> dict:
    return {
        "global_active": kill_switch.is_active("global"),
        "demo_active": kill_switch.is_active("demo"),
        "live_active": kill_switch.is_active("live"),
    }


@router.get("/brakes", response_model=BrakesStatusResponse)
def brakes_status():
    return _brake_status()


@router.get("/status", response_model=RiskStatusResponse)
def risk_status():
    cfg = load_config()
    from aletheia.agent_runtime.pause_ownership import (
        active_pause_blocker_names,
        active_pause_owners,
        pause_blocked_reason,
    )
    # Migrations run once at startup (aletheia/server/app.py:44).
    latest = list_risk_decisions(limit=1)
    latest_decision = latest[0] if latest else None
    exchange_mode = cfg.exchange.mode
    live_state = cfg.live.state
    permission_mode = cfg.agent.permission_mode
    violations: list[str] = []
    status = "not_evaluated"

    if cfg.runtime.kill_switch_active:
        status = "blocked"
        violations.append("kill_switch_active")
    elif cfg.runtime.live_brake_active and exchange_mode == "live":
        status = "blocked"
        violations.append("live_brake_active")
    elif cfg.runtime.demo_brake_active and exchange_mode != "live":
        status = "blocked"
        violations.append("demo_brake_active")
    else:
        if exchange_mode == "live" and live_state == "live_disabled":
            status = "warning"
            violations.append("live_disabled_while_exchange_live")
        if exchange_mode == "live" and permission_mode == "Observe":
            status = "warning"
            violations.append(f"permission_mode_not_live_eligible:{permission_mode}")
    # A decision is immutable history, not a live condition. It remains available
    # to the UI as latest_risk_decision, but cannot prove that current admission
    # inputs are available or that current limits were evaluated. Keep the
    # default ``not_evaluated`` until a live brake/warning is present.

    return {
        "kill_switch_active": cfg.runtime.kill_switch_active,
        "demo_brake_active": cfg.runtime.demo_brake_active,
        "live_brake_active": cfg.runtime.live_brake_active,
        "live_state": live_state,
        "permission_mode": permission_mode,
        "latest_risk_decision": latest_decision,
        "status": status,
        "violations": violations,
        "pause_owners": active_pause_owners(cfg),
        "pause_blockers": list(active_pause_blocker_names(cfg)),
        "resume_blocked_reason": pause_blocked_reason(cfg),
    }


@router.get("/summary", response_model=RiskSummaryResponse)
def risk_summary():
    """Operator-facing summary of recent risk rejections and pending proposal-worthy boundary changes."""
    return build_risk_summary()


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""
    # Releasing the brake (active=False) requires a second explicit confirmation so a
    # stray/accidental POST cannot silently un-brake trading. Engaging never needs it.
    confirm_release: bool = False


@router.post("/kill-switch")
def set_kill_switch(req: KillSwitchRequest):
    """Engage or release the global trading brake (human action).

    Release is guarded: active=False without confirm_release=True returns 422. The
    brake is the last-resort stop; un-setting it must be a deliberate two-step action.
    """
    if req.active:
        audit_ok = kill_switch.activate(reason=req.reason, actor="human")
    else:
        if not req.confirm_release:
            raise HTTPException(
                status_code=422,
                detail="Releasing the kill switch requires confirm_release=true "
                       "(second confirmation).",
            )
        try:
            audit_ok = kill_switch.deactivate(actor="human")
        except kill_switch.AuditPersistenceError as exc:
            raise HTTPException(503, detail={
                "error": "audit_write_failed",
                "message": "Kill-switch release refused; the brake remains active.",
            }) from exc
    return {
        "active": kill_switch.is_active(),
        "audit_status": "mirrored" if audit_ok else "degraded_retry_required",
    }


@router.post("/brakes/{scope}")
def set_mode_brake(scope: str, req: KillSwitchRequest):
    if scope not in {"global", "demo", "live"}:
        raise HTTPException(404, "Unknown trading brake scope")
    if req.active:
        audit_ok = kill_switch.activate(reason=req.reason, actor="human", scope=scope)
    else:
        if not req.confirm_release:
            raise HTTPException(
                422,
                "Releasing a trading brake requires confirm_release=true",
            )
        try:
            audit_ok = kill_switch.deactivate(actor="human", scope=scope)
        except kill_switch.AuditPersistenceError as exc:
            raise HTTPException(503, detail={
                "error": "audit_write_failed",
                "message": "Trading-brake release refused; the brake remains active.",
            }) from exc
    return {
        "scope": scope,
        "active": kill_switch.is_active(scope),
        "audit_status": "mirrored" if audit_ok else "degraded_retry_required",
        **_brake_status(),
    }


@router.get("/account-leverage")
def account_leverage():
    """Current account-level leverage ratio and margin status."""
    from aletheia.risk.engine import check_account_leverage
    cfg = load_config()
    return check_account_leverage(cfg)
