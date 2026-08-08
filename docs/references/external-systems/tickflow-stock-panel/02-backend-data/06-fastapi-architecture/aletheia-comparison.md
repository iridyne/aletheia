# Domain 6 Aletheia Comparison

## Same Problem, Different Risk Level

Both applications must assemble a FastAPI process, expose a local web UI, share
data services with jobs, tolerate optional integrations, and shut down workers.
Aletheia additionally owns transactional trading state, broker connectivity,
durable audit facts, risk gates, and multiple runtime roles. TickFlow's lifecycle
ideas therefore cannot be copied without preserving stricter authority and
recovery boundaries.

## Composition and Dependency Access

TickFlow has a smaller, more concrete object graph. One DataStore, repository,
and several service objects are visibly built in the lifespan and made available
through `app.state`. This is easy to trace from a single file.

Aletheia barely uses `request.app.state` in routes. Instead, routes repeatedly
call imported accessors such as `load_config()`, `get_db()`, and
`get_agent_job_manager()`, while other subsystems expose module-owned singleton
services and tasks. This reduces coupling to FastAPI's container shape but does
not provide declared dependency injection. It moves hidden dependencies from
`app.state` into imports and globals.

Neither application currently uses FastAPI `Depends(...)` in its server tree.
The desired lesson is not “convert everything to Depends.” A small typed
application context plus narrow dependency providers could make ownership and
test overrides explicit, while durable transaction helpers and pure functions
remain direct imports.

## Lifecycle Size and Comprehension

TickFlow's application root is 391 lines; Aletheia's is 909. Aletheia's size
comes partly from real responsibilities: runtime identity, migration gates,
remote-control security, static build recovery, multiple startup safety gates,
event-loop monitoring, startup sync, and clean shutdown evidence.

The richer semantics are valuable, but their order is embedded in one large
function. A future reader must reconstruct which stages are required, which are
best-effort, which run before the port binds, which start after yield, and which
may create child recovery loops. Named stage functions or a lifecycle manifest
would improve comprehension without weakening controls.

## Startup Gates

Aletheia is stronger in several concrete ways:

- database migrations are verified before any writer starts;
- stale runs are reconciled and a runtime session is opened after schema
  verification;
- background services start only in the current runtime, not shadow/candidate
  instances;
- slow exchange and LLM probes become named post-yield tasks;
- blocking exchange aggregation uses a dedicated worker executor;
- startup behavior, LLM, and connectivity failures pause authority-bearing work
  and expose durable degraded state instead of necessarily killing the UI;
- startup stages log elapsed time.

These are essential for a trading system. TickFlow's simpler “warn and
continue” policy is acceptable only for a peripheral analytical feature. It is
not suitable for migrations, risk, execution, reconciliation, or audit.

The post-yield approach has a semantic cost. The port may respond while gates
are still running. Aletheia accounts for this by making scheduler jobs re-check
pause state and blocking the PI daemon synchronously when the behavior gate says
no. That invariant must remain if startup continues to prioritize immediate UI
availability.

## Readiness Semantics

TickFlow directly exposes `indicators_ready`, a useful domain readiness fact.
Aletheia exposes three distinct surfaces:

- `/api/ready` and `/_infra/livez`: process/event-loop liveness;
- `/_infra/readyz`: API plus static-dashboard availability;
- `/api/health`: deeper business health and issues.

The separation is thoughtful, but `/_infra/readyz` intentionally stays green
when trading, model, market, or startup-gate health is degraded. That makes it an
infrastructure routing signal, not an operational-readiness verdict. The name
alone can be misread.

Aletheia should preserve the separation and make the vocabulary explicit:

```text
alive              process and event loop respond
interface-ready    HTTP API and dashboard assets are available
data-ready         required local read models/generations are available
operation-ready    authority-bearing workflows have passed their gates
healthy/degraded   system can explain current incidents and limitations
```

TickFlow's indicator fact is a useful input to this model, not a replacement
for it.

## Background Work Ownership

