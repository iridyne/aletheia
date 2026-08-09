---
type: component
title: Scheduler
description: Cron-like job scheduler with process isolation and data freshness monitoring.
tags: [scheduler, jobs, cron, monitoring]
---

# Scheduler

The **Scheduler** (`aletheia/runtime/scheduler.py`) manages all periodic background jobs in Aletheia. It uses a cron-like syntax with process isolation for data collection jobs, and includes a health monitoring system that can trigger gateway restarts when data freshness degrades.

## Architecture

```mermaid
flowchart TD
    A[Pi Daemon] --> B[Scheduler]
    B --> C[Job Registry]
    C --> D1[collect_market_data\n(Cron: 0 */2 * * *)]
    C --> D2[refresh_universe\n(Cron: */5 * * * *)]
    C --> D3[check_learning_verdicts\n(Cron: 0 * * * *)]
    C --> D4[knowledge_maintenance\n(Cron: 0 0 * * 0)]
    C --> D5[portfolio_rebalancing\n(Cron: 0 0 * * 1)]
    C --> D6[data_backup\n(Cron: 0 2 * * *)]
    C --> D7[collector_health_check\n(Cron: */30 * * * *)]
    C --> D8[regime_refresh\n(Cron: */15 * * * *)]
    D1 --> E[Collector Runner\n(Subprocess)]
    D7 --> F[Health Monitor]
    F -->|stale| G[Restart Gateway]
    F -->|ok| H[Resume Jobs]
```

## Jobs

| Job | Cron | Description | Source |
|---|---|---|---|
| `collect_market_data` | `0 */2 * * *` | Spawn collector subprocess | `runtime/scheduler.py` |
| `refresh_universe` | `*/5 * * * *` | Rebuild opportunity set | `universe/refresh.py` |
| `check_learning_verdicts` | `0 * * * *` | Check AT-01 verdicts | `learning/verdicts.py` |
| `knowledge_maintenance` | `0 0 * * 0` | Weekly KB pruning | `knowledge/maintenance.py` |
| `portfolio_rebalancing` | `0 0 * * 1` | Weekly rebalance | `portfolio/rebalancer.py` |
| `data_backup` | `0 2 * * *` | Daily backup | `data/backup.py` |
| `collector_health_check` | `*/30 * * * *` | Check collector health | `data/collector_health.py` |
| `regime_refresh` | `*/15 * * * *` | Refresh regime state | `regime/detector.py` |

## Job Configuration

Each job is configured with:

| Property | Description |
|---|---|
| `name` | Job identifier |
| `cron` | Cron expression |
| `func` | Callable to execute |
| `timeout` | Max execution time (seconds) |
| `retries` | Max retry attempts |
| `isolation` | Whether to run in subprocess |
| `critical` | Whether failure triggers restart |

## Data Collection Job

**Source:** `runtime/scheduler.py::job_collect_market_data`

The `collect_market_data` job:
1. Spawns `data/collector_runner.py` as a **subprocess**
2. Waits for completion (with timeout)
3. Checks health status from `data/collector_health.py`
4. If health is critical (>50% collectors stale), triggers gateway restart
5. Records job metrics (duration, success/failure, data points collected)

## Health Monitoring

**Source:** `data/collector_health.py`

The scheduler runs `collector_health_check` every 30 minutes:

```python
def check_all_health():
    """Check health of all data collectors."""
    health_report = {}
    for collector in ALL_COLLECTORS:
        health = collector.get_health()
        health_report[collector.name] = health
        
        if health.is_stale():
            if health.stale_duration > CRISIS_THRESHOLD:
                # Trigger gateway restart
                trigger_restart(f"Collector {collector.name} stale")
    
    return health_report
```

### Crisis Thresholds

| Metric | Threshold | Action |
|---|---|---|
| Collector freshness | > 300s | Mark staleness warning |
| Collectors stale | > 50% | Trigger gateway restart |
| Collector failures | > 3 in 10 min | Apply venue cooldown |
| API errors | > 10 in 10 min | Apply venue cooldown |

## Job Execution Model

**Source:** `runtime/job_executor.py`

Jobs execute with:
1. **Timeout enforcement** — Hard timeout kills the job
2. **Retry logic** — Exponential backoff (1s, 2s, 4s)
3. **Failure logging** — All failures logged to `job_executions` table
4. **Alert on critical failure** — System Operator notified

```python
async def execute_job(job_config):
    """Execute a scheduled job with all safety measures."""
    try:
        result = await asyncio.wait_for(
            job_func(), 
            timeout=job_config.timeout
        )
        log_job_success(job_config.name, result)
        return result
    except asyncio.TimeoutError:
        log_job_timeout(job_config.name)
        if job_config.critical:
            trigger_restart(f"Job {job_config.name} timed out")
    except Exception as e:
        log_job_failure(job_config.name, e)
        if job_config.retries > 0:
            return await execute_job_with_retry(job_config)
```

## Scheduler Persistence

**Source:** `runtime/scheduler_state.py`

Job execution state is persisted:

| Field | Description |
|---|---|
| `last_run` | Last execution timestamp |
| `last_success` | Last successful execution |
| `last_failure` | Last failure timestamp + error |
| `avg_duration` | Average execution duration |
| `success_rate` | Success rate over last 100 runs |

## Manual Job Triggering

**Source:** `runtime/scheduler.py::trigger_job`

Jobs can be manually triggered:

```python
from aletheia.runtime.scheduler import trigger_job

# Trigger market data collection
await trigger_job("collect_market_data")

# Trigger universe refresh with custom params
await trigger_job("refresh_universe", force=True)
```

## Key Symbols

| Symbol | Location | Description |
|---|---|---|
| `Scheduler` | `runtime/scheduler.py` | Main scheduler class |
| `trigger_job` | `runtime/scheduler.py` | Manual job trigger |
| `execute_job` | `runtime/job_executor.py` | Job execution with safety |
| `JobConfig` | `runtime/scheduler.py` | Job configuration |
| `ALL_JOBS` | `runtime/scheduler.py` | Registered jobs list |
| `check_all_health` | `data/collector_health.py` | Health check |
| `trigger_restart` | `runtime/scheduler.py` | Gateway restart trigger |
| `log_job_success` | `runtime/job_executor.py` | Success logging |

## Test Coverage

- `tests/test_scheduler.py` — Tests for job scheduling, execution
- `tests/test_job_executor.py` — Tests for timeout, retry logic
- `tests/test_collector_health.py` — Tests for health monitoring
- `tests/test_scheduler_state.py` — Tests for persistence

## Relationship to Other Components

- [Runtime Engine](engine.md) — Runtime integration
- [Data Collector Subprocess](../architecture/data-collector-subprocess.md) — Collection job
- [Position Manager](../components/position-manager.md) — Rebalancing job
- [Learning System](../components/learning-system.md) — Verdict checking job
- [Knowledge Base](../components/knowledge-base.md) — Maintenance job
- [Serenity Lens](../components/serenity-lens.md) — Regime refresh job
