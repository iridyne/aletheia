# Source Map

## Research Questions

The source trace answers these questions:

1. What owns each background execution mechanism?
2. Where is admission decided, and what is the real concurrency bound?
3. Which state is durable, which is process-local, and which is browser-local?
4. Does cancellation stop the execution body or only change its label?
5. Does timeout reclaim real capacity, or only make the UI stop waiting?
6. What happens across application shutdown, hard kill, reload, and restart?
7. Can the operator distinguish queued, running, partial, failed, stale, and
   unavailable work?

## TickFlow: Application Ownership

### FastAPI lifespan

Primary source: `backend/app/main.py`.

- `lifespan()` constructs `DataStore`, `KlineRepository`, capabilities,
  `QuoteService`, `DepthService`, the strategy engine, and monitoring services.
- `daily_pipeline.start_scheduler(repo, capset)` creates the main
  `AsyncIOScheduler` and stores it on `app.state.scheduler`.
- `PullScheduler.start()` captures the main asyncio event loop, then
  `refresh()` materializes one task per enabled external pull configuration.
- `FinancialScheduler.start(..., auto_schedule=False)` initializes manual
  financial synchronization but does not start its weekly loop.
- matrix-cache prewarm starts a named daemon thread guarded by a process-local
  boolean and lock.
- shutdown calls APScheduler `shutdown(wait=False)`, stops pull and financial
  schedulers, and asks quote/depth services to stop.

The source does not establish a single-worker deployment invariant. If the app
is launched with multiple server workers, each process would construct its own
scheduler, pollers, locks, and in-memory job stores. Whether deployment tooling
prevents this is `Not available` from this trace.

## TickFlow: Pipeline Job Registry

Primary sources:

- `backend/app/services/pipeline_jobs.py`
- `backend/app/api/pipeline.py`
- `backend/app/jobs/daily_pipeline.py`
- `backend/app/api/kline.py`

### State model

`JobStore` uses:

- `_active_jobs`: process-memory records for `pending` and `running` work;
- `_active_id`: one process-wide active job identifier;
- a `threading.Lock` around mutations;
- one JSON file per terminal job, capped at 50 files;
- statuses `pending`, `running`, `succeeded`, and `failed`;
- fields for overall progress, stage progress, a 200-entry log, timestamps,
  result, error, and job-specific timeout.

`create()` deduplicates both `pending` and `running`, correctly closing the
window between HTTP admission and background-task start. It returns the
existing id when work is already active, and the route must not schedule again.

### Execution path

`POST /api/pipeline/run`:

1. calls `reap_stale()`;
2. creates or reuses a job;
3. creates an unretained asyncio task;
4. tries to acquire the process-wide heavy-run slot;
5. marks the job running;
6. submits the blocking pipeline to a dedicated two-thread executor;
7. temporarily pauses realtime quote acquisition;
8. records success or failure;
9. invalidates storage cache and refreshes repository cache;
10. releases the heavy-run slot in `finally`.

The minute-sync and several maintenance endpoints reuse the same `JobStore`
and heavy-run slot, making the process-wide admission domain broader than the
daily pipeline alone.

### Progress path

`daily_pipeline.run_now()` emits named stages and both overall and stage-local
percentages. Repeated chunk messages can use `skip_log=True`, replacing the
last same-stage transient entry instead of growing the log. The pipeline
finishes its progress protocol before raising `PipelineStageError` when a soft
stage failed, so the terminal status can be failed even when some data was
committed and overall progress reached 100.

### Query path

- `GET /api/pipeline/jobs/{id}` returns the full active record or terminal JSON.
- `GET /api/pipeline/jobs` merges active memory with terminal files.
- status polling invokes `reap_stale()`.
- `GET /api/data/status` combines table coverage with the next APScheduler fire
  time and derives last pipeline/instrument completion from terminal history.

## TickFlow: Pipeline Persistence and Restart

Terminal jobs are written with direct `Path.write_text()` to their final path.
There is no temporary file, fsync, or atomic replace in this path.

Active jobs are never persisted. A process restart therefore:

- forgets every pending/running job record;
- forgets `_active_id` and the heavy-run lock;
- does not convert the lost job to interrupted/stale;
- may leave no historical explanation for an incomplete write;
- cannot reconnect a browser to that pipeline job.

The `reap_stale()` docstring explicitly says reload-orphaned tasks are outside
its reach because their records disappeared.

## TickFlow: Timeout and Cancellation

### Stale reaping

