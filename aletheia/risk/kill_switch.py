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


_SCOPES = {"global", "demo", "live"}


def _field(scope: str) -> str:
    if scope not in _SCOPES:
        raise ValueError(f"unknown brake scope: {scope!r}")
    return {
        "global": "kill_switch_active",
        "demo": "demo_brake_active",
        "live": "live_brake_active",
    }[scope]


def is_active(scope: str = "global") -> bool:
    """Return whether the selected global or mode-scoped brake is engaged."""
    return bool(getattr(load_config().runtime, _field(scope)))


def activate(
    reason: str = "",
    actor: str = "human",
    scope: str = "global",
) -> None:
    """Engage the kill switch. Idempotent; always writes an audit event."""
    cfg = load_config()
    field = _field(scope)
    was_active = bool(getattr(cfg.runtime, field))
    setattr(cfg.runtime, field, True)
    save_config(cfg)
    write_audit({
        "actor": actor,
        "source": "risk_engine" if actor == "system" else "dashboard",
        "event_type": "trading_brake_activated",
        "entity_type": "trading_brake",
        "entity_id": scope,
        "before": {"active": was_active},
        "after": {"active": True},
        "message": (
            f"{scope} trading brake activated: {reason}"
            if reason else f"{scope} trading brake activated"
        ),
    })


def deactivate(actor: str = "human", scope: str = "global") -> None:
    """Release the kill switch. Only human/system actors should call this."""
    cfg = load_config()
    field = _field(scope)
    was_active = bool(getattr(cfg.runtime, field))
    setattr(cfg.runtime, field, False)
    save_config(cfg)
    write_audit({
        "actor": actor,
        "source": "dashboard",
        "event_type": "trading_brake_deactivated",
        "entity_type": "trading_brake",
        "entity_id": scope,
        "before": {"active": was_active},
        "after": {"active": False},
        "message": f"{scope} trading brake deactivated",
    })
