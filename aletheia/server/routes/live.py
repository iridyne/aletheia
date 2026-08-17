"""Live unlock API route — requires explicit human action."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aletheia.config.loader import load_config, save_config
from aletheia.files.jsonl import write_audit
from aletheia.trust.live_unlock import (
    evidence_matches_config,
    public_evidence,
    revoke_stale_live_unlock,
    run_live_preflight,
)

router = APIRouter(prefix="/api/settings", tags=["live"])


class LiveUnlockRequest(BaseModel):
    confirm: bool = False
    acknowledge_first_unlock: bool = False


def _public_evidence(evidence: dict) -> dict:
    return public_evidence(evidence)


@router.post("/live-preflight")
async def live_preflight():
    """Read-only private-account preflight for the selected live exchange."""
    return await run_live_preflight(load_config())


@router.post("/live-unlock")
async def live_unlock(req: LiveUnlockRequest):
    """Unlock live-limited trading. Requires explicit confirmation."""
    if not req.confirm:
        raise HTTPException(400, "Must pass confirm=true to unlock live trading")

    cfg = load_config()
    if cfg.live.state != "live_disabled":
        if cfg.live.state == "live_limited":
            valid, reason = evidence_matches_config(cfg, cfg.live.unlock_evidence)
            if valid:
                return {"status": cfg.live.state, "message": "Already unlocked"}
            revoke_stale_live_unlock(cfg)
            raise HTTPException(
                409,
                detail={
                    "error": "live_unlock_evidence_stale",
                    "message": (
                        "Live unlock evidence was revoked because it is stale or no "
                        "longer matches the selected account; revalidate before "
                        "unlocking again."
                    ),
                    "reason_code": reason,
                },
            )
        raise HTTPException(409, "Live state is not unlockable from its current state")

    if not cfg.live.first_live_confirmation_done and not req.acknowledge_first_unlock:
        raise HTTPException(400, "First-time live unlock requires acknowledge_first_unlock=true")

    # The selected account is the first gate.  It is private, broker-specific,
    # live-environment, permission-aware, and expires before any state mutation.
    preflight = await run_live_preflight(cfg)
    if not preflight.get("ok"):
        raise HTTPException(
            403,
            detail={
                "error": "live_preflight_failed",
                "message": "Selected live account preflight failed; live unlock remains disabled.",
                "reason_code": preflight.get("reason_code", "preflight_failed"),
                "broker": preflight.get("broker"),
                "account_environment": preflight.get("account_environment"),
                "permission_status": preflight.get("permission_status", "unknown"),
                "preflight": _public_evidence(preflight),
            },
        )

    # D8-B02/C01: scorecard gate — composite_score must reach ≥70 before live unlock.
    # Ensures demo performance meets the minimum readiness threshold (product §8 K3).
    from aletheia.analytics.go_live_scorecard import compute_scorecard
    try:
        scorecard = compute_scorecard(live_evidence=preflight)
    except TypeError as exc:
        # Existing extension/test callables may still expose the old no-argument
        # signature.  Keep that compatibility without weakening the production
        # scorecard path above.
        if "live_evidence" not in str(exc):
            raise
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

    # Re-read after the network call and scorecard.  A credential, exchange, or
    # mode change during preflight must never be committed against old evidence.
    latest = load_config()
    if latest.live.state != "live_disabled":
        raise HTTPException(409, "Live state changed during preflight; retry after revalidation")
    valid, reason = evidence_matches_config(latest, preflight)
    if not valid:
        raise HTTPException(
            409,
            detail={
                "error": "live_unlock_config_revision_conflict",
                "message": "Selected live exchange or credentials changed during preflight; no unlock was applied.",
                "reason_code": reason,
            },
        )
    cfg = latest

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
        "after": {
            "state": "live_limited",
            "operator_permission_mode": "Auto",
            "live_execution_identity": _public_evidence(preflight),
        },
        "live_execution_identity": _public_evidence(preflight),
        "message": "Live trading unlocked to live_limited by human confirmation after selected-account preflight; PI operator elevated to Auto",
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
    cfg.live.unlock_evidence = _public_evidence(preflight)
    # §4b: System Operator gets full autonomy (Auto=4) when live is unlocked.
    # This is the only path that can elevate operator.permission_mode to Auto;
    # it must be symmetric with live_lock() which resets it to ReadOnly.
    cfg.operator.permission_mode = "Auto"
    save_config(cfg)

    return {
        "status": "live_limited",
        "message": "Live-limited trading enabled",
        "execution_identity": _public_evidence(preflight),
    }


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
        "before": {
            "state": old_state,
            "operator_permission_mode": old_permission_mode,
            "live_execution_identity": _public_evidence(cfg.live.unlock_evidence or {}),
        },
        "after": {
            "state": "live_disabled",
            "operator_permission_mode": "ReadOnly",
            "live_execution_identity": None,
        },
        "message": "Live trading locked; PI operator reset to ReadOnly",
    })

    cfg.live.state = "live_disabled"
    cfg.live.unlock_evidence = None
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
