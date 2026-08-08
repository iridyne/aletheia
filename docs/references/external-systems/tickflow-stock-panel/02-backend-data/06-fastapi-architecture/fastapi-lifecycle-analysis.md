# TickFlow FastAPI Composition and Lifecycle Analysis

## Scope

The central question is not whether TickFlow uses FastAPI correctly in the
abstract. It is how shared analytical state becomes available to routes and
jobs, what “ready” means during warm-up, and whether every started activity has
a clear failure and shutdown policy.

## Composition Root

`backend/app/main.py` is a concrete, imperative composition root. It creates one
DataStore and one KlineRepository, attaches them to `app.state`, then constructs
and connects quote, depth, strategy, screener, monitor, pull, financial, and bot
services. The order is readable because construction and wiring appear together.

This has an important virtue: there is a visible answer to “which repository is
this process using?” HTTP routes, background services, and most scheduled work
share the same repository object and its hot caches. Capability detection is
also attached as live process state rather than copied into every route.

The same mechanism becomes an untyped service locator. Twenty-one route files
reach into `request.app.state`, and a field rename or missing initialization is
not checked by Python's type system or FastAPI. Routes know the application
container's layout, not merely the service contract they need.

There is no compensating FastAPI dependency layer. This is not intrinsically
wrong, but it means request dependency declaration, typed overrides in tests,
and consistent per-request resource cleanup are not benefits the application is
currently taking from the framework.

## Route, Service, and Repository Boundaries

The route layer commonly selects an object from application state and calls it,
which keeps the data authority shared. Some routes also construct operational
objects directly. Examples include creating `BacktestEngine(repo)` in the
backtest route and multiple `ScreenerService(repo, asset_type=...)` instances in
strategy and screener routes.

This creates two service-lifetime models without an explicit rule:

- process-owned services live in `app.state` and may own threads, caches, or
  scheduler state;
- request-created services are ephemeral even when they may encapsulate caches
  or expensive setup.

The code should be judged by ownership, not by a class name. Stateless adapters
are safe to construct per call; cache owners, worker owners, and mutable policy
holders need a deliberate lifetime.

TickFlow also uses setter injection after construction: `set_repo`,
`set_app_state`, `set_strategy_engine`, and history-loader setters. This avoids
large constructors and permits staged wiring, but allows a partially initialized
object to exist. The composition root must remember the correct call order, and
the object contract does not make all required dependencies obvious.

## Warm-up and Readiness

The enriched indicator cache can be expensive to build. TickFlow deliberately
calls `repo.refresh_cache(background=True)` and lets the HTTP application become
available while it computes. A callback changes
`app.state.indicators_ready` from false to true, and `/api/data/status` exposes
that fact.

This is a valuable semantic distinction:

```text
process responds        = /health status ok
analytical cache ready  = /api/data/status indicators_ready true
```

The UI can therefore display warming rather than freezing startup or claiming
full analytical availability. The limitation is that the desktop bootstrap
polls only `/health`. It proves lifespan initialization reached `yield`, not that
the indicator cache is ready. The readiness fact also sits on a data-status
endpoint rather than a named readiness contract that all clients and supervisors
are likely to understand.

The callback itself is a private repository hook (`_on_warmup_done`). Private
callbacks are fragile, and a boolean alone lacks start time, generation, last
success, last error, progress, and stale-ready detection. If a background warm-up
fails without invoking the success callback, the UI may remain false, but the
operator receives no structured reason from this fact alone.

## Startup Semantics

TickFlow implicitly divides startup into three classes.

### Core initialization

DataStore, repository, initial cache setup, capability detection, and several
service constructions occur without protective exception handling. A failure
prevents lifespan from yielding, which is appropriate when routes cannot operate
honestly without the object.

### Optional or degradable initialization

Authentication bootstrap, custom sources, main APScheduler, depth startup,
WeCom, extension presets, monitor migration, and monitor-rule loading catch broad
exceptions, log warnings, and continue. This keeps a local analytical panel
usable when a peripheral integration fails.

### Unclassified initialization

Extension pull and financial scheduler startup, quote boot, and some strategy
construction are neither named as required nor isolated as optional. Their
failure behavior is whatever happens to emerge from placement in the lifespan.
The absence of a declared policy makes future edits risky: moving one call into
or out of a try block silently changes application availability.

