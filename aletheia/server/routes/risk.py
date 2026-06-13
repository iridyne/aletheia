"""Risk API routes: kill switch, status, and boundaries display."""

import json

from fastapi import APIRouter
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
        if latest_decision and not bool(latest_decision.get("approved")):
            raw_violations = _parse_violations(latest_decision.get("violations"))
            # Filter out stale_market_data violations that have since resolved —
            # re-check current freshness rather than echoing a past rejection forever.
            live_violations = [v for v in raw_violations if not _is_resolved_staleness(v)]
            if live_violations:
                status = "warning"
                violations.extend(live_violations)
        if not violations and cfg.runtime.kill_switch_active:
            status = "blocked"

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


@router.post("/kill-switch")
async def set_kill_switch(req: KillSwitchRequest):
    """Engage or release the global trading brake (human action)."""
    if req.active:
        kill_switch.activate(reason=req.reason, actor="human")
    else:
        kill_switch.deactivate(actor="human")
    return {"active": kill_switch.is_active()}


def _is_resolved_staleness(violation: str) -> bool:
    """Return True if a stale_market_data* violation has since resolved (data is fresh now)."""
    if not violation.startswith("stale_market_data"):
        return False
    # Extract symbol from the risk decision's proposal context — not available in the
    # violation string alone. Fall back to checking ALL observed symbols; if any are fresh
    # then the system-wide staleness has resolved.
    from aletheia.trading.market_data import latest_snapshot
    from aletheia.trading.instruments import observed_instruments_from_config

    cfg = load_config()
    is_demo = "demo" in violation
    max_age = 1800 if is_demo else 120

    instruments = observed_instruments_from_config(cfg)
    if not instruments:
        return False
    # If ANY observed symbol is fresh, the collection loop is working again.
    for inst in instruments:
        snap = latest_snapshot(inst.symbol)
        if snap:
            from datetime import datetime, timezone
            ts = snap.get("snapshot_at")
            if ts:
                try:
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age = (datetime.now(timezone.utc) - dt).total_seconds()
                    if age <= max_age:
                        return True
                except ValueError:
                    continue
    return False


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
