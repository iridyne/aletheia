"""Live unlock API route — requires explicit human action."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config
from aletheia.files.jsonl import write_audit

router = APIRouter(prefix="/api/settings", tags=["live"])


class LiveUnlockRequest(BaseModel):
    confirm: bool = False
    acknowledge_first_unlock: bool = False


@router.post("/live-unlock")
async def live_unlock(req: LiveUnlockRequest):
    """Unlock live-limited trading. Requires explicit confirmation."""
    if not req.confirm:
        raise HTTPException(400, "Must pass confirm=true to unlock live trading")

    cfg = load_config()
    if cfg.live.state != "live_disabled":
        return {"status": cfg.live.state, "message": "Already unlocked"}

    if not cfg.live.first_live_confirmation_done and not req.acknowledge_first_unlock:
        raise HTTPException(400, "First-time live unlock requires acknowledge_first_unlock=true")

    # D8-B02/C01: scorecard gate — composite_score must reach ≥70 before live unlock.
    # Ensures demo performance meets the minimum readiness threshold (product §8 K3).
    from aletheia.analytics.go_live_scorecard import compute_scorecard
    scorecard = compute_scorecard()
    composite_score = scorecard["composite_score"]
    traffic_light = scorecard.get("traffic_light")
    blockers = scorecard.get("blockers") or []
    gate_failures = []
    if composite_score < 70:
        gate_failures.append(f"composite_score={composite_score:.1f} is below 70")
    if traffic_light != "green":
        gate_failures.append(f"traffic_light={traffic_light or 'not available'} is not green")
    if blockers:
        gate_failures.append(f"{len(blockers)} readiness blocker(s) remain")
    if gate_failures:
        raise HTTPException(
            403,
            detail={
                "error": "scorecard_gate_failed",
                "message": "Go-live scorecard gate failed: "
                + "; ".join(gate_failures)
                + ".",
                "composite_score": composite_score,
                "traffic_light": traffic_light,
                "blockers": blockers,
            },
        )

    old_state = cfg.live.state
    old_permission_mode = cfg.operator.permission_mode

    # Issue #388: write the audit record BEFORE committing the state change.
    # If the JSONL write fails, refuse the unlock so config and audit chain stay
    # in sync.  A live unlock that isn't audited is a trust-boundary violation.
    audit_ok = write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_unlocked",
        "entity_type": "live_state",
        "before": {"state": old_state, "operator_permission_mode": old_permission_mode},
        "after": {"state": "live_limited", "operator_permission_mode": "Auto"},
        "message": "Live trading unlocked to live_limited by human confirmation; PI operator elevated to Auto",
    })
    if not audit_ok:
        raise HTTPException(
            503,
            detail={
                "error": "audit_write_failed",
                "message": (
                    "Live unlock refused: audit.jsonl write failed. "
                    "Resolve the storage issue and retry. "
                    "No state change has been applied."
                ),
            },
        )

    cfg.live.state = "live_limited"
    cfg.live.first_live_confirmation_done = True
    # §4b: System Operator gets full autonomy (Auto=4) when live is unlocked.
    # This is the only path that can elevate operator.permission_mode to Auto;
    # it must be symmetric with live_lock() which resets it to ReadOnly.
    cfg.operator.permission_mode = "Auto"
    save_config(cfg)

    return {"status": "live_limited", "message": "Live-limited trading enabled"}


@router.post("/live-lock")
async def live_lock():
    """Lock live trading back to disabled."""
    cfg = load_config()
    old_state = cfg.live.state
    old_permission_mode = cfg.operator.permission_mode
    # Issue #388: write the audit record BEFORE committing the state change.
    # live_lock is a safety action so we still persist even if audit write fails,
    # but we surface the failure in the response so operators know the chain is
    # incomplete.  Not refusing here is intentional: locking is safer than not.
    audit_ok = write_audit({
        "actor": "human",
        "source": "dashboard",
        "event_type": "live_locked",
        "entity_type": "live_state",
        "before": {"state": old_state, "operator_permission_mode": old_permission_mode},
        "after": {"state": "live_disabled", "operator_permission_mode": "ReadOnly"},
        "message": "Live trading locked; PI operator reset to ReadOnly",
    })

    cfg.live.state = "live_disabled"
    # §4b symmetry: locking live must reset PI operator back to ReadOnly so the
    # Auto authority granted by live_unlock does not persist after locking.
    cfg.operator.permission_mode = "ReadOnly"
    save_config(cfg)

    response: dict = {"status": "live_disabled", "message": "Live trading disabled"}
    if not audit_ok:
        response["audit_warning"] = (
            "audit.jsonl write failed — lock was applied but the JSONL audit chain "
            "is incomplete. Investigate storage before the next unlock."
        )
    return response