The built-in preset ordering is especially worth preserving. New preset config
is created before the pull scheduler refreshes. That is a small but durable
example of making bootstrap order correspond to a real invariant, rather than
starting everything opportunistically.

## Sync and Async Boundaries

The lifespan is async, but much of its work is synchronous: capability probing,
repository setup, quote checks, scheduler starts, service construction, and
several disk/config reads. This work occurs before `yield`, so it can delay port
binding and block the event loop during startup.

Moving all work to threads would not be automatically better. Required, short,
deterministic initialization is easier to reason about inline. Potentially slow
network probes, large CPU transforms, and blocking aggregations should be
explicitly classified, timed, bounded, and either moved off-loop or represented
as post-yield degraded work.

TickFlow uses raw daemon threads for matrix-cache prewarming. A lock and
`matrix_prewarm_running` boolean provide single-flight behavior, which is good.
The application retains no thread handle, cannot cancel or join it, and can shut
down while it is using repository/strategy objects. A daemon thread trades a
clean process exit for an unproven consistency boundary.

## Scheduled Work and Shared State

The daily scheduler is constructed with a repository and initial capability
set. For work that must react to a key expiring or being upgraded, the scheduled
pipeline re-reads `app.state.capabilities`; an hourly re-probe replaces that fact
only on success and preserves the previous set on probe failure.

The pipeline also pauses QuoteService while it performs a write/refresh cycle,
then calls `repo.refresh_cache()` in `finally`. These are strong ideas:

- a scheduled writer coordinates with a competing live reader/writer;
- process memory is refreshed even after partial pipeline failure;
- capability change does not require a process restart;
- failed re-detection does not destroy the last known capability description.

The mechanism is less strong than the intent. `daily_pipeline.set_app_state`
stores a module-global application-state reference, creating a second service
locator outside FastAPI. The pipeline can now depend on any application-state
field without declaring it. Refreshing cache in `finally` also needs Domain 9
analysis: it may be correct after atomic partial outputs, but could publish an
inconsistent generation if underlying writes are not transactional.

## Shutdown

Shutdown stops the main scheduler, pull scheduler, financial scheduler, quote
service, depth polling, and WeCom bot. This is better than abandoning obvious
long-lived services.

The sequence lacks an encompassing `try/finally` per resource. If one stop call
raises, later services may not stop and the final log may not be reached. The
matrix prewarm thread is not owned at shutdown. There is also no visible reverse
dependency rule: producers are not explicitly stopped before the consumers or
repositories they can use, and shutdown does not distinguish a clean exit from a
crash for later diagnosis.

## Valuable Practices

### One process repository for shared analytical caches

The same repository object coordinates cache reads across routes, services, and
jobs. This avoids accidental per-request cache islands.

### Honest background warm-up

Slow derived data does not block the whole UI, and the application exposes an
explicit not-ready fact rather than pretending all data is immediately usable.

### Startup order based on data dependencies

Creating presets before refreshing schedules and fixing data generation before
concurrent backtests are practical examples of lifecycle invariants.

### Pause competing activity around a write/refresh boundary

The quote pause around the daily pipeline acknowledges that application-level
coordination matters even when storage calls look individually safe.

### Hot capability replacement

Provider entitlement can change while the process is alive. Re-probing and
publishing a new immutable capability set is more truthful than freezing startup
state indefinitely.

## Failure Modes and Questions for Later Domains

- Does every repository background failure publish structured degraded state?
- Can a daemon prewarm read files while shutdown or a pipeline replaces them?
- Are schedulers guaranteed single-process, or can two app instances write the
  same data directory?
- Does cache refresh after partial failure expose a mixed generation?
- Are route-created services stateless, or do they duplicate valuable caches?
- Can QuoteService pause cover every path that overwrites the same Parquet data?
- Are blocking startup calls bounded under an unreachable provider?

These questions require Domains 7 through 11 or bounded runtime evidence; they
are not answered by the composition root alone.

## Evidence Boundary

All mechanisms above were traced statically. Thread termination, cache warmth,
startup duration, process concurrency, and scheduler behavior were not observed
at runtime.
