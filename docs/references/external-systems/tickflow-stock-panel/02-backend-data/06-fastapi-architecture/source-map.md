# Domain 6 Source Map

## TickFlow Composition Sources

| Boundary | Current source | Role |
|---|---|---|
| application root | `backend/app/main.py` | constructs shared objects, starts services, registers middleware/routes, mounts static assets, and shuts services down |
| durable analytical facade | `backend/app/tickflow/repository.py` | owns DataStore-facing reads, hot caches, warm-up, refresh callbacks, and data-generation access |
| scheduled pipeline | `backend/app/jobs/daily_pipeline.py` | starts APScheduler, pauses quotes during refresh, updates capabilities, and reaches application state from jobs |
| route modules | `backend/app/api/*.py` | read services and repository from `request.app.state`; some construct request-local service/engine instances |
| quote lifecycle | `backend/app/services/quote_service.py` | boots, receives repository/application-state setters, pauses around competing writes, and stops |
| auxiliary services | `backend/app/services/depth_service.py`, `ext_pull.py`, `financial_sync.py`, `wecom_bot_service.py` | independently started optional or feature-specific workers/schedulers |
| data readiness | `backend/app/api/data.py` | reports dataset status and `indicators_ready` separately from the public health route |
| basic liveness | `backend/app/api/routes.py` | returns process/application version and current provider mode |
| desktop bootstrap | `backend/app/desktop.py` | polls `/health` as the backend-started signal |

## TickFlow Object and Work Flow

```text
FastAPI lifespan
  -> DataStore
  -> KlineRepository
       -> background enriched-cache warm-up
       -> completion callback updates app.state.indicators_ready
  -> capability detection
  -> Quote / Depth / Strategy / Monitor services
  -> APScheduler + extension/financial schedulers
  -> app.state service registry

HTTP route
  -> request.app.state.repo/service/capabilities
  -> sometimes construct ScreenerService or BacktestEngine
  -> operation

scheduled daily pipeline
  -> read live app.state capabilities
  -> pause QuoteService
  -> perform data pipeline
  -> refresh repository cache in finally
```

## TickFlow Startup Ordering That Carries Meaning

1. DataStore and KlineRepository are created before routes can use them.
2. Managed backtest generation is fixed before concurrent backtest workers can
   race to create it.
3. Enriched indicators warm in the background, while a separate readiness fact
   begins as false.
4. Strategy monitor is created before QuoteService receives application state.
5. Built-in extension presets are awaited before the pull scheduler refreshes,
   so a fresh deployment does not miss newly created schedules.
6. Screener loaders and the strategy engine are wired into the monitor engine
   before persisted rules are installed.

## Static TickFlow Inventory

- `backend/app/main.py`: 391 lines;
- 23 explicit router registrations;
- 172 `request.app.state` occurrences across 21 API route files;
- zero FastAPI `Depends(...)` occurrences under `backend/app`;
- 16 `threading.Thread` occurrences across nine backend files;
- seven `asyncio.create_task` occurrences across four backend files;
- the composition root itself contains nine broad `except Exception` clauses
  and eight warning logs.

Counts are source-pattern inventories, not semantic defect counts.

## Aletheia Composition Sources

| Boundary | Current source | Role |
|---|---|---|
| gateway application root | `aletheia/server/app.py` | runtime identity, migration verification, service/gate startup, middleware/routes, static UI, task ownership, and shutdown |
| health and readiness | `aletheia/server/routes/health.py` | separates cheap liveness, static-UI readiness, and deeper business health |
| scheduler and job manager | `aletheia/agent_runtime/scheduler.py`, `job_queue.py` | module-owned runtime services initialized and stopped by lifespan |
| startup safety gates | `aletheia/agent_runtime/startup_gate.py`, `startup_behavior_gate.py`, `connectivity.py` | pause work, persist blockers, and own recheck/recovery loops |
| runtime sessions | `aletheia/agent_runtime/runtime_session.py` | records process lifecycle and clean-versus-crash termination |
| database authority | `aletheia/storage/migrations.py`, database access modules | verifies schema before writers; routes generally obtain module-level database/config accessors |
| route modules | `aletheia/server/routes/*.py` | frequently call `load_config()`, `get_db()`, and service-manager factories directly |

## Static Aletheia Inventory

- `aletheia/server/app.py`: 909 lines;
- 24 explicit router registrations;
- one `request.app.state` occurrence across route modules;
- zero FastAPI `Depends(...)` occurrences under `aletheia/server`;
- no `threading.Thread` construction under `aletheia/server`;
- nine `asyncio.create_task` occurrences across three server files;
- six lifespan-created tasks in the application root and seven explicit
  `_cancel_background_task(...)` calls;
- routes frequently obtain configuration, database connections, and managers
  through imported module-level accessors rather than a typed request context.

## Evidence Classification

The inventories prove the visible coupling and lifecycle mechanisms in current
source. They do not measure production latency, task leakage, scheduler overlap,
or actual restart behavior. Comments about intended safety are treated as design
evidence only where the corresponding control flow is also visible.
