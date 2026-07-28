"""Safety-held Persona rollout scheduler job.

The previous implementation treated an eight-hour scheduler interval as an
evidence window and automatically enabled behavior-changing Persona flags. No
prospective Persona/policy/step verdict backed that transition, so established
historical outcomes could grant new sizing authority immediately.

The job remains registered for compatibility and health visibility, but is
read-only until the existing experiment/contribution evidence system can supply
a prospective step verdict. It never changes trading configuration.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.trading.persona_rollout")


def advance_persona_rollout() -> dict:
    """Inspect rollout safety gates without changing configuration.

    Gated by cfg.trading.persona_auto_advance_enabled (master switch, default False).
    Health failures retain their specific hold reason. A healthy call also holds
    because no prospective Persona/policy/step verdict exists yet.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    t = cfg.trading

    if not getattr(t, "persona_auto_advance_enabled", False):
        return {"action": "disabled", "reason": "persona_auto_advance_enabled=False"}
    if not t.persona_sizing_enabled:
        return {"action": "hold", "reason": "persona_sizing not enabled (rollout not started)"}

    # Health gate — a persisted incident holds across restart and clean retries.
    from aletheia.learning.control import global_learning_refreeze_reason
    refreeze = global_learning_refreeze_reason()
    if refreeze is not None:
        return {"action": "hold", "reason": refreeze}

    # Secondary health gates retain the immediate detector and distress checks.
    from aletheia.trading.sizing_policy import in_distress
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash
    if in_distress(cfg):
        return {"action": "hold", "reason": "account distress (drawdown/kill switch)"}
    if detect_reconcile_thrash().get("is_thrashing"):
        return {"action": "hold", "reason": "reconcile thrash fingerprint present"}

    return {
        "action": "hold",
        "reason": "prospective_persona_policy_step_verdict_unavailable",
    }
