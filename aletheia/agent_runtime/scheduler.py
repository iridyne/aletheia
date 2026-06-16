"""Scheduler for periodic agent + market-data tasks using APScheduler."""

import asyncio
from functools import partial
import logging
import threading
from datetime import datetime, timedelta
from typing import Awaitable
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.scheduler")

_scheduler: BackgroundScheduler | None = None
_SCHEDULER_JOB_LOCK = threading.RLock()

# Hard time budgets for scheduler jobs that depend on external IO or LLM calls.
# APScheduler's max_instances=1 prevents overlap, but it cannot recover a job
# that stays inside an awaited network call forever. Timeboxing these coroutines
# keeps one stuck collector/model request from starving the scheduler pools and
# holding SQLite thread-local state indefinitely.
_ASYNC_JOB_TIMEOUT_SEC: dict[str, float] = {
    "market_data": 90.0,
    "market_observer": 240.0,
    "dreaming_agent": 300.0,
    "strategy_designer": 300.0,
    "paper_trader": 300.0,
    "pi_trading_decision": 300.0,
    "stock_observer": 240.0,
    "prediction_observer": 240.0,
    "divergence_scanner": 60.0,
    "post_trade_reviewer": 300.0,
    "risk_critic": 300.0,
    "universe_refresh": 90.0,
    "data_collectors": 45.0,
}


# Isolate latency-sensitive infrastructure jobs from long LLM/agent jobs. A
# single APScheduler default ThreadPool lets a slow post-trade review or shell
# provider call occupy the same queue that market data, health, and operator
# support jobs depend on. Multiple small pools preserve the SQLite concurrency
# cap while preventing starvation of the boot catch-up observer.
_EXECUTOR_WORKERS = {
    "default": 2,
    # SQLite has one writer. In a local-first MVP, a conservative IO lane is
    # more reliable than parallel scheduled writers that frequently collide and
    # cascade into ``database is locked`` across unrelated jobs.
    "io": 1,
    "observer": 1,
    # LLM-backed jobs also write run logs/artifacts. Keep them serial so a slow
    # model call cannot make two agent jobs compete for SQLite at once.
    "agents": 1,
}


def _run_job_guarded(job_id: str, fn):
    """Run one scheduled job and release any leaked SQLite writer state.

    The global _SCHEDULER_JOB_LOCK is held ONLY for the pre/post-job DB cleanup
    (rollback_if_in_transaction + close_db) — NOT around the job body. Network
    I/O, LLM calls, and asyncio.run() all execute outside the lock. SQLite
    single-writer ordering falls back to busy_timeout=30000 (WAL pillar 1),
    which is the correct mechanism for cross-writer contention.

    Previously the lock wrapped the entire job — including 45–240s async timeouts.
    On Windows, if asyncio.run() teardown hung (ProactorEventLoop IOCP drain),
    the executor thread held the RLock forever, starving every other job.
    """
    from aletheia.storage.sqlite import close_db, rollback_if_in_transaction

    with _SCHEDULER_JOB_LOCK:
        if rollback_if_in_transaction():
            logger.warning("Rolled back stale SQLite transaction before scheduler job %s", job_id)
    try:
        return fn()
    finally:
        with _SCHEDULER_JOB_LOCK:
            if rollback_if_in_transaction():
                logger.warning("Rolled back leaked SQLite transaction after scheduler job %s", job_id)
            close_db()


def _guarded(job_id: str, fn):
    return partial(_run_job_guarded, job_id, fn)


