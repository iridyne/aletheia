"""Guard resume operations with the same real-inference LLM probe used at boot."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup
from aletheia.config.loader import load_config


def summarize_resume_gate(result: dict[str, Any]) -> str:
    """Compact operator-facing summary that never includes secrets."""
    reason = result.get("reason") or "unknown"
    blockers = result.get("blockers") or []
    if blockers:
        return f"{reason}: blockers={'; '.join(str(blocker) for blocker in blockers)}"
    parts = []
    for row in result.get("results") or []:
        provider = row.get("provider", "?")
        if row.get("usable"):
            parts.append(f"{provider}=usable")
        else:
            parts.append(f"{provider}={row.get('error') or 'not_usable'}")
    detail = "; ".join(parts) if parts else "no enabled providers"
    return f"{reason}: {detail}"


def _persist_resumed(
    *,
    actor: str,
    source: str,
    generations: dict[str, str | None] | None = None,
):
    from aletheia.agent_runtime.pause_ownership import (
        LLM_STARTUP_OWNER,
        MANUAL_OWNER,
        release_pauses,
    )

    owners = {LLM_STARTUP_OWNER: None}
    if not (actor == "system" and source == "auto_resume"):
        owners[MANUAL_OWNER] = None
    if generations is not None:
        # Only owners observed before the async probe are releasable.  An owner
        # acquired during the probe must not be matched by a wildcard ``None``.
        owners = {owner: generations[owner] for owner in owners if owner in generations}
    return release_pauses(
        owners,
        resume_when_clear=True,
        actor=actor,
        source=source,
    )


def _remaining_owner_response(
    release,
    *,
    checked: int = 0,
    results: list[dict] | None = None,
) -> dict[str, Any] | None:
    blockers = release.blocking_owners
    if blockers:
        from aletheia.agent_runtime.pause_ownership import (
            active_pause_owners,
            pause_blocked_reason,
        )

        return {
            "allowed": False,
            "reason": pause_blocked_reason() or "active_pause_owners",
            "checked": checked,
            "results": list(results or []),
            "blockers": list(blockers),
            "pause_owners": active_pause_owners(),
        }
    return None


def _audit_resume_blocked(*, actor: str, source: str, cause: str, result: dict[str, Any]) -> None:
    try:
        from aletheia.files.jsonl import write_audit

        write_audit(
            {
                "actor": actor,
                "source": source,
                "event_type": "gateway_resume_blocked",
                "entity_type": "gateway",
                "entity_id": "gateway",
                "message": f"Resume blocked ({cause}): {summarize_resume_gate(result)}",
                "metadata": {
                    "pause_blockers": list(result.get("blockers") or []),
                    "pause_owners": list(result.get("pause_owners") or []),
                },
            }
        )
    except Exception:
        pass


async def resume_if_llm_available(
    *,
    actor: str,
    source: str,
    cause: str = "manual_resume",
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Resume only if every active runtime gate permits it.

    `max_retries` is forwarded to the probe; a manual operator resume passes 1 so
    the request returns within a single probe instead of the full startup window.
    """
    cfg = load_config()
    from aletheia.agent_runtime.pause_ownership import (
        LLM_STARTUP_OWNER,
        MANUAL_OWNER,
        STARTUP_BEHAVIOR_OWNER,
        active_pause_blocker_names,
        active_pause_owners,
        acquire_pause,
        pause_blocked_reason,
    )

    blocker_names = active_pause_blocker_names(cfg)
    release_targets = {LLM_STARTUP_OWNER}
    if not (actor == "system" and source == "auto_resume"):
        release_targets.add(MANUAL_OWNER)
    protected_blockers = tuple(name for name in blocker_names if name not in release_targets)
    if protected_blockers:
        reason = pause_blocked_reason(cfg) or "active_pause_owners"
        response = {
            "allowed": False,
            "reason": reason,
            "checked": 0,
            "results": [],
            "blockers": list(protected_blockers),
            "pause_owners": active_pause_owners(cfg),
        }
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        return response

    owner_generations = {
        owner: (
            cfg.runtime.pause_owners[owner].generation
            if cfg.runtime.pause_owners[owner].generation is not None
            else ""
        )
        for owner in release_targets
        if owner in cfg.runtime.pause_owners
    }

    if cfg.health.startup_behavior_gate_enabled:
        try:
            from aletheia.agent_runtime.startup_behavior_gate import evaluate_startup_gate

            startup_gate = evaluate_startup_gate(cfg)
        except Exception:
            startup_lease = load_config().runtime.pause_owners.get(STARTUP_BEHAVIOR_OWNER)
            if startup_lease is None or not startup_lease.active:
                acquire_pause(
                    STARTUP_BEHAVIOR_OWNER,
                    reason="startup_behavior_gate_evaluation_failed",
                    actor=actor,
                    source="startup_behavior_gate",
                )
            response = {
                "allowed": False,
                "reason": "startup_behavior_gate_unavailable",
                "checked": 0,
                "results": [],
                "blockers": [STARTUP_BEHAVIOR_OWNER],
                "pause_owners": active_pause_owners(load_config()),
            }
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
            return response

        if not bool(startup_gate.get("passed")):
            startup_lease = load_config().runtime.pause_owners.get(STARTUP_BEHAVIOR_OWNER)
            if startup_lease is None or not startup_lease.active:
                acquire_pause(
                    STARTUP_BEHAVIOR_OWNER,
                    reason="startup_behavior_checks_blocked",
                    actor=actor,
                    source="startup_behavior_gate",
                )
            response = {
                "allowed": False,
                "reason": "startup_behavior_gate_blocked",
                "checked": 0,
                "results": [],
                "blockers": list(startup_gate.get("blockers") or []) or [STARTUP_BEHAVIOR_OWNER],
                "pause_owners": active_pause_owners(load_config()),
            }
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
            return response

    if not cfg.health.require_llm_at_startup:
        release = _persist_resumed(
            actor=actor,
            source=source,
            generations=owner_generations,
        )
        blocked = _remaining_owner_response(release)
        if blocked is not None:
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=blocked)
            return blocked
        if not release.resumed:
            try:
                from aletheia.agent_runtime.scheduler import resume_scheduler

                if resume_scheduler() is False:
                    blocked = {
                        "allowed": False,
                        "reason": pause_blocked_reason(load_config()) or "active_pause_owners",
                        "checked": 0,
                        "results": [],
                        "blockers": list(active_pause_blocker_names(load_config())),
                    }
                    _audit_resume_blocked(actor=actor, source=source, cause=cause, result=blocked)
                    return blocked
            except Exception:
                blocked = {
                    "allowed": False,
                    "reason": "scheduler_resume_failed",
                    "checked": 0,
                    "results": [],
                    "blockers": [],
                }
                _audit_resume_blocked(actor=actor, source=source, cause=cause, result=blocked)
                return blocked
        return {
            "allowed": True,
            "reason": "llm_startup_gate_disabled",
            "checked": 0,
            "results": [],
            "pause_owners": active_pause_owners(load_config()),
        }

    # Call positionally-only when no override so test doubles that patch the probe
    # with a single-arg signature keep working (auto_resume integration tests).
    try:
        if max_retries is None:
            result = await probe_llm_providers_at_startup(cfg)
        else:
            result = await probe_llm_providers_at_startup(cfg, max_retries=max_retries)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A required dependency that could not be verified is itself a durable
        # blocker.  Never turn an orchestration exception into permission to
        # resume; acquire the LLM owner when no other owner already holds it.
        llm_lease = load_config().runtime.pause_owners.get(LLM_STARTUP_OWNER)
        if llm_lease is None or not llm_lease.active:
            acquire_pause(
                LLM_STARTUP_OWNER,
                reason="required_llm_probe_exception",
                actor=actor,
                source=source,
            )
        response = {
            "allowed": False,
            "reason": "llm_probe_exception",
            "checked": 0,
            "results": [],
            "blockers": [LLM_STARTUP_OWNER],
            "pause_owners": active_pause_owners(load_config()),
            "error_type": type(exc).__name__,
        }
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        return response
    response = {
        "allowed": bool(result.get("any_usable")),
        "reason": None if result.get("any_usable") else "llm_unavailable",
        "checked": int(result.get("checked") or 0),
        "results": list(result.get("results") or []),
        "pause_owners": active_pause_owners(load_config()),
    }
    if not response["allowed"]:
        response["blockers"] = [LLM_STARTUP_OWNER]
        llm_lease = load_config().runtime.pause_owners.get(LLM_STARTUP_OWNER)
        if llm_lease is None or not llm_lease.active:
            acquire_pause(
                LLM_STARTUP_OWNER,
                reason="llm_unavailable",
                actor=actor,
                source=source,
            )
            response["pause_owners"] = active_pause_owners(load_config())
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        return response

    try:
        from aletheia.agent_runtime.health import record_llm_success

        record_llm_success()
    except Exception:
        pass
    release = _persist_resumed(
        actor=actor,
        source=source,
        generations=owner_generations,
    )
    blocked = _remaining_owner_response(
        release,
        checked=response["checked"],
        results=response["results"],
    )
    if blocked is not None:
        response.update(blocked)
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
    elif not release.resumed:
        # A brake may have been released separately, leaving no releasable
        # behavioral owner to trigger the scheduler transition.  Route that
        # final action through the same public, owner-aware gate.
        try:
            from aletheia.agent_runtime.scheduler import resume_scheduler

            if resume_scheduler() is False:
                response.update({
                    "allowed": False,
                    "reason": pause_blocked_reason(load_config()) or "active_pause_owners",
                    "blockers": list(active_pause_blocker_names(load_config())),
                })
                _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        except Exception:
            response.update({"allowed": False, "reason": "scheduler_resume_failed"})
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
    return response


def resume_if_llm_available_sync(
    *,
    actor: str,
    source: str,
    cause: str = "manual_resume",
) -> dict[str, Any]:
    """Synchronous wrapper safe for CLI and rare event-loop callers."""
    coro = resume_if_llm_available(actor=actor, source=source, cause=cause)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
