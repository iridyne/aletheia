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


def _persist_resumed(*, actor: str, source: str):
    from aletheia.agent_runtime.pause_ownership import (
        LLM_STARTUP_OWNER,
        MANUAL_OWNER,
        release_pauses,
    )

    owners = {LLM_STARTUP_OWNER: None}
    if not (actor == "system" and source == "auto_resume"):
        owners[MANUAL_OWNER] = None
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
    if release.remaining_owners:
        return {
            "allowed": False,
            "reason": "active_pause_owners",
            "checked": checked,
            "results": list(results or []),
            "blockers": list(release.remaining_owners),
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

    if cfg.runtime.paused and cfg.runtime.paused_by_connectivity:
        response = {
            "allowed": False,
            "reason": "exchange_connectivity_pause",
            "checked": 0,
            "results": [],
            "blockers": ["exchange_connectivity"],
        }
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        return response

    if cfg.health.startup_behavior_gate_enabled:
        try:
            from aletheia.agent_runtime.startup_behavior_gate import evaluate_startup_gate

            startup_gate = evaluate_startup_gate(cfg)
        except Exception:
            response = {
                "allowed": False,
                "reason": "startup_behavior_gate_unavailable",
                "checked": 0,
                "results": [],
                "blockers": [],
            }
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
            return response

        if not bool(startup_gate.get("passed")):
            response = {
                "allowed": False,
                "reason": "startup_behavior_gate_blocked",
                "checked": 0,
                "results": [],
                "blockers": list(startup_gate.get("blockers") or []),
            }
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
            return response

    if not cfg.health.require_llm_at_startup:
        release = _persist_resumed(actor=actor, source=source)
        blocked = _remaining_owner_response(release)
        if blocked is not None:
            _audit_resume_blocked(actor=actor, source=source, cause=cause, result=blocked)
            return blocked
        return {
            "allowed": True,
            "reason": "llm_startup_gate_disabled",
            "checked": 0,
            "results": [],
        }

    # Call positionally-only when no override so test doubles that patch the probe
    # with a single-arg signature keep working (auto_resume integration tests).
    if max_retries is None:
        result = await probe_llm_providers_at_startup(cfg)
    else:
        result = await probe_llm_providers_at_startup(cfg, max_retries=max_retries)
    response = {
        "allowed": bool(result.get("any_usable")),
        "reason": None if result.get("any_usable") else "llm_unavailable",
        "checked": int(result.get("checked") or 0),
        "results": list(result.get("results") or []),
    }
    if not response["allowed"]:
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
        return response

    try:
        from aletheia.agent_runtime.health import record_llm_success

        record_llm_success()
    except Exception:
        pass
    release = _persist_resumed(actor=actor, source=source)
    blocked = _remaining_owner_response(
        release,
        checked=response["checked"],
        results=response["results"],
    )
    if blocked is not None:
        response.update(blocked)
        _audit_resume_blocked(actor=actor, source=source, cause=cause, result=response)
    return response


def resume_if_llm_available_sync(
    *,
    actor: str,
    source: str,
    cause: str = "manual_resume",
) -> dict[str, Any]:
    """Synchronous wrapper safe for CLI and rare event-loop callers."""
    from aletheia.agent_runtime.async_utils import run_coro_from_sync
    coro = resume_if_llm_available(actor=actor, source=source, cause=cause)
    return run_coro_from_sync(coro)
