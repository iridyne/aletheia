# Research Reflections

## Why This Reference Was Valuable

TickFlow and Aletheia use a superficially similar stack, but their architectural
strengths come from different pressures. That made the comparison more useful
than a same-purpose codebase would have been.

TickFlow is shaped like an operator-facing analytical product. It repeatedly
optimizes comprehension, data density, optional provider access, interactive
latency, local packaging, and the presentation of long-running work.

Aletheia is shaped like a safety-sensitive automated trading system. It places
more weight on canonical state, exact market identity, causal records,
fail-closed authority, durable execution evidence, verified migration, and
recovery.

The most productive question therefore was not “which implementation is
better?” It was:

> Which local design is correct for the kind of truth that component owns?

That question prevented attractive analytical techniques from being copied
into transactional authority, and prevented safety mechanisms from being used
as an excuse for a less understandable operator experience.

## Research Method That Worked

### Start with system shape, then follow ownership

The study began with repository topology, entrypoints, dependencies, route
families, lifecycle construction, and storage locations. After that, each
domain followed a fact through its owners:

```text
source or user intent
  -> transport
  -> normalization
  -> validation
  -> mutation/publication
  -> cache/read model
  -> realtime notification
  -> UI state
  -> persistence/restart/recovery
```

This was more revealing than reading large files sequentially. It exposed
places where a polished surface was backed by weak termination or completeness
semantics, and places where modest UI code sat on top of unusually strong
authority guarantees.

### Use CodeGraph before textual search

Indexing the reference repository made symbol definitions, consumers, and call
paths the primary navigation layer. Text search remained useful for:

- configuration keys and route strings;
- dependency/workflow inventories;
- migration and packaging files;
- tests and security-relevant process execution;
- confirming absence or distribution of a pattern.

The combination reduced the risk of judging an isolated helper without seeing
how it was reached.

### Separate source evidence from runtime proof

This was a static analysis program. No pytest suites were run, and the reports
do not claim that unexecuted code works in production.

The evidence vocabulary used throughout was:

- **observed in source**: implementation or configuration exists;
- **statically inferred**: behavior follows from connected code paths but was
  not executed;
- **repository inventory**: counts or file presence, not test outcome;
- **not available**: a runtime, deployment, scale, or user-behavior fact was not
  present in the repository;
- **hypothesis/experiment**: a bounded way to obtain missing evidence later.

This boundary matters especially for concurrency, cancellation, filesystem
durability, provider behavior, packaging, and release recovery.

### Compare invariants, not library names

React Query, Zustand, Tailwind, Polars, DuckDB, Parquet, APScheduler, PyInstaller,
and FastAPI are implementation choices. The reusable knowledge lies one level
deeper:

- Who owns identity?
- When is a fact authoritative?
- What does partial mean?
- Which event proves publication?
- Can a timeout stop physical work?
- Can a cache be reconstructed?
- Can the operator see degradation?
- Does the final artifact preserve source assumptions?

This distinction is the main defense against technology-first expansion.

## Recurring Pattern: Polished UI Comes from Constraint Coherence

TickFlow's frontend does not feel coherent because of one component library.
Its polish emerges from agreement among:

- semantic color and surface depth;
- numeric typography and dense spacing;
- repeated page composition;
- eager navigation with lazy feature content;
- visible loading/progress/error states;
- user-controlled table columns and view persistence;
- charts that retain operator context;
- global shell indicators for long-lived concerns.

The transferable lesson is to standardize behavior and meaning before pursuing
component uniformity. A shared button style is less valuable than a shared
answer to whether a destructive action is disabled, confirmable, pending,
retryable, or complete.

The reference also showed where visual coherence stops: custom calendars,
mouse-oriented dense controls, unconditional motion, and raw button variants
can still produce accessibility or semantic inconsistency. Surface polish is
not proof of interaction completeness.

## Recurring Pattern: Data Components Are Query Planners

One of the strongest ideas from the study is that dense tables and charts are
not merely renderers. They negotiate a resource contract across layers.

A configurable column may imply:

