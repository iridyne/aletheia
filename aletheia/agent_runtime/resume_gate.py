"""Guard resume operations with the same real-inference LLM probe used at boot."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from aletheia.agent_runtime.startup_gate import probe_llm_providers_at_startup
from aletheia.config.loader import load_config, save_config


def summarize_resume_gate(result: dict[str, Any]) -> str:
    """Compact operator-facing summary that never includes secrets."""
    reason = result.get("reason") or "unknown"
    parts = []
    for row in result.get("results") or []:
        provider = row.get("provider", "?")
        if row.get("usable"):
            parts.append(f"{provider}=usable")
        else:
            parts.append(f"{provider}={row.get('error') or 'not_usable'}")
    detail = "; ".join(parts) if parts else "no enabled providers"
    return f"{reason}: {detail}"


def _persist_resumed() -> None:
    cfg = load_config()
    cfg.runtime.paused = False
    save_config(cfg)
    try:
        from aletheia.agent_runtime.scheduler import resume_scheduler

        resume_scheduler()
    except Exception:
        pass


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
    """Resume only if the configured LLM gate is disabled or real inference works.

    `max_retries` is forwarded to the probe; a manual operator resume passes 1 so
    the request returns within a single probe instead of the full startup window.
    """
    cfg = load_config()
    if not cfg.health.require_llm_at_startup:
        _persist_resumed()
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
    _persist_resumed()
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
