# Executive Summary

## Overall Judgment

TickFlow is a high-value reference because it solves the same broad technical
shape as Aletheia—a Node/React frontend over a Python/FastAPI backend—while
optimizing for a different center of gravity.

TickFlow is strongest at:

- turning complex data work into understandable operator feedback;
- presenting dense analytical information without making the UI feel chaotic;
- separating hot interactive read models from cold analytical storage;
- exploiting Polars, Parquet, DuckDB, matrix layouts, caching, and process
  isolation for research/backtest workloads;
- making optional provider capabilities, limits, data coverage, and platform
  packaging visible;
- shipping an accessible multi-platform product.

Aletheia is stronger at:

- exact trading identity and semantic provenance;
- risk/execution authority separation and fail-closed controls;
- canonical transactional state and cross-process writer serialization;
- durable collector/run/outbox/audit semantics;
- loopback-first remote access and write-only credential APIs;
- liveness/readiness/health/status separation;
- PR verification, secret scanning, migration identity, verified backup,
  signed update, activation health proof, and rollback.

The correct synthesis is not to “move Aletheia onto TickFlow's architecture.”
It is to add TickFlow's analytical plane and operator clarity around Aletheia's
existing authority plane.

## The Most Important Combined Principle

```text
One authoritative business state
  -> explicit committed identity and provenance
  -> one-way derived analytical representations
  -> push invalidation plus authoritative reads
  -> visible freshness, coverage, completeness, and degradation
  -> bounded supervised work
  -> verified release and recovery chain
```

Every optimization remains downstream of authority. Every UI claim remains
traceable to a backend fact. Every long-running action has an owner and terminal
truth. Every replaceable artifact states its source, generation, coverage, and
rebuildability.

## Strongest Practices Worth Learning

### 1. A coherent visual constraint system

TickFlow feels polished because semantic color, depth, radius, density, numeric
typography, and page composition agree. The reusable lesson is not Tailwind or
Framer Motion. It is a constrained visual vocabulary combined with repeated
behavioral primitives.

High-value extractions:

- one modal focus/backdrop/escape contract;
- a semantic empty/degraded/unavailable state family;
- visible page-purpose text;
- a chart-palette adapter from UI semantics to canvas libraries;
- separate market-direction colors from application status colors;
- reduced-motion and offline/local-font boundaries.

### 2. An eager operational shell with lazy feature pages

TickFlow keeps global navigation mounted while lazy-loading major pages. It
also splits heavy chart vendors, preserves cached onboarding decisions during
background refresh, and redirects retired paths to canonical routes.

Aletheia should preserve its workflow-oriented navigation taxonomy and
responsive shell, but can adopt:

- a single route metadata manifest;
- eager shell/lazy outlet boundaries;
- destination-aware loading and route error recovery;
- canonical aliases/redirects;
- small globally visible status components whose data ownership stays local.

### 3. Data density as a resource contract

TickFlow coordinates visible columns, optional I/O, provider limits, virtualized
rendering, user view preferences, and chart viewport retention. This is more
useful than copying its table styling.

The key ideas are:

- distinguish source/result/render cardinality;
- virtualize only after a measured threshold;
- let visible expensive columns declare bounded fetch requirements;
- expose pending, missing, filtered, truncated, stale, and unavailable
  separately;
- preserve zoom/logical range across refresh;
- observe the real container with `ResizeObserver`;
- treat a configurable analytical column as part of the query plan.

### 4. Query keys and realtime routing as architecture

TickFlow centralizes query keys, shared observers, user-visible transport state,
and event-specific invalidation. Its realtime channel usually announces what
changed; React Query then fetches the authoritative REST representation.

This confirms the best shared pattern:

```text
commit state
  -> publish domain change identity
  -> map change to typed query families
  -> refetch authoritative representation
  -> preserve last value with explicit staleness on failure
```

Aletheia should keep its stronger initial catch-up, exact REST authority, and
visibility-aware socket lifecycle while adding visible connection degradation,
typed query-key factories, coalesced invalidation, and committed change
sequences where timestamp fingerprints are insufficient.

### 5. Capabilities, limits, and provider choice as backend facts

TickFlow models observed entitlements, batch/RPM limits, per-dataset provider
selection, versioned capability snapshots, safe declarative mappings, and
segmented history acquisition.

Aletheia should adapt this into a descriptive provider contract without using
it as execution permission. The contract should expose:

- provider/physical venue;
- datasets and exact resolutions;
- instrument and market-type support;
- requested versus actual identity;
- configured, entitled, healthy, and permitted as separate axes;
- rate/page/history budgets;
- freshness and last success;
- complete/partial/empty/failed outcome with rejected scope.

Aletheia's exact instrument identity, closed-bar semantics, consumer-relative
freshness, exact protective pricing, and durable collector taxonomy must remain
authoritative.

### 6. A purpose-built analytical plane

TickFlow's deepest technical strength is representation discipline:

- Parquet partitions for columnar history;
- DuckDB views for ad hoc/cold queries;
- Polars latest cross sections and live aggregates for interactive paths;
- projected scans and sparse partition reads;
- versioned byte-bounded matrix caches for dense backtests;
- single-flight and prewarm for repeated expensive work;
- timings, RSS, cache, and serialization telemetry.

This does not justify moving Aletheia's trading state out of SQLite. It suggests
a future one-way, reconstructible analytical mirror only when a measured Alpha
or research workload needs it:

```text
committed SQLite closed-market facts
  -> supervised export at an exact watermark
  -> versioned columnar partitions and manifest
  -> named workload-specific read models
  -> optional byte-bounded matrix cache
```

Orders, approvals, fills, positions, risk decisions, settlements, outcomes,
and audit evidence remain transactional SQLite authority.

### 7. Progress as a real domain protocol

TickFlow's background jobs show the value of:

- reserving identity while pending, before execution starts;
- overall plus stage-local progress;
- high-frequency progress compaction;
- server-owned task identity and reconnect;
- workload-specific executors;
- process isolation for memory-heavy analysis;
- schedule facts beside data health.

The reference also proves the danger of presentation without execution truth:
its pipeline “cancel” changes metadata but does not stop work, stale reaping can
release a lock while the writer thread is still alive, and active job state
disappears on restart.

Aletheia should borrow the progress and presentation model only on top of a
durable attempt record, physical capacity ownership, honest partial publication,
and cancellation that distinguishes requested, acknowledged, cooperative stop,
forced termination, and terminal cleanup.

### 8. Verify final-artifact properties

The TickFlow Windows release checks that the final PyInstaller artifact contains
both required Polars runtimes. This is a small, excellent release practice.

Aletheia should verify analogous properties in the immutable package: native
host, dashboard assets, trusted update key, manifest identity, SQLite/crypto
imports, clean isolated startup, readiness, secret exclusion, shutdown, and
rollback fixture.

## Critical Practices Not to Copy

### Credential readback

TickFlow returns Feishu and WeCom bearer-equivalent secrets through general
settings APIs and stores them in an unprotected preferences file. Aletheia must
preserve write-only/status-only credential APIs and strengthen schema-level
credential classification.

### Partial acquisition as complete replacement

Several reference sync paths can overwrite complete files with partial frames
or advance “last sync” after zero/partial outcomes. Completeness, not non-empty
row count, authorizes replacement.

### File atomicity as dataset atomicity

Replacing individual Parquet files safely does not publish one coherent
multi-file generation. A generation manifest/pointer must be published after
all partitions validate.

### Timeout as proof of termination

A status timeout cannot interrupt a Python executor thread. Releasing writer
capacity lets a zombie overlap a new writer. Capacity follows physical
execution until termination is proved or the owner process is replaced.

### AST checking as sandboxing

The strategy checker is good admission lint, but allowed Python modules execute
with server-process authority. Untrusted or AI-generated code requires a
credential-free, resource-bounded, restricted worker process.

### Mutable dependency installation in the live runtime

Plugin endpoints can run npm/pip installs into the active environment without
lock/hash/signature/isolation. Extensions should resolve into immutable signed
artifacts or isolated environments, never mutate the core runtime in place.

### Build-and-publish without verification chain

TickFlow's release workflows build useful artifacts but do not gate on backend
tests, lint, frontend tests, security scan, installed smoke, signatures, or
checksums. Aletheia should preserve fail-closed locked CI and bind release
publication to exact verified artifact digests.

### Program-adjacent mutable data on macOS

Putting frozen data beside `sys.executable` stores macOS data inside the
replaceable `.app` bundle. Platform application-data roots and verified backups
are the correct cross-platform invariant.

### Technology-first architecture expansion

TickFlow benefits from DuckDB, Parquet, Polars, and matrices because its primary
workload is wide analytical scanning. Aletheia should not add a data lake,
Postgres, Redis, generic plugin platform, or universal provider framework
without a measured Alpha-relevant bottleneck.