`reap_stale()` compares elapsed wall time with a stored timeout, calls
`fail()`, then explicitly releases `_heavy_run_lock`.

This contradicts the later heavy-run-lock design comment, which says the lock
must remain tied to the actual execution body so a stuck executor thread blocks
new writers until process restart. Python cannot interrupt that executor
thread. Releasing the lock from the polling thread allows a new job to acquire
the slot while the old thread can still return from network I/O and write.

The comment that a later pull will overwrite the zombie's writes is not a
consistency proof. Ordering is uncontrolled, two jobs can touch different
partitions or derived datasets, and the zombie may write after the newer job.

### Manual cancellation

`POST /api/pipeline/jobs/{id}/cancel` only calls `job_store.fail(...)`.
It does not:

- cancel the asyncio task;
- signal the executor function;
- stop provider calls;
- prevent later writes;
- retain the heavy-run slot;
- prevent the executor's normal `succeed()` call from being attempted.

Because `fail()` removes the in-memory record, later `progress()` and
`succeed()` silently do nothing. The displayed lifecycle ends, but the
execution lifecycle can continue. No frontend pipeline-cancel control was
found, so this endpoint is also not part of the visible primary workflow.

## TickFlow: APScheduler

Primary source: `backend/app/jobs/daily_pipeline.py`.

The scheduler is configured for `Asia/Shanghai` and registers:

- weekday instrument synchronization;
- weekday daily pipeline;
- depth finalization;
- hourly capability reprobe;
- optional weekday AI market review.

The jobs use explicit `misfire_grace_time` and stable ids with
`replace_existing=True`. The scheduled pipeline and instruments job pass
through `_run_tracked()`, so they share manual-job history and single-flight.
The pipeline reads the live capability set from `app.state`, pauses quote
acquisition, and refreshes the in-memory repository cache in `finally`, even
when a soft stage failure follows committed writes.

No persistent APScheduler job store is configured. Schedule definitions and
last fire times are process-local. There is no explicit `coalesce` or
`max_instances` configuration in this scheduler construction; APScheduler
defaults therefore matter. Deployment-wide uniqueness is not proven.

`shutdown(wait=False)` stops accepting scheduled fires but does not prove that
already running sync work has finished before repository and process teardown.

## TickFlow: External Pull Scheduler

Primary source: `backend/app/services/ext_pull.py`.

`PullScheduler` demonstrates a useful ownership technique:

- captures the lifespan event loop;
- routes task-dictionary mutations through `call_soon_threadsafe()`;
- creates one task per enabled configuration;
- rereads configuration every cycle;
- applies changed intervals without restart;
- persists last status, message, row count, and next run in the config record;
- stops by cancelling all known tasks.

Limitations:

- every configuration loop runs immediately on enable/start;
- there is no global concurrency or bandwidth bound across configurations;
- cancellation is requested but not awaited during `stop()`;
- no durable run ledger exists, only last-run summary;
- a crash mid-pull has no interrupted terminal state;
- schedule drift is completion-time plus interval, rather than a fixed wall-clock
  cadence;
- task exceptions outside the inner fetch block could terminate a loop without
  an operator-visible scheduler state.

## TickFlow: Financial Scheduler

Primary sources:

- `backend/app/services/financial_sync.py`
- `backend/app/api/financials.py`

Despite its name and docstring, the application initializes it with
`auto_schedule=False`; current primary behavior is manual background sync.

Strengths:

- `_is_syncing` is set under a lock before `trigger()` returns, closing the
  duplicate-click race;
- one background thread performs the work;
- the status endpoint exposes `syncing` across page refresh;
- per-table `last_sync` timestamps persist in preferences and are restored;
- old installations can backfill missing last-sync values from Parquet mtime;
- capability changes can be injected after startup.

Limitations:

- there is no job id, stage record, error record, cancel signal, or run history;
- the daemon thread is not retained or joined on shutdown;
- a process restart clears `syncing` even if a prior write was interrupted;
- `_record_sync()` runs after each table function returns, even when that
  function returned zero because all provider batches failed;
- per-table fetch catches batch errors and may overwrite a complete file with a
  partial result;
- the API returns a nested `started` result under a response named `synced`,
  which blurs accepted versus completed semantics.

## TickFlow: Realtime Poller Threads

Primary sources:

- `backend/app/services/quote_service.py`
- `backend/app/services/depth_service.py`

Both services own daemon polling threads and cooperative `_running` flags.
QuoteService joins for up to ten seconds on stop; DepthService uses short sleep
slices so shutdown can be noticed promptly. These are continuous services,
not durable jobs. Their last-fetch and availability state is process-local.

