"""Auto-resume: periodic LLM probe that self-recovers from paused state.

When the LLM startup gate pauses the system (all providers unreachable), this
asyncio.Task runs independently of APScheduler (which is itself paused) and
periodically probes LLM availability. On success, it triggers the standard
resume_gate flow: persist paused=False, resume scheduler, audit the event.

Stall detection: after N consecutive failures, classify the root cause
(secret_missing, auth_error, network_unreachable, unknown) and escalate to the
operator. Back off the probe interval to avoid log/notification spam.

The loop exits when:
- LLM becomes available and resume succeeds
- System is manually resumed (detected via config check)
- auto_resume is disabled in config
"""

from __future__ import annotations

import asyncio
import logging

from aletheia.config.loader import load_config
from aletheia.agent_runtime.resume_gate import resume_if_llm_available

logger = logging.getLogger("aletheia.auto_resume")

_active_task: asyncio.Task | None = None


def _classify_stall(results: list[dict]) -> str:
    """Classify root cause from probe results for actionable escalation."""
    from aletheia.config.secrets import get_secret

    cfg = load_config()
    default_provider = cfg.models.default_provider
    provider_cfg = cfg.models.providers.get(default_provider)
    if provider_cfg:
        secret_ref = getattr(provider_cfg, "api_key_secret_ref", None)
        if secret_ref and not get_secret(secret_ref):
            return "secret_missing"

    errors = [r.get("error", "") for r in results if not r.get("usable")]
    error_text = " ".join(errors).lower()

    if "401" in error_text or "403" in error_text or "unauthorized" in error_text:
        return "auth_error"
    if "getaddrinfo" in error_text or "dns" in error_text or "name or service not known" in error_text:
        return "network_unreachable"
    if "connection" in error_text or "timeout" in error_text or "refused" in error_text:
        return "network_unreachable"
    return "unknown"


_STALL_GUIDANCE = {
    "secret_missing": (
        "LLM API key is missing from the secrets store (keyring/encrypted file). "
        "Set it via Settings -> Model Provider, or `aletheia secrets set <key>`."
    ),
    "auth_error": (
        "LLM provider returns 401/403 — the API key may be expired or invalid. "
        "Update via Settings -> Model Provider."
    ),
    "network_unreachable": (
        "Cannot reach LLM provider endpoint — check network/DNS/proxy configuration "
        "in Settings -> Network."
    ),
    "unknown": (
        "LLM provider probe fails for an unknown reason. "
        "Run `aletheia model probe` for details."
    ),
}


def _escalate_stall(cause: str, consecutive_failures: int) -> None:
    """Notify operator about permanently stuck pause with actionable guidance."""
    guidance = _STALL_GUIDANCE.get(cause, _STALL_GUIDANCE["unknown"])
    message = (
        f"Auto-resume stalled after {consecutive_failures} consecutive failures. "
        f"Root cause: {cause}. {guidance}"
    )
    logger.warning("auto_resume stall escalation: %s", message)

    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            "Auto-Resume Stalled",
            message,
            source="auto_resume",
            event_type=None,
            audit=False,
            error_log=True,
        )
    except Exception:
        pass

    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system",
            "source": "auto_resume",
            "event_type": "auto_resume_stall_escalated",
            "entity_type": "gateway",
            "entity_id": "gateway",
            "message": message,
            "metadata": {"cause": cause, "consecutive_failures": consecutive_failures},
        })
    except Exception:
        pass


async def _auto_resume_loop(interval_sec: int = 300) -> None:
    """Probe LLM every interval_sec; resume on first success. Escalate on stall."""
    consecutive_failures = 0
    current_interval = interval_sec
    last_escalation_at = 0  # failure count at last escalation

    while True:
        await asyncio.sleep(current_interval)

        cfg = load_config()
        if not cfg.runtime.paused:
            logger.info("auto_resume: system already resumed — exiting loop")
            return

        if not cfg.health.auto_resume_enabled:
            logger.info("auto_resume: disabled in config — exiting loop")
            return

        stall_threshold = cfg.health.auto_resume_stall_threshold
        backoff_factor = cfg.health.auto_resume_stall_backoff_factor
        max_interval = cfg.health.auto_resume_max_interval_sec

        try:
            result = await resume_if_llm_available(
                actor="system",
                source="auto_resume",
                cause="periodic_probe",
            )
            if result.get("allowed"):
                logger.info("auto_resume: LLM available — scheduler resumed")
                return
            if (
                result.get("reason") == "active_pause_owners"
                and "llm_startup_gate" not in (result.get("blockers") or [])
            ):
                logger.info(
                    "auto_resume: LLM pause released — trading remains paused by %s",
                    ", ".join(result.get("blockers") or []),
                )
                return

            consecutive_failures += 1
            probe_results = result.get("results", [])

            if consecutive_failures >= stall_threshold and (
                consecutive_failures - last_escalation_at >= stall_threshold
            ):
                cause = _classify_stall(probe_results)
                _escalate_stall(cause, consecutive_failures)
                last_escalation_at = consecutive_failures
                current_interval = min(
                    int(current_interval * backoff_factor), max_interval
                )
                logger.info(
                    "auto_resume: stall detected (cause=%s), backed off to %ds",
                    cause, current_interval,
                )
            else:
                logger.info(
                    "auto_resume: LLM still unavailable (%s), retry in %ds",
                    result.get("reason", "unknown"),
                    current_interval,
                )
        except Exception as e:
            consecutive_failures += 1
            probe_results = [{"provider": "unknown", "usable": False, "error": str(e)}]
            cause = _classify_stall(probe_results)
            if consecutive_failures >= stall_threshold and (
                consecutive_failures - last_escalation_at >= stall_threshold
            ):
                _escalate_stall(cause, consecutive_failures)
                last_escalation_at = consecutive_failures
                current_interval = min(
                    int(current_interval * backoff_factor), max_interval
                )
                logger.info(
                    "auto_resume: stall detected (cause=%s), backed off to %ds",
                    cause, current_interval,
                )
            else:
                logger.warning(
                    "auto_resume: probe exception (%s), retry in %ds",
                    e, current_interval,
                )


def start_auto_resume_task(interval_sec: int = 300) -> asyncio.Task | None:
    """Launch the auto-resume loop as a fire-and-forget asyncio.Task.

    Safe to call from within a running event loop (e.g. uvicorn lifespan).
    Returns the Task (for testing) or None if no loop is running.
    """
    global _active_task
    if _active_task is not None and not _active_task.done():
        logger.debug("auto_resume: task already active, skipping")
        return _active_task

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("auto_resume: no running event loop — cannot start")
        return None

    _active_task = loop.create_task(
        _auto_resume_loop(interval_sec),
        name="auto_resume_probe",
    )
    logger.info("auto_resume: started probe loop (interval=%ds)", interval_sec)
    return _active_task


async def stop_auto_resume_task() -> None:
    """Stop the module-owned probe loop and consume its terminal state."""
    global _active_task
    task = _active_task
    _active_task = None
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        logger.debug("auto_resume: task ended during shutdown: %s", exc)