Aletheia's lifespan gives named asyncio tasks to slow startup work, retains their
handles, cancels and consumes them, and explicitly stops child recheck loops.
Its shutdown comment states and implements “producers before consumers.” It
closes the inference broker, stops operator and Telegram activity, ends the
runtime session cleanly, and only clears a PID file it owns.

This is materially stronger than TickFlow's raw daemon matrix-prewarm thread and
linear shutdown calls. Aletheia should not regress to fire-and-forget threads for
work that touches shared state.

Aletheia still has module-owned recovery loops that are not direct lifespan
children. The explicit stop calls compensate, but ownership is distributed. A
task registry scoped to the runtime could make “who starts, monitors, and stops
this?” mechanically visible.

## Sync and Async Boundaries

Aletheia deliberately moves long blocking exchange sync off the event loop and
documents why a dedicated executor is used instead of ordinary
`asyncio.to_thread`. TickFlow's startup has more unclassified synchronous calls
inside async lifespan.

This does not prove Aletheia is uniformly non-blocking. Many routes are sync
functions so FastAPI will place them in a worker thread, and lifecycle imports,
config reads, migrations, job-manager startup, and local gates still run inline.
The stronger reusable practice is explicit classification and timing, not a
blanket async rewrite.

## Failure Policy

TickFlow's broad exception handling optimizes panel availability. Aletheia uses
a more suitable hierarchy:

| Failure | Current Aletheia behavior | Why |
|---|---|---|
| stale database migration | fail startup | writers must not run against unknown schema |
| scheduler startup | warn and keep UI available | operator can diagnose; work remains controlled |
| LLM/exchange unavailable | pause and persist degraded state | process availability must not imply trading authority |
| startup behavior blocker | prevent PI daemon and layer pause | local truth gates AI/action startup |
| runtime-session evidence failure | warn | observability should not always prevent local recovery UI |
| missing dashboard assets | explicit 503 page and readiness failure | avoid a misleading API-only “healthy” interface |

The remaining weakness is that these policies are distributed through comments
and exception blocks rather than one reviewable policy map.

## What TickFlow Does Better

### Visible shared analytical object graph

The repository and its consumers can be followed from one composition root.
Aletheia's database/config/service access is more dispersed.

### A named domain warm-up fact

`indicators_ready` tells the UI that process readiness and derived-data
readiness differ. Aletheia has many health facts but no single typed catalog of
data-read-model readiness.

### Dependency-based bootstrap ordering

Preset creation before scheduler refresh is a compact, easy-to-review invariant.
Aletheia has comparable ordering, but its larger lifespan makes the dependency
graph harder to see.

### Coordination around analytical replacement

Pausing quotes while the pipeline writes and refreshing shared cache afterward
is a useful pattern for analytical data. Aletheia must adapt it to SQLite
authority, audit, and execution invariants rather than use a global pause as a
transaction substitute.

## What Aletheia Must Preserve

- migration verification before writers;
- background-service isolation by runtime role;
- Risk approval before Execution Gateway activity;
- live disabled by default and layered startup pauses;
- named task handles and cancellation;
- producer-before-consumer shutdown;
- durable runtime-session distinction between clean exit and crash;
- local UI availability during external-provider degradation, without granting
  action authority;
- remote-control fail-closed access and minimal anonymous liveness surface.

## Architectural Opportunity

A small typed runtime context could combine the strongest properties of both
systems. It would own stable process services and lifecycle task handles, expose
narrow getters to routes/jobs, and publish typed readiness facts. Construction
would remain explicit, while routes would no longer know the entire container or
depend on unrelated module globals.

This is an architectural observation, not an implementation recommendation.
Any change should begin on one bounded analytical service and prove that it
reduces hidden coupling without detouring Alpha work.

## Evidence Boundary

The comparison is static. No startup, shutdown, migration failure, gate failure,
thread leak, or readiness response was exercised. Runtime behavior described as
an intention is included only where the current control flow implements it.