Their source shows an important distinction for a future unified model:
long-lived services should expose service health and lease ownership, while
finite jobs should expose per-run lifecycle. Treating both as generic jobs
would lose useful semantics.

## TickFlow: Spawn-Isolated Backtests

Primary sources:

- `backend/app/backtest/worker.py`
- `backend/app/api/backtest.py`
- `backend/app/backtest/optimizer.py`
- `backend/app/backtest/walkforward.py`
- `frontend/src/lib/backtestTask.ts`
- `frontend/src/lib/optimizerTask.ts`
- `frontend/src/lib/walkforwardTask.ts`

### Worker isolation

`run_worker_task()` uses multiprocessing `spawn`, not fork. Each task builds a
fresh store, repository, strategy engine, and backtest service in the child.
Progress and terminal result travel through a multiprocessing queue. The child
reports peak/final RSS, serialized-result size, elapsed time, PID, exit code,
and parent RSS before/after exit.

After a result is received, the parent waits ten seconds for deterministic
worker exit, then terminates a lingering process. The outer `finally` also
terminates any still-live child. This provides a real execution boundary that
thread-only pipeline work lacks.

### Admission and task table

The streaming backtest path keeps a module-level `_running_jobs` dictionary.
One deterministic key represents equivalent inputs, so reconnects and duplicate
starts attach to the same task. Finished tasks remain for five minutes.

Backtests use a semaphore of two to protect a small-memory server. Optimizer and
walk-forward paths start daemon parent threads but do not visibly use that same
semaphore in the traced code, so the overall compute admission domain is not
unified.

### Progress and reconnect

The parent retains all progress messages in a list. Every SSE connection starts
its cursor at zero and replays the history, then emits the terminal result. The
server emits its authoritative job key as the first event. Frontend modules
store the query and job key in local storage, reconnect after page reload, and
bound transport reconnect attempts.

This is one of TickFlow's strongest reusable interaction patterns: server-owned
identity, replayable progress, detached execution, and browser recovery are
treated as one protocol.

### Cancellation

Frontend stop waits for the server-provided job key and posts cancellation. The
parent sets a threading event, mirrors it into a multiprocessing event, and the
backtest/optimizer loops check it at defined boundaries.

This is cooperative, not instant. A provider-free CPU segment that does not
check the event can delay cancellation. The parent does not hard-terminate the
worker merely because cancel was requested; hard termination is reserved for a
worker that fails to exit after returning a result or for outer cleanup.

### Restart

The task table, parent thread, job key, progress history, and result are all
process-local. A backend restart loses them. Browser local storage can reconnect
only while the same backend process retains the key. There is no durable
interrupted status or child-process adoption after parent restart.

## TickFlow: Frontend Pipeline UX

Primary sources:

- `frontend/src/pages/Data.tsx`
- `frontend/src/components/data/ActiveJobCard.tsx`
- `frontend/src/components/data/SectionTitle.tsx`
- `frontend/src/lib/api.ts`

The Data page:

- restores the server's active job id from history;
- polls active jobs every second;
- polls data coverage faster during work;
- stops polling terminal jobs;
- maps stages to affected data cards;
- shows overall progress and stage-local progress;
- renders a compact, auto-scrolling stage log;
- invalidates only relevant summaries when stages or jobs finish;
- displays persisted history and partial result summaries.

This gives users far more causal visibility than a generic spinner. The main
semantic gap is that the UI assumes a terminal JobStore state represents the
execution body, which is false for pipeline cancel and stale reap.

## Aletheia: Runtime Ownership

Primary source: `aletheia/server/app.py`.

`runtime_process_context()` derives runtime id, mode, state, and port. Only a
runtime with mode `current` starts background services and owns the gateway PID
file. Shadow runtime instances therefore serve without duplicating the live
scheduler and workers.

The lifespan sequence:

- verifies database migrations before any writer starts;
- records the boot commit and runtime session;
- reconciles stale durable Agent runs;
- starts the bounded manual Agent worker;
- initializes the scheduler;
- starts named post-bind gate/sync tasks;
- starts optional operator and messaging services;
- on shutdown stops producers before consumers;
- cancels and awaits owned asyncio tasks through a helper;
- stops independent recovery loops;
- closes inference resources;
- records clean runtime-session termination.

This creates a stronger distinction between desired runtime, current owner,
shadow process, and clean versus unclean process lifetime.

## Aletheia: APScheduler Lanes

Primary source: `aletheia/agent_runtime/scheduler.py`.