def _run_async_with_timeout(job_id: str, awaitable: Awaitable[object]) -> object:
    """Run an async job with a hard wall-clock bound.

    Runs the coroutine in a dedicated daemon thread so that a hung asyncio.run()
    teardown (Windows ProactorEventLoop IOCP drain) cannot pin the calling
    APScheduler executor thread indefinitely. The caller gets back control after
    timeout + 15s grace regardless of internal loop state.
    """
    import concurrent.futures

    timeout = _ASYNC_JOB_TIMEOUT_SEC[job_id]
    thread_timeout = timeout + 15.0

    def _run():
        return asyncio.run(asyncio.wait_for(awaitable, timeout=timeout))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(_run)
    try:
        return future.result(timeout=thread_timeout)
    except concurrent.futures.TimeoutError:
        logger.error(
            "job %s: asyncio.run() teardown hung after %.0fs — "
            "executor thread abandoned (ProactorEventLoop IOCP drain)",
            job_id, thread_timeout,
        )
        # shutdown(wait=False): do NOT block on the hung daemon thread.
        # The thread will die when the process exits (daemon semantics).
        pool.shutdown(wait=False)
        raise TimeoutError(f"job {job_id} teardown hung after {thread_timeout}s")
    finally:
        pool.shutdown(wait=False)


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

    # Keep each pool small for SQLite, but split queues by job class:
    # - io: short read/write infrastructure jobs (market data, health, sync)
    # - observer: the boot catch-up market observer; must not starve behind LLM jobs
    # - agents: slower LLM / review / repair jobs
    # Each job is wrapped by _run_job_guarded(), which rolls back leaked
    # per-thread transactions and closes thread-local SQLite connections at job
    # end so one failed job cannot pin the writer lock for the next jobs.
    _scheduler = BackgroundScheduler(
        executors={name: ThreadPoolExecutor(workers) for name, workers in _EXECUTOR_WORKERS.items()},
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    _scheduler.add_job(
        _guarded("market_data", _run_market_data), "interval", minutes=5, id="market_data",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("market_observer", _run_market_observer), "interval", minutes=15, id="market_observer",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=75),
        executor="observer",
    )
    _scheduler.add_job(
        _guarded("dreaming_daily", _run_dreaming_agent), "cron", hour=2, minute=0, id="dreaming_daily",
        misfire_grace_time=300, executor="agents",
    )
    _scheduler.add_job(
        _guarded("strategy_designer_daily", _run_strategy_designer), "cron", hour=1, minute=0, id="strategy_designer_daily",
        misfire_grace_time=300, executor="agents",
    )
    _scheduler.add_job(
        _guarded("reconciliation", _run_reconciliation), "interval", minutes=2, id="reconciliation",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("portfolio_snapshot", _run_portfolio_snapshot), "interval", minutes=10, id="portfolio_snapshot",
        misfire_grace_time=120, executor="io",
    )
    _scheduler.add_job(
        _guarded("exchange_sync", _run_exchange_sync), "interval", minutes=5, id="exchange_sync",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=10),
        executor="io",
    )
    _scheduler.add_job(
        _guarded("paper_trader", _run_paper_trader), "interval", minutes=15, id="paper_trader",
        misfire_grace_time=60, executor="agents",
    )
    _scheduler.add_job(
        _guarded("pi_trading_decision", _run_pi_trading_decision), "interval", minutes=15,
        id="pi_trading_decision", misfire_grace_time=120,
        next_run_time=datetime.now() + timedelta(minutes=7), executor="agents",
    )
    _scheduler.add_job(
        _guarded("position_manager", _run_position_manager), "interval", minutes=5, id="position_manager",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("position_reconcile", _run_position_reconcile), "interval", minutes=5, id="position_reconcile",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=20),
        executor="io",
    )
    _scheduler.add_job(
        _guarded("cleanup", _run_cleanup), "cron", hour=3, minute=17, id="cleanup",
        misfire_grace_time=600, executor="io",
    )
    _scheduler.add_job(
        _guarded("health_check", _run_health_check), "interval", minutes=10, id="health_check",
        misfire_grace_time=120, executor="io",
    )
    # US equity observer — runs every 30 min Mon–Fri during US market hours
    # (14:30–21:00 UTC = 09:30–16:00 Eastern). The job function enforces the
    # time gate so off-hours fires are silent no-ops (stale daily data).
    _scheduler.add_job(
        _guarded("stock_observer", _run_stock_observer), "cron",
        day_of_week="mon-fri", hour="14-20", minute="*/30",
        id="stock_observer", misfire_grace_time=300, executor="agents",
    )
    # Prediction market observer — runs every 30 min around the clock.
    _scheduler.add_job(
        _guarded("prediction_observer", _run_prediction_observer), "interval", minutes=30,
        id="prediction_observer", misfire_grace_time=120, executor="agents",
    )
    # Post-trade reviewer — reviews closed positions every 4 hours.
    # Delay first run by 5 min so LLM-heavy jobs don't compete with market_data
    # at startup (ThreadPool=3, and LLM calls can block a slot for minutes).
    _scheduler.add_job(
        _guarded("post_trade_reviewer", _run_post_trade_reviewer), "interval", hours=4,
        id="post_trade_reviewer", misfire_grace_time=300,
        next_run_time=datetime.now() + timedelta(minutes=5), executor="agents",
    )
    # Risk critic — reviews risk decisions and writes lessons every 6 hours.
    _scheduler.add_job(
        _guarded("risk_critic", _run_risk_critic), "interval", hours=6,
        id="risk_critic", misfire_grace_time=300,
        next_run_time=datetime.now() + timedelta(minutes=10), executor="agents",
    )
    # Universe Generator — dynamic asset pool refresh.
    _scheduler.add_job(
        _guarded("universe_refresh", _run_universe_refresh), "interval", minutes=10,
        id="universe_refresh", misfire_grace_time=120, executor="io",
    )
    # 9-domain market data collectors (microstructure/derivatives/macro).
    # Single job, runs every 2 min, uses internal phase counter to control
    # per-domain frequency (orderbook+funding every 2min; macro every 10min).
    _scheduler.add_job(
        _guarded("data_collectors", _run_data_collectors), "interval", minutes=2,
        id="data_collectors", misfire_grace_time=60,
        next_run_time=datetime.now() + timedelta(seconds=30), executor="io",
    )
    # Strategy backtest orchestrator — daily evaluation of all active strategies.
    # Runs after strategy_designer (01:00) so newly generated strategies get tested.
    _scheduler.add_job(
        _guarded("backtest_orchestrator", _run_backtest_orchestrator), "cron", hour=4, minute=0,
        id="backtest_orchestrator", misfire_grace_time=600, executor="agents",
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
    # If the scheduler never came up (init_scheduler raised during lifespan and
    # was only warned, leaving _scheduler is None), a bare `if _scheduler` resume
    # silently no-ops while the /api/gateway/resume route still reports success —
    # so the operator thinks the loop resumed but no job is ever scheduled. Build
    # it on demand. By now the caller has already persisted runtime.paused=False,
    # so init_scheduler() won't boot it paused.
    global _scheduler
    if _scheduler is None:
        init_scheduler()
        if _scheduler is None:
            logger.warning("resume_scheduler: scheduler unavailable (disabled or init failed)")
        return  # init_scheduler starts unpaused when runtime.paused is False
    _scheduler.resume()
    logger.info("Scheduler resumed")


def reschedule_universe_refresh(interval_min: int) -> bool:
    """Apply a new universe_refresh interval to the live scheduler.

    APScheduler's IntervalTrigger is immutable once added, so changing
    ``universe.refresh_interval_min`` in config does nothing until the next
    gateway restart unless the job is explicitly rescheduled. Called from the
    settings route after a config save so the UI takes effect immediately.

    Returns True if the live job was rescheduled, False if there was no running
    scheduler/job to update (the new interval still applies at next boot via
    config). Never raises into the request path.
    """
    if _scheduler is None:
        return False
    try:
        with _SCHEDULER_JOB_LOCK:
            if _scheduler.get_job("universe_refresh") is None:
                return False
            _scheduler.reschedule_job(
                "universe_refresh", trigger="interval", minutes=interval_min,
            )
        logger.info("Rescheduled universe_refresh to every %d min", interval_min)
        return True
    except Exception as e:  # noqa: BLE001 — config is already saved; never 500 the save
        logger.warning("Failed to reschedule universe_refresh (applies at next restart): %s", e)
        return False


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def status_snapshot() -> dict:
    """Return an honest, read-only snapshot of the in-process scheduler.

    `runtime.scheduler_enabled` is only the operator's desired configuration.
    This snapshot reports the actual APScheduler object state so the dashboard
    does not show "active" when init failed or the scheduler is paused.
    """
    if _scheduler is None:
        return {"running": False, "state": "not_started", "job_count": 0}
    state = getattr(_scheduler, "state", None)
    if state == STATE_RUNNING:
        label = "running"
    elif state == STATE_PAUSED:
        label = "paused"
    elif state == STATE_STOPPED:
        label = "stopped"
    else:
        label = f"unknown:{state}"
    try:
        job_count = len(_scheduler.get_jobs())
    except Exception:
        job_count = 0
    return {
        "running": state == STATE_RUNNING,
        "state": label,
        "job_count": job_count,
    }


def _run_market_data():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.trading.market_data import collect_market_snapshots
        _run_async_with_timeout("market_data", collect_market_snapshots())
    except TimeoutError:
        logger.warning(
            "market_data job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["market_data"],
        )
    except Exception as e:
        logger.warning("market_data job failed: %s", e)


def _run_market_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    # Retry once on database-is-locked — the WAL contention window is short
    # (exchange_sync / data_collectors / uvicorn handlers), so a brief wait
    # usually clears it without waiting a full 15-min cycle.
    for attempt in range(2):
        try:
            _run_async_with_timeout("market_observer", _async_run("market_observer"))
            return
        except TimeoutError:
            logger.warning(
                "market_observer job timed out after %.0fs",
                _ASYNC_JOB_TIMEOUT_SEC["market_observer"],
            )
            return
        except Exception as e:
            if "database is locked" in str(e) and attempt == 0:
                import time as _time
                logger.warning("market_observer: database locked, retrying in 10s…")
                _time.sleep(10)
                continue
            logger.warning("market_observer job failed: %s", e)
            return


def _run_dreaming_agent():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.storage.models import SerenityResearchRun
        from aletheia.storage import repositories as repo
        from aletheia.agents.research_worker import enqueue_research_run
        run = SerenityResearchRun(trigger_type="scheduled", mode="standard", status="queued")
        repo.insert_serenity_research_run(run)
        enqueue_research_run(run.id)
    except Exception:
        # Fallback: direct agent call if new pipeline fails
        try:
            _run_async_with_timeout("dreaming_agent", _async_run("dreaming_agent"))
        except TimeoutError:
            logger.warning(
                "dreaming_agent fallback timed out after %.0fs",
                _ASYNC_JOB_TIMEOUT_SEC["dreaming_agent"],
            )
        except Exception as e:
            logger.warning("dreaming_agent job failed: %s", e)


def _run_strategy_designer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("strategy_designer", _async_run("strategy_designer"))
    except TimeoutError:
        logger.warning(
            "strategy_designer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["strategy_designer"],
        )
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
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return
    try:
        _run_async_with_timeout("paper_trader", _async_run("paper_trader"))
    except TimeoutError:
        logger.warning(
            "paper_trader job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["paper_trader"],
        )
    except Exception as e:
        logger.warning("paper_trader job failed: %s", e)


def _run_pi_trading_decision():
    """PI Agent trading decision — evaluates market state and submits proposals."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return
    try:
        from aletheia.agent_shell.commands import evaluate_trading_opportunity
        result = _run_async_with_timeout("pi_trading_decision", evaluate_trading_opportunity({}))
        if result.get("action") == "trade":
            logger.info(
                "pi_trading_decision: %s %s conviction=%s proposal=%s",
                result.get("direction"), result.get("symbol"),
                result.get("conviction"), result.get("proposal_id"),
            )
        else:
            logger.debug("pi_trading_decision: skip — %s", result.get("reasoning", "")[:80])
    except TimeoutError:
        logger.warning("pi_trading_decision job timed out after %.0fs", _ASYNC_JOB_TIMEOUT_SEC.get("pi_trading_decision", 300))
    except Exception as e:
        logger.warning("pi_trading_decision job failed: %s", e)


def _run_position_manager():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.portfolio.position_manager import evaluate_open_positions
        evaluate_open_positions()
    except Exception as e:
        logger.warning("position_manager job failed: %s", e)


def _run_position_reconcile():
    """Periodic position reconciliation: adopt externals + close orphans (demo)."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.agent_runtime.run_logger import start_run, end_run
        from aletheia.portfolio.position_reconciler import run_reconcile

        run = start_run("position_reconciler", "scheduled", cfg.agent.permission_mode)
        try:
            summary = run_reconcile()
            outputs = []
            if summary["adopted"]:
                outputs.append(f"adopted {len(summary['adopted'])} positions")
            if summary["closed_orphans"]:
                outputs.append(f"closed {len(summary['closed_orphans'])} orphans")
            if not outputs:
                outputs.append("no action needed")
            end_run(run, status="completed", outputs=outputs, errors=summary.get("errors") or None)
        except Exception as e:
            end_run(run, status="failed", errors=[str(e)])
            raise
    except Exception as e:
        logger.warning("position_reconcile job failed: %s", e)


def _run_stock_observer():
    """Run stock_observer during US market hours (gate checked here too)."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    # Redundant time gate (scheduler cron already filters, but be explicit).
    if not cfg.market_data.observed_stocks:
        return
    try:
        _run_async_with_timeout("stock_observer", _async_run("stock_observer"))
    except TimeoutError:
        logger.warning(
            "stock_observer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["stock_observer"],
        )
    except Exception as e:
        logger.warning("stock_observer job failed: %s", e)


def _run_prediction_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.market_data.observed_prediction_markets:
        return
    try:
        _run_async_with_timeout("prediction_observer", _async_run("prediction_observer"))
    except TimeoutError:
        logger.warning(
            "prediction_observer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["prediction_observer"],
        )
    except Exception as e:
        logger.warning("prediction_observer job failed: %s", e)
        return  # skip divergence scan on observer failure
    # Chain: scan for crypto/prediction divergences after observer persists fresh data
    try:
        _run_async_with_timeout("divergence_scanner", _async_run("divergence_scanner"))
    except TimeoutError:
        logger.warning(
            "divergence_scanner timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["divergence_scanner"],
        )
    except Exception as e:
        logger.warning("divergence_scanner failed: %s", e)


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
            "orderbook_snapshots": repo.prune_orderbook_snapshots(7),
            "liquidation_events": repo.prune_liquidation_events(7),
            "funding_rate_snapshots": repo.prune_funding_rate_snapshots(30),
            "open_interest_snapshots": repo.prune_open_interest_snapshots(30),
            "macro_snapshots": repo.prune_macro_snapshots(90),
            "opportunities_expired": repo.expire_stale_opportunities(r.opportunity_ttl_hours),
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


def _run_post_trade_reviewer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("post_trade_reviewer", _async_run("post_trade_reviewer"))
    except TimeoutError:
        logger.warning(
            "post_trade_reviewer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["post_trade_reviewer"],
        )
    except Exception as e:
        logger.warning("post_trade_reviewer job failed: %s", e)


def _run_risk_critic():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("risk_critic", _async_run("risk_critic"))
    except TimeoutError:
        logger.warning(
            "risk_critic job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["risk_critic"],
        )
    except Exception as e:
        logger.warning("risk_critic job failed: %s", e)


def _run_universe_refresh():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.universe.enabled:
        logger.info("universe_refresh skipped: universe.enabled=False in config")
        return
    try:
        _run_async_with_timeout("universe_refresh", _async_universe_refresh())
    except TimeoutError:
        logger.warning(
            "universe_refresh job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["universe_refresh"],
        )
    except Exception as e:
        logger.warning("universe_refresh job failed: %s", e)


def _run_backtest_orchestrator():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.strategy.orchestrator import run_all_backtests, generate_daily_report
        from aletheia.strategy.performance_ledger import (
            compute_ledger_snapshot, append_ledger_event,
        )
        from aletheia.files.jsonl import write_audit

        result = run_all_backtests(retire_killed=True)
        report = generate_daily_report(result)
        logger.info("Backtest orchestrator: %d evaluated, %d killed, %d kept",
                    result.strategies_evaluated, result.strategies_killed, result.strategies_kept)

        snapshot = compute_ledger_snapshot(period_days=7)
        append_ledger_event(snapshot)

        write_audit({
            "actor": "system", "source": "scheduler",
            "event_type": "backtest_orchestrator_complete",
            "entity_type": "strategy",
            "message": (
                f"Backtest run: {result.strategies_evaluated} evaluated, "
                f"{result.strategies_killed} killed, {result.strategies_kept} kept"
            ),
        })

        # Lifecycle automation: apply exit_policy feedback + evidence-based actions
        from aletheia.analytics.lifecycle_automation import run_lifecycle_automation
        lc_result = run_lifecycle_automation()
        logger.info("Lifecycle automation: %s", lc_result)
    except Exception as e:
        logger.warning("backtest_orchestrator job failed: %s", e)


async def _async_universe_refresh():
    from aletheia.universe.service import UniverseService
    from aletheia.files.jsonl import write_agent_run_log
    from aletheia.storage.models import _utcnow
    started = _utcnow()
    svc = UniverseService()
    try:
        snap_id = await svc.refresh()
        write_agent_run_log({
            "agent_name": "universe_refresh", "trigger_type": "scheduled",
            "started_at": started, "ended_at": _utcnow(),
            "status": "completed" if snap_id else "completed_no_snapshot",
            "outputs_created": [snap_id] if snap_id else [],
            "errors": [],
        })
    except Exception as e:
        write_agent_run_log({
            "agent_name": "universe_refresh", "trigger_type": "scheduled",
            "started_at": started, "ended_at": _utcnow(),
            "status": "failed", "outputs_created": [], "errors": [str(e)],
        })
        raise


async def _async_run(agent_name: str):
    from aletheia.agent_runtime.orchestrator import run_agent
    await run_agent(agent_name, trigger_type="scheduled")


# Phase counter for the data_collectors job (module-level, not reset between calls).
_DC_TICK: int = 0


def _run_data_collectors():
    """Collect microstructure/derivatives/macro data on a 2-min tick.

    Phase A (every tick, 2min): orderbook + funding + OI for observed symbols.
    Phase B (every 5th tick, 10min): macro (CoinGecko + yFinance).

    Single scheduled job keeps collector fan-out inside the small IO executor.
    """
    global _DC_TICK
    cfg = load_config()
    if cfg.runtime.paused:
        return
    _DC_TICK += 1
    try:
        _run_async_with_timeout("data_collectors", _async_data_collectors(cfg, _DC_TICK))
    except TimeoutError:
        logger.warning(
            "data_collectors job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["data_collectors"],
        )
    except Exception as e:
        logger.warning("data_collectors job failed: %s", e)


async def _async_data_collectors(cfg, tick: int) -> None:
    from aletheia.data.collectors.microstructure import MicrostructureCollector
    from aletheia.data.collectors.derivatives import DerivativesCollector
    from aletheia.data.collectors.macro import MacroCollector
    from aletheia.data.normalizer import compute_tradability, compute_leverage_state
    from aletheia.storage import repositories as repo

    # Observed symbols: use universe if available, fall back to configured symbols.
    symbols = _get_observed_symbols(cfg)
    if not symbols:
        logger.debug("data_collectors: no symbols configured, skipping")
        return

    # --- Phase A: microstructure + derivatives (every tick) ---
    micro = MicrostructureCollector()
    micro_result = await micro.collect(symbols)
    for snap in micro_result.get("snapshots", []):
        try:
            repo.insert_orderbook_snapshot(snap.model_dump())
        except Exception as e:
            logger.debug("orderbook persist failed: %s", e)

    deriv = DerivativesCollector()
    deriv_result = await deriv.collect(symbols)
    for snap in deriv_result.get("funding", []):
        try:
            repo.insert_funding_rate_snapshot(snap.model_dump())
        except Exception as e:
            logger.debug("funding persist failed: %s", e)
    for snap in deriv_result.get("open_interest", []):
        try:
            repo.insert_open_interest_snapshot(snap.model_dump())
        except Exception as e:
            logger.debug("OI persist failed: %s", e)
    for event in deriv_result.get("liquidations", []):
        try:
            repo.insert_liquidation_event(event.model_dump())
        except Exception as e:
            logger.debug("liquidation persist failed: %s", e)

    # --- Phase B: macro (every 5th tick = ~10 min) ---
    if tick % 5 == 1:
        macro_coll = MacroCollector()
        macro_result = await macro_coll.collect(symbols)
        snap = macro_result.get("snapshot")
        if snap:
            try:
                repo.insert_macro_snapshot(snap.model_dump())
            except Exception as e:
                logger.debug("macro persist failed: %s", e)

    # Log summary
    micro_count = len(micro_result.get("snapshots", []))
    funding_count = len(deriv_result.get("funding", []))
    logger.debug(
        "data_collectors tick=%d: orderbook=%d funding=%d",
        tick, micro_count, funding_count,
    )


def _get_observed_symbols(cfg) -> list[str]:
    """Return list of canonical symbols to collect data for."""
    # Primary: active universe members
    try:
        from aletheia.storage import repositories as repo
        members = repo.get_active_universe_members(limit=20)
        if members:
            return [m["symbol"] for m in members if m.get("symbol")]
    except Exception:
        pass

    # Fallback: configured observed symbols
    try:
        return list(cfg.market_data.observed_symbols or [])[:20]
    except Exception:
        return []
