"""Auto-resume: periodic LLM probe that self-recovers from paused state.

When the LLM startup gate pauses the system (all providers unreachable), this
asyncio.Task runs independently of APScheduler (which is itself paused) and
periodically probes LLM availability. On success, it triggers the standard
resume_gate flow: persist paused=False, resume scheduler, audit the event.

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


async def _auto_resume_loop(interval_sec: int = 300) -> None:
    """Probe LLM every interval_sec; resume on first success."""

    while True:
        await asyncio.sleep(interval_sec)

        cfg = load_config()
        if not cfg.runtime.paused:
            logger.info("auto_resume: system already resumed — exiting loop")
            return

        if not cfg.health.auto_resume_enabled:
            logger.info("auto_resume: disabled in config — exiting loop")
            return

        try:
            result = await resume_if_llm_available(
                actor="system",
                source="auto_resume",
                cause="periodic_probe",
            )
            if result.get("allowed"):
                logger.info("auto_resume: LLM available — scheduler resumed")
                return
            logger.info(
                "auto_resume: LLM still unavailable (%s), retry in %ds",
                result.get("reason", "unknown"),
                interval_sec,
            )
        except Exception as e:
            logger.debug("auto_resume: probe exception: %s", e)


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
