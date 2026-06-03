"""Global kill switch — system-wide trading brake.

When active, the Risk Engine rejects every proposal (paper and live). State is
persisted to config (`runtime.kill_switch_active`) so the brake survives a
gateway restart. May be set by explicit human action (CLI / API / dashboard) or
auto-tripped by the risk engine when the daily-loss limit is breached.

This is a HARD control (guide §2.4, §21.3). The agent cannot deactivate it via
any LLM-callable tool — only human/system actors through this module.
"""

from aletheia.config.loader import load_config, save_config
from aletheia.files.jsonl import write_audit


def is_active() -> bool:
    """Return True if the kill switch is currently engaged."""
    return load_config().runtime.kill_switch_active


def activate(reason: str = "", actor: str = "human") -> None:
    """Engage the kill switch. Idempotent; always writes an audit event."""
    cfg = load_config()
    was_active = cfg.runtime.kill_switch_active
    cfg.runtime.kill_switch_active = True
    save_config(cfg)
    write_audit({
        "actor": actor,
        "source": "risk_engine" if actor == "system" else "dashboard",
        "event_type": "kill_switch_activated",
        "entity_type": "kill_switch",
        "before": {"active": was_active},
        "after": {"active": True},
        "message": f"Kill switch activated: {reason}" if reason else "Kill switch activated",
    })


def deactivate(actor: str = "human") -> None:
    """Release the kill switch. Only human/system actors should call this."""
    cfg = load_config()
    was_active = cfg.runtime.kill_switch_active
    cfg.runtime.kill_switch_active = False
    save_config(cfg)
    write_audit({
        "actor": actor,
        "source": "dashboard",
        "event_type": "kill_switch_deactivated",
        "entity_type": "kill_switch",
        "before": {"active": was_active},
        "after": {"active": False},
        "message": "Kill switch deactivated",
    })
