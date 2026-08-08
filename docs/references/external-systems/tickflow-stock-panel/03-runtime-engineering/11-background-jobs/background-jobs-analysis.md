# Background Jobs Analysis

## Executive Finding

TickFlow is not powered by one background-job framework. It is a collection of
well-tuned local mechanisms, each chosen for a particular workload:

- APScheduler for wall-clock work;
- a JSON-backed JobStore for visible data pipelines;
- event-loop tasks for configurable external pulls;
- daemon threads for pollers and financial synchronization;
- dedicated executors for blocking pipelines;
- spawn-isolated child processes for memory-heavy backtests;
- SSE plus process-memory replay for interactive long jobs.

This variety produces some excellent local practices, especially the Data page
progress model and backtest worker isolation. It also produces the central
architectural weakness: task identity, cancellation, timeout, persistence, and
restart mean different things in each subsystem. The UI can look more coherent
than the underlying ownership model actually is.

## A Useful Four-Layer Model

Background work should be analyzed as four separate layers:

1. **Intent**: a request or schedule says work should happen.
2. **Admission**: the system grants capacity and establishes ownership.
3. **Execution**: a thread, process, coroutine, or external worker performs it.
4. **Observation**: durable or transient state explains what happened.

TickFlow's pipeline problem occurs because observation can be terminated while
execution continues. Its backtest path is stronger because a child process is
an explicit execution object that the parent can terminate and join.

## Workload Taxonomy

| Workload | TickFlow mechanism | Lifetime | Durable run state | Real cancel boundary |
|---|---|---:|---:|---:|
| Daily data pipeline | asyncio task + thread executor | Finite | Terminal JSON only | No |
| Scheduled data pipeline | APScheduler + synchronous wrapper | Finite | Terminal JSON only | No |
| Minute/repair jobs | asyncio task + shared JobStore | Finite | Terminal JSON only | No |
| External pull | asyncio loop per config | Continuous | Last summary only | Task cancel |
| Financial sync | daemon thread | Finite | Last-sync timestamps | No |
| Quote/depth acquisition | daemon thread | Continuous | Preference/last state | Cooperative stop |
| Backtest | daemon parent thread + spawned child | Finite | Process memory for 5 min | Cooperative event + hard process cleanup |
| Optimizer/walk-forward | same spawned worker model | Finite | Process memory for 5 min | Cooperative event + hard process cleanup |
| AI review schedule | APScheduler coroutine | Finite | Saved report on success | Task/framework dependent |

The table explains why a single “background task” abstraction would be too
coarse. The reusable part is a shared lifecycle contract, not necessarily a
single executor implementation.

## Admission: Where TickFlow Is Strong

### Pending counts as active

The Pipeline JobStore explicitly deduplicates both `pending` and `running`.
This is a small but important correctness detail. Admission begins before the
execution body starts, so a second click during scheduling must attach to the
first intent.

### Resource-specific isolation

Blocking data pipelines use a dedicated thread pool rather than FastAPI's
default pool. Backtests use spawned child processes. The backtest path limits
concurrent server work with a semaphore. These choices prevent one workload
class from trivially starving request handling.

### Deterministic job keys

Interactive backtests derive job identity from the complete normalized input
signature. Equivalent reconnects attach to the same live task. The server then
returns the canonical key to the client, avoiding duplicate client/server key
logic for cancel.

### Configuration mutation on the owner loop

`PullScheduler` funnels `_tasks` mutations through the captured event loop.
This is a good answer to FastAPI sync routes that may run in worker threads.

## Admission: Where TickFlow Is Weak

### One global pipeline slot hides workload semantics

Daily pipeline, minute sync, repairs, and other data jobs share one `_active_id`
and one heavy slot. That is conservative and appropriate for overlapping
Parquet writes, but the job record does not identify the resource set or reason
for conflict. A user receives “already running” without a typed admission
explanation beyond the reused job id.

### Process-local admission is not deployment admission

Every mutex, semaphore, registry, and scheduler is process-local. Without a
proven single current process, multiple app workers can each admit one pipeline
or two backtests.

### Backtest bounds are incomplete across modes

The traced strategy backtest uses `_backtest_semaphore = 2`. Optimizer and
walk-forward start separate daemon threads and spawned children without visibly
sharing that semaphore. Memory-heavy task types can therefore bypass a bound
that appears global in the UI.

### Pull loops have no aggregate cap

Each external configuration gets an independent immediate-run loop. Enabling
many configurations can create a startup burst of HTTP calls and writes.

## Execution Ownership

### Pipelines: task handle discarded

`asyncio.create_task(task())` is not assigned to a registry. The JobStore owns
metadata, not the actual `Task` or executor future. Consequently it cannot await,
cancel, or prove completion of the execution object.

### Backtests: explicit child handle

The parent retains a `multiprocessing.Process`, event queue, and cancel event.
It observes liveness, joins the child, and terminates it in cleanup. This is the
most complete execution ownership in TickFlow.

### Pollers: cooperative service ownership

Quote/depth services retain their thread and running flag. Shutdown at least has
an object to signal and join. External pull tasks are retained in a dictionary,
but shutdown cancels without awaiting their completion.

## Progress Is a Domain Contract

TickFlow's pipeline progress works because it is not just a percentage. It has:

- overall progress;
- named stage;
- stage-local progress;
- bounded timestamped log;
- transient-message compaction;
- result summary;
- explicit skipped stages;
- terminal error;
- history that survives normal restart after completion.

The Data page maps stages to physical data cards, so progress explains what
part of the product is changing. This is more useful than exposing thread-pool
metrics alone.

