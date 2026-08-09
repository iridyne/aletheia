"""Durable multi-owner coordination for the shared runtime pause state."""

from __future__ import annotations

from dataclasses import dataclass
import threading

from aletheia.config.loader import load_config, save_config
from aletheia.config.schema import PauseOwnerLease

MANUAL_OWNER = "manual"
CONNECTIVITY_OWNER = "exchange_connectivity"
STARTUP_BEHAVIOR_OWNER = "startup_behavior_gate"
LLM_STARTUP_OWNER = "llm_startup_gate"

_pause_transition_lock = threading.RLock()


@dataclass(frozen=True)
class PauseReleaseResult:
    released: bool
    resumed: bool
    remaining_owners: tuple[str, ...]
    generation: str | None = None
    released_owners: tuple[str, ...] = ()
    resume_failed: bool = False


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
                    "remaining_pause_owners": list(remaining),
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
    layered: bool | None = None,
) -> PauseOwnerLease:
    """Persist one owner lease and pause under the same process transition lock."""
    with _pause_transition_lock:
        cfg = load_config()
        lease = cfg.runtime.acquire_pause(
            owner,
            reason=reason,
            generation=generation,
            acquired_at=acquired_at,
        )
        save_config(cfg)
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
        remaining = tuple(sorted(cfg.runtime.pause_owners))
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
        cfg = load_config()
        released_leases: list[tuple[str, PauseOwnerLease]] = []
        for owner, generation in owners.items():
            lease = cfg.runtime.pause_owners.get(owner)
            if lease is None or (generation is not None and lease.generation != generation):
                continue
            if cfg.runtime.release_pause(owner, generation=generation):
                released_leases.append((owner, lease))

        if not released_leases:
            return PauseReleaseResult(
                released=False,
                resumed=False,
                remaining_owners=tuple(sorted(cfg.runtime.pause_owners)),
            )

        remaining = tuple(sorted(cfg.runtime.pause_owners))
        save_config(cfg)
        resumed = False
        if resume_when_clear and not remaining:
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
                    )
                save_config(cfg)
                return PauseReleaseResult(
                    released=False,
                    resumed=False,
                    remaining_owners=tuple(sorted(cfg.runtime.pause_owners)),
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
            generation=released_leases[0][1].generation,
            released_owners=tuple(owner for owner, _lease in released_leases),
        )


def active_pause_owners(cfg=None) -> list[dict[str, str | None]]:
    """Stable, secret-free projection for API and Doctor surfaces."""
    if cfg is None:
        cfg = load_config()
    return [
        {
            "owner": owner,
            "reason": lease.reason,
            "generation": lease.generation,
            "acquired_at": lease.acquired_at,
        }
        for owner, lease in sorted(cfg.runtime.pause_owners.items())
    ]
