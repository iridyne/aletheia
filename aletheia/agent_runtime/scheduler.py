"""Scheduler for periodic agent + market-data tasks using APScheduler."""

import asyncio
import logging
from apscheduler.schedulers.background import BackgroundScheduler

from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.scheduler")

_scheduler: BackgroundScheduler | None = None


def init_scheduler() -> BackgroundScheduler | None:
    """Initialize and start the background scheduler.

    Returns None if scheduler is disabled in config. Honors runtime.paused at boot.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    cfg = load_config()
    if not cfg.runtime.scheduler_enabled:
        logger.info("Scheduler disabled by config; not starting.")
        return None

    _scheduler = BackgroundScheduler()

    _scheduler.add_job(
        _run_market_data, "interval", minutes=5, id="market_data",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_market_observer, "interval", minutes=15, id="market_observer",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_dreaming_agent, "cron", hour=2, minute=0, id="dreaming_daily",
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _run_strategy_designer, "cron", hour=1, minute=0, id="strategy_designer_daily",
        misfire_grace_time=300,
    )
    _scheduler.add_job(
        _run_reconciliation, "interval", minutes=2, id="reconciliation",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_portfolio_snapshot, "interval", minutes=10, id="portfolio_snapshot",
        misfire_grace_time=120,
    )
    _scheduler.add_job(
        _run_exchange_sync, "interval", minutes=5, id="exchange_sync",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_paper_trader, "interval", minutes=15, id="paper_trader",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_position_manager, "interval", minutes=5, id="position_manager",
        misfire_grace_time=60,
    )
    _scheduler.add_job(
        _run_cleanup, "cron", hour=3, minute=17, id="cleanup",
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _run_health_check, "interval", minutes=10, id="health_check",
        misfire_grace_time=120,
    )

    _scheduler.start()
    # If config says paused at boot, immediately pause the scheduler.
    if cfg.runtime.paused:
        _scheduler.pause()
        logger.info("Scheduler started PAUSED (runtime.paused=true) with %d jobs", len(_scheduler.get_jobs()))
    else:
        logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def pause_scheduler() -> None:
    """Actually pause job execution (not just skip via config flag)."""
    if _scheduler:
        _scheduler.pause()
        logger.info("Scheduler paused")


def resume_scheduler() -> None:
    if _scheduler:
        _scheduler.resume()
        logger.info("Scheduler resumed")


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def _run_market_data():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.trading.market_data import collect_market_snapshots
        asyncio.run(collect_market_snapshots())
    except Exception as e:
        logger.warning("market_data job failed: %s", e)


def _run_market_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        asyncio.run(_async_run("market_observer"))
    except Exception as e:
        logger.warning("market_observer job failed: %s", e)


def _run_dreaming_agent():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        asyncio.run(_async_run("dreaming_agent"))
    except Exception as e:
        logger.warning("dreaming_agent job failed: %s", e)


def _run_strategy_designer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        asyncio.run(_async_run("strategy_designer"))
    except Exception as e:
        logger.warning("strategy_designer job failed: %s", e)


def _run_reconciliation():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    # Only meaningful in live mode; reconcile() itself early-returns otherwise.
    if cfg.exchange.mode != "live":
        return
    try:
        from aletheia.execution.reconciliation import reconcile
        reconcile()
    except Exception as e:
        logger.warning("reconciliation job failed: %s", e)


def _run_exchange_sync():
    """Periodic refresh of the cached real-account view (read-only)."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.portfolio.exchange_sync import sync_exchange_account
        sync_exchange_account()
    except Exception as e:
        logger.warning("exchange_sync job failed: %s", e)


def _run_portfolio_snapshot():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.portfolio.pnl import compute_and_store_snapshot
        compute_and_store_snapshot(execution_mode=("bitget_live_limited" if cfg.exchange.mode == "live" else "paper_internal"))
    except Exception as e:
        logger.warning("portfolio_snapshot job failed: %s", e)


def _run_paper_trader():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    # Gate A: autonomous paper trading is default-OFF and must be explicitly enabled.
    if not cfg.agent.autonomous_paper_trading:
        return
    # Only modes whose permission allows paper_tool (Research/ReadOnly excluded).
    if cfg.agent.permission_mode not in ("PaperAuto", "AllowEditing", "Auto"):
        return
    try:
        asyncio.run(_async_run("paper_trader"))
    except Exception as e:
        logger.warning("paper_trader job failed: %s", e)


def _run_position_manager():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.portfolio.position_manager import evaluate_open_positions
        evaluate_open_positions()
    except Exception as e:
        logger.warning("position_manager job failed: %s", e)


def _run_cleanup():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.storage import repositories as repo
        from aletheia.files.jsonl import write_audit
        r = cfg.retention
        counts = {
            "agent_runs": repo.prune_agent_runs(r.agent_runs_days),
            "run_steps": repo.prune_run_steps(r.run_steps_days),
            "tool_calls": repo.prune_tool_calls(r.tool_calls_days),
            "market_snapshots": repo.prune_market_snapshots(r.market_snapshots_days),
            "portfolio_snapshots": repo.prune_portfolio_snapshots(r.portfolio_snapshots_days),
        }
        write_audit({
            "actor": "system", "source": "scheduler",
            "event_type": "data_pruned", "entity_type": "telemetry",
            "message": f"Pruned old telemetry: {counts}",
        })
    except Exception as e:
        logger.warning("cleanup job failed: %s", e)


def _run_health_check():
    cfg = load_config()
    if cfg.runtime.paused or not cfg.health.enabled:
        return
    try:
        from aletheia.agent_runtime.health import run_health_check
        run_health_check()
    except Exception as e:
        logger.warning("health_check job failed: %s", e)


async def _async_run(agent_name: str):
    from aletheia.agent_runtime.orchestrator import run_agent
    await run_agent(agent_name, trigger_type="scheduled")