## Proven Aletheia Risks Surfaced by the Comparison

The reference study also exposed current Aletheia issues. These are static
source findings, not runtime observations:

- the first Live unlock UI request cannot satisfy the backend acknowledgement
  contract;
- structured API status/code information can be flattened into messages;
- critical frontend/backend safety strings and types have multiple authorities;
- the remote-control browser token is held in localStorage;
- query identity and realtime invalidation registries can drift;
- realtime degradation and malformed frames are insufficiently visible;
- scalar `MAX(timestamp)` fingerprints can miss corrections that do not change
  the maximum;
- broad restart invalidation and event fan-out can amplify refetch load;
- prolonged chart-tail failures can leave old data without clear degradation;
- some chart lifecycle dependencies and container resize behavior are
  incomplete;
- generic scheduled jobs lack one durable per-attempt lifecycle record;
- not every secondary SQLite-to-file mirror has durable outbox semantics;
- Aletheia's encrypted secret fallback stores the key beside the ciphertext;
- tokenless private-network Remote Control remains an explicit convenience
  mode;
- current PR CI does not itself prove every final platform artifact was built,
  signed, installed, upgraded, and rolled back.

These findings should not be mixed into an unbounded refactor. They belong in
the owning safety, frontend, runtime, data, or release boundary if and when
implementation is authorized.

## The Combined Target by Layer

### Presentation layer

- semantic design tokens and typed behavioral primitives;
- workflow-oriented route manifest;
- eager shell, lazy/recoverable feature pages;
- consistent state family: loading, empty, partial, stale, unavailable,
  permission-limited, degraded, failed;
- retained operator viewport and accessible dense interactions.

### Client data layer

- one bounded abort-aware transport helper;
- small typed domain clients and critical runtime decoders;
- typed query keys and source-to-read-model dependency graph;
- one owner for fallback polls;
- push invalidation plus authoritative reads;
- visible transport, backend, freshness, and source states.

### API/service layer

- stable error envelope with code/status/retry/correlation;
- capability/configuration/permission/health kept distinct;
- typed runtime context and narrow service dependencies;
- explicit lifecycle manifest and owned tasks;
- liveness/interface/data/operation/business readiness vocabulary;
- credential registry and outbound destination policy.

### Data layer

- SQLite remains the only authoritative trading/business store;
- exact identity and short database-only transactions;
- definitive writer outcome and durable outbox for required mirrors;
- typed provider result and completeness envelopes;
- one-way reconstructible analytical projections;
- manifest-led, byte-bounded derived caches;
- source/as-of/observed/published identities retained end to end.

### Runtime layer

- workload-specific bounded lanes;
- durable attempt identity and restart reconciliation where mutation matters;
- progress separate from terminal/publication truth;
- physical cancellation semantics;
- committed change sequence, per-client bounded delivery, and replay/cursor where
  consequences demand it;
- bounded resource and degradation telemetry.

### Assurance/release layer

- locked and pinned verification chain;
- critical schema/contract, secret, architecture, and equivalence tests;
- checksummed migrations and verified backups;
- immutable signed manifests/update feed;
- final-artifact install/start/upgrade/rollback evidence;
- platform signatures, SBOM/provenance, and exact version identity.

## Expected-Value Judgment

The highest expected-value lessons are not the largest architectural changes.
They are narrow patterns that improve truth and operator speed:

1. stable error/capability/outcome envelopes;
2. query-key and change dependency centralization;
3. visible transport/data freshness and partial scope;
4. dataset coverage and run progress stored as backend facts;
5. optimized-path semantic equivalence tests;
6. final-artifact property assertions;
7. one credential classification contract;
8. one publication/completeness contract for analytical data.

Large changes—columnar mirror, dense matrices, custom strategy sandbox,
multi-platform packaged release expansion—are valuable only behind measured
workloads and bounded experiments.

## Final Conclusion

TickFlow is not simply “better UI” or “better database I/O.” Its real strength
is that it designs around an analytical operator: it precomputes the right
representations, communicates stages and coverage, and makes a sophisticated
local product approachable.

Aletheia is not behind because it lacks the same storage stack or visual
density. It solves the harder authority problem: what may act, what state is
canonical, what evidence survives, and how a bad release is rejected or rolled
back.

The best architecture combines both:

> TickFlow's clarity and analytical efficiency on top of Aletheia's semantic,
> transactional, safety, and recovery authority.
