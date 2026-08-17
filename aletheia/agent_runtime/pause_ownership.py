"""Durable multi-owner coordination for the shared runtime pause state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import threading

from aletheia.config import loader as _config_loader
from aletheia.config.schema import PauseOwnerLease

MANUAL_OWNER = "manual"
CONNECTIVITY_OWNER = "exchange_connectivity"
STARTUP_BEHAVIOR_OWNER = "startup_behavior_gate"
LLM_STARTUP_OWNER = "llm_startup_gate"

_pause_transition_lock = threading.RLock()


def _generation_matches(current: str | None, requested: str | None) -> bool:
    """Match exact generations, with ``""`` reserved for legacy ``None``."""
    return (
        requested is None
        or current == requested
        or (requested == "" and current is None)
    )


def _active_owner_names(cfg) -> tuple[str, ...]:
    return tuple(
        sorted(
            owner
            for owner, lease in cfg.runtime.pause_owners.items()
            if lease.active
        )
    )


def _lease_age_seconds(lease: PauseOwnerLease) -> float | None:
    """Return a non-negative age, or ``None`` when legacy time is unknown."""
    if not lease.acquired_at:
        return None
    try:
        acquired_at = datetime.fromisoformat(lease.acquired_at.replace("Z", "+00:00"))
        if acquired_at.tzinfo is None:
            acquired_at = acquired_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - acquired_at).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class PauseReleaseResult:
    released: bool
    resumed: bool
    remaining_owners: tuple[str, ...]
    generation: str | None = None
    released_owners: tuple[str, ...] = ()
    resume_failed: bool = False
    blocking_owners: tuple[str, ...] = ()


def _audit(
    event_type: str,
    *,
    owner: str,
    lease: PauseOwnerLease,
    remaining: tuple[str, ...],
    actor: str,
    source: str,
) -> None:
    try:
        from aletheia.files.jsonl import write_audit

        write_audit(
            {
                "actor": actor,
                "source": source,
                "event_type": event_type,
                "entity_type": "gateway",
                "entity_id": "gateway",
                "message": f"Pause owner {owner} {event_type.removeprefix('pause_owner_')}",
                "metadata": {
                    "pause_owner": owner,
                    "pause_generation": lease.generation,
                    "pause_reason": lease.reason,
                    "pause_acquired_at": lease.acquired_at,
                    "pause_source": lease.source,
                    "pause_scope": lease.scope,
                    "pause_active": event_type != "pause_owner_released",
                    "pause_released": event_type == "pause_owner_released",
                    "pause_released_at": (
                        datetime.now(timezone.utc).isoformat()
                        if event_type == "pause_owner_released"
                        else None
                    ),
                    "remaining_pause_owners": list(remaining),
                },
            }
        )
    except Exception:
        pass


def _audit_release_rejected(
    *,
    owner: str,
    requested_generation: str | None,
    current: PauseOwnerLease | None,
    actor: str,
    source: str,
) -> None:
    """Persist stale/unknown release attempts without leaking config values."""
    try:
        from aletheia.files.jsonl import write_audit

        write_audit(
            {
                "actor": actor,
                "source": source,
                "event_type": "pause_owner_release_rejected",
                "entity_type": "gateway",
                "entity_id": "gateway",
                "message": f"Pause owner {owner} release rejected (stale or unknown generation)",
                "metadata": {
                    "pause_owner": owner,
                    "requested_pause_generation": requested_generation,
                    "current_pause_generation": current.generation if current else None,
                    "current_pause_reason": current.reason if current else None,
                    "current_pause_acquired_at": current.acquired_at if current else None,
                    "current_pause_source": current.source if current else None,
                    "current_pause_scope": current.scope if current else None,
                },
            }
        )
    except Exception:
        pass


def acquire_pause(
    owner: str,
    *,
    reason: str,
    generation: str | None = None,
    acquired_at: str | None = None,
    actor: str = "system",
    source: str = "pause_ownership",
    scope: str | None = None,
    layered: bool | None = None,
) -> PauseOwnerLease:
    """Persist one owner lease and pause under the same process transition lock."""
    with _pause_transition_lock:
        cfg = _config_loader.load_config()
        lease = cfg.runtime.acquire_pause(
            owner,
            reason=reason,
            generation=generation,
            acquired_at=acquired_at,
            source=source,
            scope=scope,
        )
        _config_loader.save_config(cfg)
        try:
            from aletheia.agent_runtime.scheduler import pause_scheduler

            if layered is None:
                pause_scheduler()
            else:
                pause_scheduler(layered=layered)
        except Exception:
            # The durable owner remains fail-closed even if the in-process
            # scheduler hook is unavailable; jobs also consult runtime.paused.
            pass
        remaining = _active_owner_names(cfg)
        _audit(
            "pause_owner_acquired",
            owner=owner,
            lease=lease,
            remaining=remaining,
            actor=actor,
            source=source,
        )
        return lease


def release_pause(
    owner: str,
    *,
    generation: str | None = None,
    resume_when_clear: bool = False,
    actor: str = "system",
    source: str = "pause_ownership",
) -> PauseReleaseResult:
    """Release only one matching lease and optionally resume after the final owner.

    The config mutation and scheduler decision share one process lock, so two
    overlapping recovery paths cannot both observe themselves as the final
    releaser and call ``resume_scheduler`` twice.
    """
    return release_pauses(
        {owner: generation},
        resume_when_clear=resume_when_clear,
        actor=actor,
        source=source,
    )


def release_pauses(
    owners: dict[str, str | None],
    *,
    resume_when_clear: bool = False,
    actor: str = "system",
    source: str = "pause_ownership",
) -> PauseReleaseResult:
    """Atomically release matching generations for one or more owners."""
    with _pause_transition_lock:
        cfg = _config_loader.load_config()
        released_leases: list[tuple[str, PauseOwnerLease]] = []
        rejected: list[tuple[str, str | None, PauseOwnerLease | None]] = []
        for owner, generation in owners.items():
            lease = cfg.runtime.pause_owners.get(owner)
            if (
                lease is None
                or not lease.active
                or not _generation_matches(lease.generation if lease else None, generation)
            ):
                rejected.append((owner, generation, lease))
                continue
            if cfg.runtime.release_pause(owner, generation=generation):
                released_leases.append((owner, lease))

        for owner, generation, current in rejected:
            _audit_release_rejected(
                owner=owner,
                requested_generation=generation,
                current=current,
                actor=actor,
                source=source,
            )

        if not released_leases:
            blocking = active_pause_blocker_names(cfg)
            return PauseReleaseResult(
                released=False,
                resumed=False,
                remaining_owners=_active_owner_names(cfg),
                blocking_owners=blocking,
            )

        remaining = _active_owner_names(cfg)
        blocking = active_pause_blocker_names(cfg)
        _config_loader.save_config(cfg)
        resumed = False
        if resume_when_clear and not blocking:
            from aletheia.agent_runtime.scheduler import resume_scheduler

            try:
                if resume_scheduler() is False:
                    raise RuntimeError("scheduler_resume_blocked_by_pause_owner")
                resumed = True
            except Exception:
                # Persistence already recorded the release. Restore the exact
                # leases so config remains fail-closed and the owning recovery
                # loop can retry instead of reporting a false resume.
                for owner, lease in released_leases:
                    cfg.runtime.acquire_pause(
                        owner,
                        reason=lease.reason,
                        generation=lease.generation,
                        acquired_at=lease.acquired_at,
                        source=lease.source,
                        scope=lease.scope,
                    )
                _config_loader.save_config(cfg)
                return PauseReleaseResult(
                    released=False,
                    resumed=False,
                    remaining_owners=_active_owner_names(cfg),
                    blocking_owners=active_pause_blocker_names(cfg),
                    generation=released_leases[0][1].generation,
                    resume_failed=True,
                )
        for owner, lease in released_leases:
            _audit(
                "pause_owner_released",
                owner=owner,
                lease=lease,
                remaining=remaining,
                actor=actor,
                source=source,
            )
        return PauseReleaseResult(
            released=True,
            resumed=resumed,
            remaining_owners=remaining,
            blocking_owners=blocking,
            generation=released_leases[0][1].generation,
            released_owners=tuple(owner for owner, _lease in released_leases),
        )


def _current_behavior_scope(cfg) -> str:
    """Map the configured execution pool to the mode-scoped brake."""
    exchange = getattr(cfg, "exchange", None)
    return "live" if getattr(exchange, "mode", None) == "live" else "demo"


def active_pause_blocker_names(cfg=None) -> tuple[str, ...]:
    """Return active owners that can block the current behavioral pool.

    A mode-scoped brake remains visible in ``active_pause_owners`` but only
    blocks a resume for its matching pool. Global and non-brake owners always
    block. This preserves the Risk contract that demo/live brakes are scoped.
    """
    if cfg is None:
        cfg = _config_loader.load_config()
    current_scope = _current_behavior_scope(cfg)
    cfg.runtime.set_pause_scope(current_scope)
    return tuple(
        sorted(
            owner
            for owner, lease in cfg.runtime.pause_owners.items()
            if lease.active
            and (lease.scope is None or lease.scope in {"global", current_scope})
        )
    )


def pause_blocked_reason(cfg=None) -> str | None:
    """Return the stable operator-facing reason for the current blocker set."""
    blockers = active_pause_blocker_names(cfg)
    if not blockers:
        return None
    if any(owner in {"kill_switch", "demo_brake", "live_brake"} for owner in blockers):
        return "trading_brake_active"
    if "exchange_connectivity" in blockers:
        return "exchange_connectivity_pause"
    return "active_pause_owners"


def active_pause_owners(cfg=None) -> list[dict[str, object]]:
    """Stable, secret-free projection for API and Doctor surfaces."""
    if cfg is None:
        cfg = _config_loader.load_config()
    return [
        {
            "owner": owner,
            "reason": lease.reason,
            "generation": lease.generation,
            "acquired_at": lease.acquired_at,
            "age_seconds": _lease_age_seconds(lease),
            "source": lease.source,
            "scope": lease.scope,
            "active": lease.active,
            "blocking": owner in active_pause_blocker_names(cfg),
        }
        for owner, lease in sorted(cfg.runtime.pause_owners.items())
        if lease.active
    ]
