# Domain 6 Lessons and Reflections

## Reuse Directly

### Publish domain warm-up separately from process liveness

Keep the UI responsive during expensive analytical cache construction, but
return an honest warming/unavailable fact for the affected feature. Do not call
the whole system ready merely because the port is open.

### Preserve one shared analytical repository/cache owner

Routes, scheduled refresh, and analytical services should not silently create
independent hot-cache islands over the same files. Give the owner a clear
process lifetime and generation identity.

### Encode bootstrap dependencies in startup order

If a scheduler reads presets, create/validate the presets first. If concurrent
workers depend on a generation, establish it first. Record the invariant next
to the ordering.

### Re-read mutable external capabilities

Provider entitlement can expire or improve while a process is running. Publish
a new capability snapshot on successful re-probe and keep last-known state plus
a visible probe-health fact when re-detection fails.

### Coordinate readers and writers at application level

When a background pipeline replaces analytical files and a live quote service
can observe them, deliberately pause/coalesce or switch generations. Individual
file APIs cannot infer the whole application transition.

## Reuse After Adaptation

### Typed runtime context instead of an untyped service locator

Keep explicit construction, but expose a typed context or narrow dependency
providers. A route should request `MarketReadService`, not know that it lives at
`request.app.state.repo` or in a module singleton.

### Lifecycle manifest

For each started component, record:

```text
owner
required dependencies
startup phase: pre-bind or post-bind
failure policy: fail, degrade, pause, or retry
readiness facts
task/thread handle
shutdown order and timeout
```

This can begin as executable structure or concise design documentation; it
should not become a second progress tracker.

### Rich warm-up state

Replace a lone boolean with a compact typed fact containing state, generation,
started/finished timestamps, last successful generation, and sanitized failure
reason. A feature can serve stale-but-known data differently from no data.

### Narrow dependency injection

Use FastAPI dependencies where they improve declaration and overrideability:
request identity, runtime context, unit-of-work, and selected service interfaces.
Do not wrap every pure helper or turn the framework into a global container.

### Explicit startup timing and budgets

Retain Aletheia's stage timing. Add a classification for potentially blocking
work and a budget/timeout where external network or large aggregation is
involved. Choose inline, dedicated executor, or post-yield task by semantics.

### Task ownership registry

Named asyncio tasks, scheduler handles, and any unavoidable threads should have
one runtime owner. Shutdown should stop producers, prevent new work, wait or
cancel within a budget, consume failures, then close shared resources.

### Readiness vocabulary

Expose liveness, interface readiness, data readiness, operational authority, and
business health as distinct facts. Supervisors and UI consumers should select
the fact they actually need.

## Study Further

- Trace whether TickFlow's repository warm-up publishes failures or can remain
  indefinitely false without a structured reason.
- Determine whether APScheduler can run in more than one TickFlow process over
  the same data directory and what prevents overlapping writers.
- Inspect every process-owned TickFlow service for a start/stop pair and any
  child threads it creates.
- Classify Aletheia's module-level singleton factories by lifetime and mutable
  state; identify only the highest-cost hidden dependencies.
- Check whether Aletheia startup gates publish one coherent operational-readiness
  view or require the UI to infer it from several responses.
- Measure pre-yield startup stages only if runtime performance becomes an actual
  problem; source structure alone cannot rank their latency.
- Decide whether route-created screeners/backtest engines are truly stateless or
  duplicate cache/setup work.
- Carry the pipeline pause/cache-refresh question into Domain 9 before treating
  it as a complete consistency proof.

## Do Not Reuse

### Raw daemon threads touching shared state

Do not start a fire-and-forget daemon thread without a retained handle, failure
surface, shutdown policy, and generation/transaction boundary.

### Private callback injection as a public lifecycle contract

Assigning `_on_refresh_done` or `_on_warmup_done` from the application root is
fragile. Use a declared callback/event interface or an owned task result.

### Module-global application-state references

`set_app_state()` makes every future field reachable from scheduled code and
hides the job's real inputs. Pass a narrow context or service interface.

### Broad best-effort handling for authority-bearing services

“Log and continue” is unsuitable for migration mismatch, risk initialization,
execution authority, audit requirements, or unresolved reconciliation. Preserve
Aletheia's fail/pause semantics.

### Readiness inferred only from the listening port

A bound HTTP port proves neither analytical data readiness nor operational
authority. Do not use one health endpoint for every supervisor and UI decision.

### Setter injection without an invalid-state guard

If a service cannot function without repository or policy, prefer constructor
requirements or an explicit build/finalize step that makes partial state
unusable.

### Linear shutdown that aborts after the first error

Every resource must get its stop opportunity even when another component's stop
raises. Aggregate/log sanitized failures after attempting the ordered shutdown.

### Per-request reconstruction of stateful cache owners

Construct cheap stateless adapters freely. Do not reconstruct workers, mutable
policy holders, or heavy cache owners inside routes by habit.

## Durable Design Reflections

### “Where is the object?” is an ownership question

`app.state`, a global singleton, and a dependency provider are access
mechanisms. None by itself defines lifetime, concurrency, authority, or cleanup.
The useful design unit is an owned resource with declared consumers.

### Ready is not a scalar

A local trading application can be alive, render its dashboard, warm derived
data, lack an external model, and remain safely paused—all at once. A single
green/red endpoint discards operational truth.

### Port-first startup is safe only with authority-first gates

Letting the UI bind before slow external probes improves diagnosis. It is safe
only if no job, agent, or execution path can act before durable pause and gate
checks apply.

### Failure isolation needs a reason, not just an exception block

Optionality is a product decision. Each catch-and-continue branch should answer
what remains usable, what fact becomes degraded, and how recovery occurs.

### Shutdown is a consistency operation

Stopping producers before consumers, completing or cancelling owned work, and
recording clean termination protect the meaning of the next boot. Shutdown is
not merely process hygiene.

### Explicit construction and explicit consumption are complementary

TickFlow makes construction visible; Aletheia makes many consumers independent
of FastAPI. A typed runtime context can preserve both without centralizing all
business logic in the web framework.

### Lifecycle refactoring should follow Alpha pressure

The application root can be made prettier indefinitely. Extract a boundary only
when it improves a real startup failure, data-staleness explanation, worker
ownership problem, or delivery speed. Do not replace Alpha work with container
architecture for its own sake.

## Evidence Boundary

These lessons are derived from static source and call-path analysis. No runtime
claim about latency, cancellation, thread survival, scheduler overlap, or cache
correctness has been validated.
