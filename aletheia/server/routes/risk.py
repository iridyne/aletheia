"""Risk API routes: kill switch, status, and boundaries display."""

import json

from fastapi import APIRouter
from pydantic import BaseModel

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
        if latest_decision and not bool(latest_decision.get("approved")):
            status = "warning"
            violations.extend(_parse_violations(latest_decision.get("violations")))
        if not violations and cfg.runtime.kill_switch_active:
            status = "blocked"

    return {
        "kill_switch_active": cfg.runtime.kill_switch_active,
        "live_state": live_state,
        "permission_mode": permission_mode,
        "latest_risk_decision": latest_decision,
        "status": status,
        "violations": violations,
    }


class KillSwitchRequest(BaseModel):
    active: bool
    reason: str = ""


@router.post("/kill-switch")
async def set_kill_switch(req: KillSwitchRequest):
    """Engage or release the global trading brake (human action)."""
    if req.active:
        kill_switch.activate(reason=req.reason, actor="human")
    else:
        kill_switch.deactivate(actor="human")
    return {"active": kill_switch.is_active()}


def _parse_violations(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [value]
    if isinstance(parsed, list):
        return [str(v) for v in parsed]
    return [str(parsed)]
