"""Global kill switch — system-wide trading brake.

When active, the Risk Engine rejects every proposal (paper and live). State is
persisted to config (`runtime.kill_switch_active`) so the brake survives a
gateway restart. May be set by explicit human action (CLI / API / dashboard) or
auto-tripped by the risk engine when the daily-loss limit is breached.

This is a HARD control (guide §2.4, §21.3). The agent cannot deactivate it via
any LLM-callable tool — only human/system actors through this module.
"""

from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import BRAKE_OWNERS_BY_SCOPE
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
    from aletheia.agent_runtime.pause_ownership import _pause_transition_lock

    field = _field(scope)
    owner = BRAKE_OWNERS_BY_SCOPE[scope]
    source = {"system": "risk_engine", "telegram": "telegram"}.get(actor, "dashboard")
    with _pause_transition_lock:
        cfg = load_config()
        was_active = bool(getattr(cfg.runtime, field))
        lease = cfg.runtime.acquire_pause(
            owner,
            reason=reason or f"{scope}_trading_brake_active",
            source=source,
            scope=scope,
        )
        save_config(cfg)
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler

            pause_scheduler()
        except Exception:
            # The durable brake remains authoritative even if the in-process
            # scheduler is unavailable during startup or shutdown.
            pass
    write_audit({
        "actor": actor,
        "source": source,
        "event_type": "trading_brake_activated",
        "entity_type": "trading_brake",
        "entity_id": scope,
        "before": {"active": was_active},
        "after": {"active": True},
        "metadata": {
            "pause_owner": owner,
            "pause_generation": lease.generation,
            "pause_reason": lease.reason,
            "pause_acquired_at": lease.acquired_at,
            "pause_source": lease.source,
            "pause_scope": lease.scope,
        },
        "message": (
            f"{scope} trading brake activated: {reason}"
            if reason else f"{scope} trading brake activated"
        ),
    })


def deactivate(actor: str = "human", scope: str = "global") -> None:
    """Release the kill switch. Only human/system actors should call this."""
    from aletheia.agent_runtime.pause_ownership import _audit, _pause_transition_lock

    field = _field(scope)
    owner = BRAKE_OWNERS_BY_SCOPE[scope]
    with _pause_transition_lock:
        cfg = load_config()
        was_active = bool(getattr(cfg.runtime, field))
        lease = cfg.runtime.pause_owners.get(owner)
        if lease is not None:
            cfg.runtime.release_pause(owner, generation=lease.generation)
        else:
            setattr(cfg.runtime, field, False)
        remaining = tuple(sorted(cfg.runtime.pause_owners))
        save_config(cfg)
        if lease is not None:
            _audit(
                "pause_owner_released",
                owner=owner,
                lease=lease,
                remaining=remaining,
                actor=actor,
                source={"system": "risk_engine", "telegram": "telegram"}.get(actor, "dashboard"),
            )
    write_audit({
        "actor": actor,
        "source": {"system": "risk_engine", "telegram": "telegram"}.get(actor, "dashboard"),
        "event_type": "trading_brake_deactivated",
        "entity_type": "trading_brake",
        "entity_id": scope,
        "before": {"active": was_active},
        "after": {"active": False},
        "metadata": {
            "pause_owner": owner,
            "pause_generation": lease.generation if lease else None,
            "pause_reason": lease.reason if lease else None,
            "pause_acquired_at": lease.acquired_at if lease else None,
            "pause_source": lease.source if lease else None,
            "pause_scope": lease.scope if lease else scope,
            "pause_active": False,
        },
        "message": f"{scope} trading brake deactivated",
    })
