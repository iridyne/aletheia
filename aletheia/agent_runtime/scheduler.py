"""Scheduler for periodic agent tasks using APScheduler."""

import asyncio
import logging
from apscheduler.schedulers.background import BackgroundScheduler

from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.scheduler")

_scheduler: BackgroundScheduler | None = None


def init_scheduler() -> BackgroundScheduler:
    """Initialize and start the background scheduler."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    _scheduler = BackgroundScheduler()

    _scheduler.add_job(
        _run_market_observer, "interval", minutes=15, id="market_observer",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_dreaming_agent, "cron", hour=2, minute=0, id="dreaming_daily",
        misfire_grace_time=300,
    )

    _scheduler.start()
    logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def _run_market_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    asyncio.run(_async_run("market_observer"))


def _run_dreaming_agent():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    asyncio.run(_async_run("dreaming_agent"))


async def _async_run(agent_name: str):
    from aletheia.agent_runtime.orchestrator import run_agent
    await run_agent(agent_name, trigger_type="scheduled")
