"""Durable, cross-session persona rollout progression (runs as a scheduler job).

owner authorized (2026-07-02) advancing the dynamic-sizing rollout ladder at a
measured, health-gated pace, ACROSS sessions. Claude's own durable cron isn't
supported in this environment, so the progression lives here as an Aletheia
scheduler job (`persona_rollout`, every 8h) — it runs inside the gateway process,
which the Task Scheduler watchdog keeps alive across sessions and reboots.

Because load_config() re-reads the file every call, save_config() here takes effect
on the next sizing decision WITHOUT a gateway restart (no self-restart problem).

Ladder (design §8): persona_sizing → [obs] → progressive_unlock → [obs] → learning.
One step per call; the 8h interval IS the observation window between steps. Held on
distress or reconcile-thrash (the runtime auto-refreeze is the second line). NEVER
touches owner-gated items (demo leverage cap, roll, pyramid live scale-in).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("aletheia.trading.persona_rollout")


def advance_persona_rollout() -> dict:
    """Advance the rollout by at most ONE step if healthy. Returns a summary dict.

    Gated by cfg.trading.persona_auto_advance_enabled (master switch, default False).
    Health gate: no distress (drawdown/kill switch) and no reconcile-thrash fingerprint.
    """
    from aletheia.config.loader import load_config, save_config
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

    if not t.persona_progressive_unlock_enabled:
        t.persona_progressive_unlock_enabled = True
        step = "progressive_unlock"
    elif not t.persona_learning_enabled:
        t.persona_learning_enabled = True
        step = "learning"
    else:
        return {"action": "hold", "reason": "rollout complete (unlock + learning both on)"}

    save_config(cfg)
    logger.info("persona rollout advanced: %s enabled", step)
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system", "source": "persona_rollout",
            "event_type": "persona_rollout_advance", "entity_type": "config_param",
            "entity_id": step, "after": {"step": step, "active_persona": t.active_persona},
            "message": f"Persona rollout advanced (health-gated): {step} enabled",
        })
    except Exception:
        pass
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            title="动态 sizing 推进一步",
            message=f"persona rollout 健康门通过,已启用 {step}(active_persona={t.active_persona})。",
            level="info", event_type="self_heal", entity_type="config_param", error_log=False,
        )
    except Exception:
        pass
    return {"action": "advanced", "step": step}