A `BackgroundScheduler` owns several small executor lanes:

- `io`: one worker for scheduled SQLite-heavy infrastructure work;
- `fast_io`: one worker for latency-sensitive probes/feeds;
- `observer`: one worker for catch-up observation;
- `agents`: one worker for LLM-backed work;
- `default`: two workers for selected general work.

Global job defaults are `coalesce=True` and `max_instances=1`. Each job has a
stable id, explicit executor, misfire grace, and interval/cron/date trigger.
Cron timezone is specified where wall-clock meaning matters. Selected jobs use
delayed first-run times to avoid cold-start contention.

The scheduler itself is process-local, but several daily jobs implement
database-backed catch-up keys. Cleanup, learning measurement, and research
recovery do not assume APScheduler remembers downtime.

## Aletheia: Guard and Timeout Workers

Every scheduled job is wrapped by `_run_job_guarded()`:

- rolls back any stale per-thread transaction before the job;
- runs the body outside the cleanup lock;
- records selected learning-job lifecycle;
- records consecutive failure state;
- rolls back leaked transactions and closes thread-local SQLite connections
  after the body.

External I/O and LLM work has explicit hard budgets. Async work is submitted to
a process-owned event-loop thread with bounded admission. News analysis has a
separate loop so a blocking provider cannot starve unrelated async work.
Synchronous time-boxed work uses one fixed daemon worker with a bounded queue.

The synchronous design states the unavoidable truth: a running Python call
cannot be interrupted. On timeout its one lane remains occupied and new work
stays bounded. It does not manufacture capacity by releasing an ownership lock.

Metrics expose submissions, completions, timeouts, cancellations, rejections,
worker liveness, pending count, capacity, and sync backlog.

## Aletheia: Pause and Failure State

`runtime.paused` is persisted in configuration. The scheduler can:

- blanket-pause all jobs; or
- layer-pause non-essential jobs while data collection, reconciliation,
  connectivity, settlement, and audit repair continue.

`get_pause_state()` reports stopped, blanket-paused, layered-paused, or running,
including job ids. `status_snapshot()` reports actual scheduler state and job
count rather than equating enabled configuration with a running object.

Repeated job failures increment a per-process counter. At three consecutive
failures, the scheduler emits a deduplicated infrastructure event and error
record visible in the dashboard status surface. The counter itself resets on
process restart.

## Aletheia: Durable Agent Runs and Manual Queue

Primary sources:

- `aletheia/agent_runtime/job_queue.py`
- `aletheia/agent_runtime/run_logger.py`
- `aletheia/server/routes/data.py`

The manual Agent manager is intentionally serial and capacity-bounded. It:

- persists a `queued` AgentRun before in-memory admission;
- rejects queue overflow with a durable terminal status;
- deduplicates per agent or caller-supplied idempotency key;
- reports queue depth, active run id, counts, and average wait;
- runs a single daemon worker;
- rolls back and closes SQLite state after every job;
- cancels queued work durably during shutdown;
- retains the worker reference when it fails to join within the grace period,
  preventing accidental creation of a second worker.

AgentRun lifecycle lives in SQLite with a JSONL mirror, including queued,
running, terminal status, outputs, errors, tools, provider, model, and cost
summary. The API exposes runs, steps, and tool calls.

At gateway startup, all leftover queued/running Agent runs are atomically marked
`stale` with an audit entry. A periodic health job also closes old running rows.
This does not resume work, but it prevents the UI from claiming a dead process
still owns it.

## Aletheia: Special Durable Research Runs

Serenity research has its own persisted run state, artifacts, retry, cancel,
and startup recovery semantics. It shows that some long work already needs a
richer domain record than the generic AgentRun. The existence of multiple run
models is appropriate when outputs and recovery differ, but their common
lifecycle vocabulary should remain aligned.

## Evidence Not Available

- TickFlow deployment worker count and whether an external singleton lock is
  enforced: `Not available`.
- TickFlow production shutdown behavior with active pipeline/backtest work:
  `Not available`.
- Measured cancellation latency at every backtest loop boundary: `Not available`.
- Whether a TickFlow zombie pipeline has caused a real overlapping write:
  `Not available`; the possibility follows from source control flow.
- Aletheia production scheduler queue wait distribution and timeout frequency:
  `Not available`.
- Whether every Aletheia scheduled job creates a durable run record: no; only
  specific Agent/learning/domain flows do so in the traced source.
- Exact behavior after OS hard-kill of a spawned TickFlow child on every
  platform: `Not available`.