- an optional provider endpoint;
- a rate-budget cost;
- an extra join or analytical scan;
- a different cache key;
- a partial-result condition;
- a width and virtualization cost;
- a persistence/migration identity.

A chart viewport similarly carries operator context that should survive data
refresh and container geometry changes.

This suggests a richer component contract for genuinely expensive analytical
views, but not for every table. The key is to measure source, result, and render
cardinality separately and add complexity only at the bottleneck.

## Recurring Pattern: Realtime Should Carry Change, Not Become Truth

Both the benefits and failure modes of realtime synchronization became clearer
when expressed as a publication protocol:

```text
authoritative commit
  -> committed change identity
  -> best-effort low-latency notification
  -> typed dependency routing
  -> authoritative read/reconciliation
```

This yields four useful separations:

- connection state is not data freshness;
- notification receipt is not successful refetch;
- event time is not committed-change order;
- last rendered data may remain useful while explicitly stale.

TickFlow's visible connection ownership and event-specific invalidation are
worth adapting. Aletheia's stronger catch-up and authority model should remain.
The remaining opportunity is to make the dependency graph and committed
identity explicit enough that corrections, reconnect gaps, and invalidation
cost can be reasoned about.

## Recurring Pattern: Completeness Is a First-Class Data Property

Many of the most important findings across provider normalization, analytical
storage, I/O, jobs, and UI were the same finding under different names:

> Non-empty is not complete, atomic file replacement is not coherent dataset
> publication, and successful traversal is not successful publication.

A useful data result needs to state:

- requested scope;
- acquired/processed/published scope;
- missing, rejected, and failed scope;
- source and actual identity;
- schema/computation version;
- event/source/ingest/publication times;
- whether replacement is authorized;
- whether the prior complete generation remains active.

Once completeness is explicit, UI partial states, retry behavior, cache
identity, publication, and audit become easier to align. Without it, every
layer invents its own guess.

## Recurring Pattern: Logical State Must Follow Physical Ownership

The background-job analysis produced a broadly applicable rule:

> A logical terminal label cannot release a resource that physical execution
> still owns.

This is visible in several reference patterns:

- setting “cancelled” without stopping the worker;
- marking a thread-executor task timed out while it continues running;
- reaping a stale lock while the writer remains alive;
- storing active state only in process memory;
- reporting progress completion before classifying publication outcome.

The stronger model reserves attempt identity and capacity before work, tracks
the physical handle, separates cancellation request from termination proof, and
reconciles abandoned attempts after restart. Progress can be compacted; terminal
truth and resource ownership cannot.

## Recurring Pattern: Representations Should Be Purpose-Built and One-Way

TickFlow demonstrates why analytical performance often improves through
multiple representations rather than one universal database:

- transactional preferences and metadata;
- Parquet history partitions;
- DuckDB views over cold columnar data;
- Polars for projected transformations and latest cross sections;
- dense matrices for repeated vectorized backtests;
- in-memory caches for hot derived results.

The lesson is valuable only with an authority direction. In Aletheia, a derived
analytical plane should be reconstructible from an exact committed watermark
and must never become an undocumented second writer for trading truth.

The study also reinforced that each representation needs a manifest. File
paths and modification time are not enough to prove source, schema, formula,
coverage, layout, completeness, or rebuild identity.

## Recurring Pattern: Product Packaging Is Part of Architecture

TickFlow's multi-platform workflows, desktop packaging, local data placement,
and final-binary runtime check made packaging visible as engineering work. The
analysis also uncovered the opposite lesson: building an artifact is not the
same as proving it safe to activate.

Release assurance needs one chain from source to exact digest:

```text
verified source and dependencies
  -> final platform artifact
  -> property and secret-exclusion checks
  -> isolated install/start/readiness
  -> upgrade and migration preflight
  -> backup/restore
  -> activation health soak
  -> rollback
  -> publish the tested digest
```

This is one area where Aletheia already has stronger conceptual machinery and
can still learn from TickFlow's explicit platform-specific artifact assertion.

## Security Lessons Were Architectural, Not Cosmetic

Several negative findings are valuable precisely because the surrounding
product is otherwise polished:

- bearer-equivalent webhook values stored in general preferences and returned
  by settings APIs;
