---
type: service
title: APScheduler Job Registry
description: 30+ scheduled jobs across 5 executor pools with health checks and pause/resume.
tags: [scheduler, runtime]
---

# APScheduler Job Registry

The scheduler is the **central orchestration engine** for all automated work in Aletheia. It is initialized in `aletheia/agent_runtime/scheduler.py` and uses APScheduler with 5 executor pools.

## Executors

| Pool | Type | Purpose |
|---|---|---|
| `default` | ThreadPoolExecutor (10 threads) | General async jobs |
| `blocking` | ThreadPoolExecutor (4 threads) | Blocking I/O jobs |
| `process` | ProcessPoolExecutor (2 workers) | CPU-bound jobs |
| `llm` | ThreadPoolExecutor (4 threads) | LLM inference jobs (rate-limited via broker) |
| `collectors` | ThreadPoolExecutor (6 threads) | Data collection |

## Job Registry (30+ jobs)

All jobs are registered in `aletheia/agent_runtime/scheduler.py::init_scheduler`. Each job has a unique ID, a trigger (cron/interval), an executor, a max_jitter, and a max_running_time.

| Job ID | Trigger | Executor | Function | Purpose |
|---|---|---|---|---|
| `universe_refresh` | every 5m | default | `universe.service.refresh_universe` | Scan markets, emit signals |
| `collect_market_data` | every 30s | collectors | `data.collector_runner.run_collectors` | Run all 9 collectors |
| `news_collection` | every 10m | default | `data.news_collector.collect_news` | RSS news feed ingestion |
| `news_analysis` | every 15m | llm | `data.news_analyzer.analyze_news` | LLM sentiment + entity extraction |
| `paper_trader` | every 10m | default | `agents.paper_trader.run` | Rank candidates, emit signals |
| `paper_trader_ranker` | every 15m | default | `agents.paper_trader_ranker.run` | Deterministic candidate ranking |
| `market_observer` | cron (6x daily) | llm | `agents.market_observer.run` | Per-symbol LLM market analysis |
| `pi_operator` | every 4h | llm | `agent_runtime.pi_daemon.run_operator_cycle` | System Operator assessment |
| `dreaming_agent` | cron (daily) | default | `agents.dreaming_agent.run` | Daily consolidation + review |
| `equity_observer` | cron (daily) | llm | `agents.equity_observer.run` | US equity observation |
| `prediction_observer` | cron (hourly) | llm | `agents.prediction_observer.run` | Polymarket observation |
| `divergence_scanner` | cron (daily) | llm | `agents.divergence_scanner.run` | Price divergence detection |
| `post_trade_reviewer` | cron (daily) | default | `agents.post_trade_reviewer.run` | Review closed positions |
| `risk_critic` | cron (daily) | default | `agents.risk_critic.run` | Review risk decisions |
| `lesson_reviewer` | cron (daily) | default | `agents.lesson_reviewer.run` | Promote lessons to KB |
| `research_worker` | cron (weekly) | llm | `agents.research_worker.run` | Serenity research pipeline |
| `insight_synthesizer` | cron (daily) | llm | `agents.insight_synthesizer.run` | Daily insight synthesis |
| `portfolio_management` | every 1m | default | `portfolio.position_manager.manage` | Autonomous position closes |
| `pnl_calculation` | every 5m | default | `portfolio.pnl.calculate` | PnL calculation |
| `reconciliation` | every 30m | default | `execution.reconciliation.run` | Exchange order reconciliation |
| `stale_order_cleanup` | every 10m | default | `execution.stale_order_cleanup.run` | Clean stale orders |
| `kill_switch_monitor` | every 1m | default | `risk.kill_switch.monitor` | Monitor kill switch state |
| `regime_detection` | every 30m | default | `regime.detector.detect` | Market regime detection |
| `regime_collect` | every 15m | default | `regime.collector.collect` | Regime input collection |
| `economics_tracing` | every 5m | default | `economics.tracing.flush` | Flush LLM cost tracing |
| `maintenance` | cron (daily) | default | `maintenance.database_capacity.check` | Database capacity check |
| `knowledge_maintenance` | cron (weekly) | default | `knowledge.maintenance.run` | Knowledge base maintenance |
| `learning_experiments` | cron (daily) | default | `learning.experiments.evaluate` | AT-01 evaluation |
| `audit_hashchain` | cron (daily) | default | `files.jsonl.verify_audit_chain` | Audit chain verification |
| `health_check` | every 1m | default | `agent_runtime.health.check` | System health monitoring |
| `doctor_probes` | cron (daily) | default | `diagnostics.doctor.run` | Run doctor probes |
| `runtime_continuity` | every 5m | default | `runtime_continuity.service.poll` | Runtime continuity state poll |
| `tunnel_check` | every 5m | default | `gateway.tunnel.check` | Cloudflare tunnel health |

## Job Lifecycle

1. **Registration:** Jobs are added to the APScheduler during `init_scheduler()` with `replace_existing=True` so restarts don't create duplicates.
2. **Execution:** Each job runs in its assigned executor pool. Long-running jobs get a `max_running_time` (default 300s); the scheduler kills stuck jobs.
3. **Health monitoring:** `agent_runtime/health.py` checks if expected jobs are alive and if the scheduler itself is responsive. If the scheduler stalls, it triggers a gateway restart.
4. **Pause/Resume:** The gateway supports `aletheia gateway pause` and `resume` which pauses/resumes the scheduler and relevant jobs.

## Pause/Resume Semantics

When paused:
- The scheduler stops scheduling new jobs
- Already-running jobs are allowed to complete
- Data collectors continue (marked as critical)
- Paper trading continues for existing positions (risk management only)

When resumed:
- The scheduler resumes scheduling
- All paused jobs begin running again
- A catch-up mechanism prevents thundering herd on resume

## Error Handling

- **Job failures** are logged to `aletheia/infra/errors.py` (error store) and the error summary
- **Repeated failures** (3+ consecutive) trigger a health alert
- **Critical job failures** (scheduler, health_check) trigger a gateway restart
<!-- openwiki: broken internal link [services/model-provider.md] file "services/model-provider.md" does not exist. Fix the href or restore the target, then delete this comment. -->
- **LLM job failures** trigger provider failover (see [Model Provider](services/model-provider.md))

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `init_scheduler` | `agent_runtime/scheduler.py` | Initializes APScheduler with 5 executor pools and registers all 30+ jobs |
| `JOB_DEFINITIONS` | `agent_runtime/scheduler.py` | Registry of all job definitions (ID, function, trigger, executor) |
| `pause_scheduler` | `agent_runtime/scheduler.py` | Pauses all schedulable jobs |
| `resume_scheduler` | `agent_runtime/scheduler.py` | Resumes all paused jobs |
| `health_check` | `agent_runtime/health.py` | Periodic health monitor job |
| `MAX_RUNNING_TIME` | `agent_runtime/scheduler.py` | Per-job timeout configuration |