The limitation is that progress writes disappear when JobStore removes a
cancelled/reaped record. Execution may continue without an observable owner.

## Partial Completion

The daily pipeline has improved semantics: it may continue through selected
soft failures to preserve useful work, then raises a typed error so the job ends
failed. This is a good distinction between execution strategy and terminal
truth.

However, the terminal error does not retain the partial `result` because
`fail()` accepts only a string. The `run_now()` result with `stage_errors` is
constructed but then discarded by the exception path. Operators can inspect
logs, but machine-readable committed stages and coverage are weaker in failed
runs than in succeeded runs.

Financial synchronization is less honest. A table can be stamped as synced
after a zero-row return caused by complete provider-batch failure. Its UI only
has `syncing` and last-sync timestamps, so it cannot distinguish partial,
failed, empty, or successfully unchanged.

## Cancellation Semantics

The word “cancel” should be reserved for a request plus an acknowledged
execution response.

### Pipeline cancel is status cancellation

It ends the JobStore row, not the thread. This should be called “detach/mark
failed” if kept, because no stop token reaches the workload.

### Backtest cancel is cooperative execution cancellation

The client obtains the server key, the parent sets a cancellation event, the
event crosses the process boundary, and compute loops check it. This is a real
protocol, although not an immediate kill.

### Shutdown cancel is uneven

- pull tasks: cancel requested, not awaited;
- APScheduler: shutdown without waiting;
- financial thread: no retained thread/join;
- quote service: retained thread with bounded join;
- backtest child: explicit termination in parent cleanup, but parent daemon
  thread itself is not registered with application lifespan.

## Timeout Semantics

A timeout can mean three different things:

1. observer stopped waiting;
2. cooperative cancellation was requested;
3. execution was forcibly terminated.

TickFlow pipeline `reap_stale()` implements only the first, while naming it
“forced cancel.” It then releases capacity as if the third occurred. That is
the most consequential flaw in this domain.

Backtest hard termination is safer because it applies to a real process handle,
after a bounded join. The pipeline cannot offer equivalent force while using an
uninterruptible thread.

## Restart Recovery

### Terminal history recovery

Pipeline terminal JSON and financial last-sync preferences survive restart.
This supports history and user orientation.

### Active ownership recovery

No TickFlow generic mechanism marks previously active jobs interrupted. The
active pipeline table, backtest table, progress replay, pull task ownership,
and financial `syncing` flag reset with the process.

This creates two problems:

- the UI loses the causal record of why data may be incomplete;
- new admission cannot reason about a child or external effect left behind by
  an abnormal parent exit.

Spawned child behavior after abrupt parent death is platform-dependent and was
not exercised, so it remains `Not available`.

## Scheduling Semantics

TickFlow makes several good scheduling choices:

- explicit Shanghai timezone for A-share wall-clock jobs;
- stable ids and replace-in-place schedule updates;
- differentiated misfire grace;
- capability reprobe so subscription changes do not require restart;
- live capability lookup in the daily pipeline;
- initial configuration ordering before pull scheduler refresh;
- typed async registration for the scheduled review instead of a lambda that
  would return an un-awaited coroutine.

Important gaps:

- the scheduler store is memory-only;
- missed downtime windows are not repaired by durable period keys;
- multi-process uniqueness is not established;
- per-job `max_instances` and global coalescing policy are not made explicit;
- shutdown does not wait for active jobs;
- “next run” is exposed for only selected jobs.

## Operator Experience

TickFlow's Data page is exemplary in its visual decomposition:

- users see the current stage rather than an indeterminate overlay;
- card-level animation links work to affected datasets;
- compact logs explain batch movement;
- schedule and last/next run appear near data coverage;
- active jobs are recovered from the server after page navigation;
- history remains clickable.

Backtest pages add server-identity, reconnect, replay, and cancel-race handling.
These interactions are worth borrowing even if Aletheia keeps its own backend
runtime model.

The UX should not be copied without strengthening the backend truth. A polished
progress card magnifies lifecycle inaccuracies when “failed/cancelled” does not
mean execution stopped.

## Design Contradictions

### Heavy-run comment versus reaper code

The source contains both of these intended claims:

- a stuck execution must keep the slot so new writes fail closed;
- a stale observer should release the slot so work can “self-heal.”

Both cannot be true for an uninterruptible thread. The implementation chooses
availability and overlap risk; the explanatory comment claims safety.

### Weekly scheduler versus actual initialization

`FinancialScheduler` describes weekly metrics synchronization, but the app
starts it with automatic scheduling disabled. The accurate product behavior is
manual-only initialization unless another untraced caller enables it.

### Completed progress versus failed terminal state

The daily pipeline intentionally emits `done/100` before raising a partial
failure. This can be valid if UI separates “work traversal complete” from
“outcome succeeded.” The current progress visualization makes that distinction
only at the terminal status, so it should be documented explicitly.

## Overall Assessment

TickFlow is strongest where job behavior is closest to a user workflow:
pipeline stage UX and interactive backtests. It is weaker at process-wide
runtime truth: active persistence, real cancellation, consistent timeout,
deployment uniqueness, and unified shutdown.

The best borrowing strategy is therefore selective:

- borrow its progress/event vocabulary and server-owned reconnect protocol;
- borrow child-process isolation for memory-heavy analytical work;
- borrow schedule visibility and live configuration refresh;
- do not borrow the JobStore as a durable execution authority;
- do not treat a metadata timeout as resource reclamation;
- do not create another independent scheduler without explicit runtime owner.
