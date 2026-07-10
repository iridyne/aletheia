"""Risk API routes: kill switch, status, and boundaries display."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.agent_shell.operator_recommendations import build_risk_summary
from aletheia.config.loader import load_config
from aletheia.risk import kill_switch
from aletheia.storage.migrations import run_migrations
from aletheia.storage.repositories import list_risk_decisions

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/boundaries")
async def get_risk_boundaries():
    """Return the hard risk limits for display. Read-only over the API."""
    return load_config().risk.model_dump()


@router.get("/kill-switch")
async def kill_switch_status():
    return {"active": kill_switch.is_active()}


@router.get("/status")
async def risk_status():
    cfg = load_config()
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
    else:
        if exchange_mode == "live" and live_state == "live_disabled":
            status = "warning"
            violations.append("live_disabled_while_exchange_live")
        if exchange_mode == "live" and permission_mode == "Observe":
            status = "warning"
            violations.append(f"permission_mode_not_live_eligible:{permission_mode}")
    # A decision is immutable history, not a live condition. It remains available
    # to the UI as latest_risk_decision, but must not turn a resolved past rejection
    # into a permanent current warning.
    if not violations and latest_decision:
        status = "healthy"

    return {
        "kill_switch_active": cfg.runtime.kill_switch_active,
        "live_state": live_state,
        "permission_mode": permission_mode,
        "latest_risk_decision": latest_decision,
        "status": status,
        "violations": violations,
    }


@router.get("/summary")
async def risk_summary():
    """Operator-facing summary of recent risk rejections and pending proposal-worthy boundary changes."""
    return build_risk_summary()


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""
    # Releasing the brake (active=False) requires a second explicit confirmation so a
    # stray/accidental POST cannot silently un-brake trading. Engaging never needs it.
    confirm_release: bool = False


@router.post("/kill-switch")
async def set_kill_switch(req: KillSwitchRequest):
    """Engage or release the global trading brake (human action).

    Release is guarded: active=False without confirm_release=True returns 422. The
    brake is the last-resort stop; un-setting it must be a deliberate two-step action.
    """
    if req.active:
        kill_switch.activate(reason=req.reason, actor="human")
    else:
        if not req.confirm_release:
            raise HTTPException(
                status_code=422,
                detail="Releasing the kill switch requires confirm_release=true "
                       "(second confirmation).",
            )
        kill_switch.deactivate(actor="human")
    return {"active": kill_switch.is_active()}


@router.get("/account-leverage")
async def account_leverage():
    """Current account-level leverage ratio and margin status."""
    from aletheia.risk.engine import check_account_leverage
    cfg = load_config()
    return check_account_leverage(cfg)