- configurable outbound HTTP with broad URL/method/body freedom;
- AI-generated Python checked by AST and then loaded into the server process;
- pip/npm installation into the active runtime;
- forwarded-header trust coupled to private-peer assumptions;
- long-lived raw persisted sessions;
- application data placed inside a macOS app bundle.

These are not isolated “security bugs” that disappear with input validation.
They are authority and lifecycle design problems. The corresponding positive
contracts are write-only credentials, destination policy, credential-free
isolated workers, immutable dependencies, explicit proxy trust, protected
session material, and stable external data roots.

## What Aletheia Should Preserve

The comparison made several existing Aletheia choices look more important, not
less:

- one canonical transactional SQLite authority for trading and business state;
- exact venue/instrument/market/timeframe identity;
- closed-bar and consumer-relative freshness semantics;
- Risk approval before the Execution Gateway;
- live disabled by default and backend-enforced confirmation;
- durable writer, run, outbox, audit, and collector identities;
- process-role separation for main, shadow, candidate, or helper runtimes;
- liveness/readiness/status distinctions;
- write-only credential interfaces;
- migration ledger and verified WAL-safe state movement;
- signed update, preflight, activation proof, and rollback concepts.

Borrowing from TickFlow should make these invariants easier to see and cheaper
to use, not bypass them.

## What Aletheia Can Learn First

The highest-leverage near-term learning is not a new database. It is a clearer
contract between existing layers:

- typed query identities and explicit change-to-read-model routing;
- semantic state presentation tied to backend facts;
- visible transport degradation without confusing it with freshness;
- provider results that carry provenance, limits, and completeness;
- durable attempt/progress/cancellation vocabulary;
- release checks against the exact final artifact;
- bounded telemetry that connects request, run, publication, source, runtime,
  and release identities.

These improvements reduce ambiguity before adding capability.

## Where Further Investigation Is Justified

Some questions cannot be answered responsibly from source alone:

### Actual analytical bottlenecks

Repository architecture suggests possible value from Parquet, DuckDB, Polars,
or matrices, but only a named Aletheia workload can justify one. Needed evidence
includes data shape, cold/warm latency, peak memory, concurrency, update
frequency, and the business value of the result.

### Real operator friction

Static UI analysis can find inconsistency and missing states, but it cannot show
which workflow causes mistakes or delay. A bounded observation or replay should
identify the decision, the fact needed, and where the current surface obscures
it.

### Platform artifact behavior

Workflow files reveal intent, not whether Windows/macOS packages start cleanly,
place data safely, preserve migration invariants, or roll back. Those claims
need the exact platform artifact in an isolated environment.

### Failure timing and concurrency

Thread cancellation, process termination, SQLite contention, file publication,
websocket overflow, and restart reconciliation require deterministic failure
injection or runtime traces. Static inference identifies risk, not incidence.

### Provider contracts

Provider schemas, limits, entitlements, and correction behavior can change.
Fixtures should be sanitized and versioned, but selected live probes may still
be needed before relying on a capability promise.

## Limits of This Study

- The reference was studied at the recorded commit in the research baseline;
  later changes are not represented.
- No production environment, real provider account, user session, or platform
  installer was exercised.
- Test files and workflows were inventoried but not executed.
- Static call paths cannot prove scheduler timing, OS behavior, network failure,
  memory pressure, or third-party responses.
- Aletheia comparisons identify architectural opportunities and risks; they are
  not approvals to change safety-sensitive code.
- Quantitative benefit remains **not available** where the repositories contain
  no benchmark or runtime observation.

These limits are retained deliberately so the research remains a trustworthy
input rather than a confident-looking substitute for evidence.

## Final Reflection

The reference's greatest contribution is not a list of packages. It is a set of
contrasts that sharpens Aletheia's own architecture:

```text
clarity without invented certainty
density without unbounded work
realtime without a second authority
analytics without contaminating transactions
progress without false termination
extensibility without inherited privilege
packaging without confusing build with proof
```

The best combined system would feel as understandable and analytically capable
as TickFlow while remaining as explicit about state, safety, provenance, and
recovery as Aletheia must be.
