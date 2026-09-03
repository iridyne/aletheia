"""Global kill switch — system-wide trading brake.

When active, the Risk Engine rejects every proposal (paper and live). State is
persisted to config (`runtime.kill_switch_active`) so the brake survives a
gateway restart. May be set by explicit human action (CLI / API / dashboard) or
auto-tripped by the risk engine when the daily-loss limit is breached.

This is a HARD control (guide §2.4, §21.3). The agent cannot deactivate it via
any LLM-callable tool — only human/system actors through this module.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from aletheia.config.loader import load_config, mutate_config
from aletheia.config.schema import BRAKE_OWNERS_BY_SCOPE, PauseOwnerLease
from aletheia.files.jsonl import write_audit


_SCOPES = {"global", "demo", "live"}


class AuditPersistenceError(RuntimeError):
    """A risk-expanding brake release lacked authoritative audit durability."""


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
) -> bool:
    """Engage the kill switch. Idempotent; always writes an audit event."""
    from aletheia.agent_runtime.pause_ownership import (
        _pause_transition_lock,
        active_pause_blocker_names,
    )

    field = _field(scope)
    owner = BRAKE_OWNERS_BY_SCOPE[scope]
    source = {"system": "risk_engine", "telegram": "telegram"}.get(actor, "dashboard")
    # Use a capture list so Pyright can track the type narrowing out of the closure.
    _out: List[Tuple[bool, PauseOwnerLease]] = []
    with _pause_transition_lock:
        # Use mutate_config so we read the latest file under the write lock:
        # a stale snapshot written after activate() cannot remove the brake.
        def _do_activate(cfg):
            was = bool(getattr(cfg.runtime, field))
            current_lease = cfg.runtime.pause_owners.get(owner)
            if current_lease is None or not current_lease.active:
                acquired = cfg.runtime.acquire_pause(
                    owner,
                    reason=reason or f"{scope}_trading_brake_active",
                    source=source,
                    scope=scope,
                )
                _out.append((was, acquired))
            else:
                _out.append((was, current_lease))
            return True  # changed

        mutate_config(_do_activate)
        was_active, lease = _out[0]
        cfg = load_config()
        if owner in active_pause_blocker_names(cfg):
            try:
                from aletheia.agent_runtime.scheduler import pause_scheduler

                pause_scheduler()
            except Exception:
                # The durable brake remains authoritative even if the in-process
                # scheduler is unavailable during startup or shutdown.
                pass
    return write_audit({
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


def deactivate(actor: str = "human", scope: str = "global") -> bool:
    """Release the kill switch. Only human/system actors should call this."""
    from aletheia.agent_runtime.pause_ownership import (
        _pause_transition_lock,
        release_pauses,
    )

    field = _field(scope)
    owner = BRAKE_OWNERS_BY_SCOPE[scope]
    source = {"system": "risk_engine", "telegram": "telegram"}.get(actor, "dashboard")
    # Use a capture list so Pyright can track types out of the closure.
    _state: List[Tuple[bool, Optional[PauseOwnerLease]]] = []
    with _pause_transition_lock:
        # Read the latest state under the write lock before deciding what to release.
        def _read_state(cfg):
            was = bool(getattr(cfg.runtime, field))
            _state.append((was, cfg.runtime.pause_owners.get(owner)))
            return False  # read-only; release_pauses will do its own save

        mutate_config(_read_state)
        was_active, lease = _state[0]

        audit_ok = write_audit({
            "actor": actor,
            "source": source,
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
            },
            "message": f"{scope} trading brake deactivated",
        })
        if not audit_ok:
            raise AuditPersistenceError("trading_brake_deactivation_audit_failed")

        if lease is not None and lease.active:
            release_pauses(
                {owner: lease.generation if lease.generation is not None else ""},
                resume_when_clear=True,
                actor=actor,
                source=source,
            )
        else:
            # No active lease — clear the field directly via mutate_config.
            def _clear_field(cfg):
                setattr(cfg.runtime, field, False)
                return True
            mutate_config(_clear_field)
    return True
