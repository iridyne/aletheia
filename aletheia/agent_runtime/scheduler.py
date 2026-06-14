"""Scheduler for periodic agent + market-data tasks using APScheduler."""

import asyncio
import logging
from datetime import datetime
from apscheduler.executors.pool import ThreadPoolExecutor
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

    # max_workers=3: default 10 lets 13 jobs all hit `data/app.db` at the same minute
    # mark; with WAL+busy_timeout, 3 concurrent writers is the sweet spot before lock
    # contention dominates. Jobs that miss their slot use APScheduler's misfire grace.
    _scheduler = BackgroundScheduler(executors={"default": ThreadPoolExecutor(3)})

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
        misfire_grace_time=60, next_run_time=datetime.now(),
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
        _run_position_reconcile, "interval", minutes=5, id="position_reconcile",
        misfire_grace_time=60, next_run_time=datetime.now(),
    )
    _scheduler.add_job(
        _run_cleanup, "cron", hour=3, minute=17, id="cleanup",
        misfire_grace_time=600,
    )
    _scheduler.add_job(
        _run_health_check, "interval", minutes=10, id="health_check",
        misfire_grace_time=120,
    )
    # US equity observer — runs every 30 min Mon–Fri during US market hours
    # (14:30–21:00 UTC = 09:30–16:00 Eastern). The job function enforces the
    # time gate so off-hours fires are silent no-ops (stale daily data).
    _scheduler.add_job(
        _run_stock_observer, "cron",
        day_of_week="mon-fri", hour="14-20", minute="*/30",
        id="stock_observer", misfire_grace_time=300,
    )
    # Prediction market observer — runs every 30 min around the clock.
    _scheduler.add_job(
        _run_prediction_observer, "interval", minutes=30,
        id="prediction_observer", misfire_grace_time=120,
    )
    # Post-trade reviewer — reviews closed positions every 4 hours.
    _scheduler.add_job(
        _run_post_trade_reviewer, "interval", hours=4,
        id="post_trade_reviewer", misfire_grace_time=300,
    )
    # Risk critic — reviews risk decisions and writes lessons every 6 hours.
    _scheduler.add_job(
        _run_risk_critic, "interval", hours=6,
        id="risk_critic", misfire_grace_time=300,
    )
    # Universe Generator — dynamic asset pool refresh.
    _scheduler.add_job(
        _run_universe_refresh, "interval", minutes=10,
        id="universe_refresh", misfire_grace_time=120,
    )
    # 9-domain market data collectors (microstructure/derivatives/macro).
    # Single job, runs every 2 min, uses internal phase counter to control
    # per-domain frequency (orderbook+funding every 2min; macro every 10min).
    _scheduler.add_job(
        _run_data_collectors, "interval", minutes=2,
        id="data_collectors", misfire_grace_time=60,
        next_run_time=datetime.now(),
    )
    # Strategy backtest orchestrator — daily evaluation of all active strategies.
    # Runs after strategy_designer (01:00) so newly generated strategies get tested.
    _scheduler.add_job(
        _run_backtest_orchestrator, "cron", hour=4, minute=0,
        id="backtest_orchestrator", misfire_grace_time=600,
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
        from aletheia.storage.models import SerenityResearchRun
        from aletheia.storage import repositories as repo
        from aletheia.agents.research_worker import enqueue_research_run
        run = SerenityResearchRun(trigger_type="scheduled", mode="standard", status="queued")
        repo.insert_serenity_research_run(run)
        enqueue_research_run(run.id)
    except Exception:
        # Fallback: direct agent call if new pipeline fails
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
    if cfg.agent.permission_mode not in ("Paper", "Full"):
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
        asyncio.run(_async_run("stock_observer"))
    except Exception as e:
        logger.warning("stock_observer job failed: %s", e)


def _run_prediction_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.market_data.observed_prediction_markets:
        return
    try:
        asyncio.run(_async_run("prediction_observer"))
    except Exception as e:
        logger.warning("prediction_observer job failed: %s", e)


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
        asyncio.run(_async_run("post_trade_reviewer"))
    except Exception as e:
        logger.warning("post_trade_reviewer job failed: %s", e)


def _run_risk_critic():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        asyncio.run(_async_run("risk_critic"))
    except Exception as e:
        logger.warning("risk_critic job failed: %s", e)


def _run_universe_refresh():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.universe.enabled:
        return
    try:
        asyncio.run(_async_universe_refresh())
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
    except Exception as e:
        logger.warning("backtest_orchestrator job failed: %s", e)


async def _async_universe_refresh():
    from aletheia.universe.service import UniverseService
    svc = UniverseService()
    await svc.refresh()


async def _async_run(agent_name: str):
    from aletheia.agent_runtime.orchestrator import run_agent
    await run_agent(agent_name, trigger_type="scheduled")


# Phase counter for the data_collectors job (module-level, not reset between calls).
_DC_TICK: int = 0


def _run_data_collectors():
    """Collect microstructure/derivatives/macro data on a 2-min tick.

    Phase A (every tick, 2min): orderbook + funding + OI for observed symbols.
    Phase B (every 5th tick, 10min): macro (CoinGecko + yFinance).

    Single job keeps us within the max_workers=3 SQLite constraint.
    """
    global _DC_TICK
    cfg = load_config()
    if cfg.runtime.paused:
        return
    _DC_TICK += 1
    try:
        asyncio.run(_async_data_collectors(cfg, _DC_TICK))
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
