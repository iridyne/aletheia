# Priority and Bounded Experiments

## Purpose

This report turns the twelve-domain study into an evidence-driven sequence of
small investigations. It is not an implementation roadmap and does not approve
any production change. Each experiment is designed to answer one narrow
question before architecture is expanded.

The ordering follows Aletheia's governing priority:

```text
State consistency
  > Safety
  > Auditability
  > Semantic honesty
  > Tests
  > Capability
  > Agent autonomy
```

Within that boundary, higher priority goes to work that either closes a proven
truth gap or cheaply improves the operator's ability to see the real state.

## Decision Dimensions

| Dimension | Question |
|---|---|
| Authority risk | Can the change alter orders, risk, balances, or canonical history? |
| Truth gain | Does it distinguish current, stale, partial, degraded, or unavailable facts? |
| Alpha leverage | Does it reduce the cost or latency of a real research/decision workload? |
| Reversibility | Can the change be removed without data migration or compatibility debt? |
| Evidence cost | Can the hypothesis be answered with a fixture, trace, or focused benchmark? |
| Scope pressure | Does it imply new infrastructure, generic frameworks, or broad rewrites? |

The best early candidates have high truth gain, low authority risk, high
reversibility, and bounded evidence cost.

## Priority Bands

### P0: Protect Existing Authority and Correct Proven Truth Gaps

P0 contains issues discovered while comparing the systems that can misstate or
weaken an existing Aletheia contract. These are not TickFlow feature imports.

| Area | Research conclusion | Appropriate next evidence |
|---|---|---|
| Live unlock acknowledgement | client and backend confirmation semantics can diverge | trace the exact request/response contract and all callers against one fixture |
| API error identity | some client paths flatten distinct backend errors | inventory safety-relevant codes and observe what survives transport and rendering |
| Contract authority | Python response models, handwritten TS, and runtime assumptions can drift | select one critical endpoint and compare producer schema, wire sample, and consumer decode |
| Realtime reconciliation | timestamps can miss corrections or same-time commits | construct a correction fixture and compare timestamp fingerprint with committed sequence |
| Credential boundaries | encrypted values may have adjacent key material; remote mode can be tokenless | document threat model, storage locations, bootstrap path, and explicit operator promises |
| Release proof | source checks do not alone prove the shipped platform artifact | enumerate the properties that must be tested on the exact release digest |

Any P0 implementation still requires the normal safety review for the boundary
being changed.

### P1: Make Existing Truth More Visible and Less Expensive

P1 adopts the most reusable TickFlow practices without changing canonical
authority.

| Candidate | Expected value | Primary risk |
|---|---|---|
| semantic UI state family | makes partial/stale/degraded/unavailable visible | components may invent state not present in backend facts |
| typed query-key registry | reduces invalidation drift and duplicate identities | a broad migration could obscure working paths |
| visible realtime state | separates transport condition from data freshness | connected may be misread as healthy/current |
| route metadata manifest | aligns route, navigation, title, and recovery | accidental eager page imports can erase lazy-loading gain |
| chart lifecycle and resize contract | preserves viewport and fixes container-driven layout | shared abstraction can become vendor-specific or overgeneralized |
| bounded request/operation telemetry | makes latency and failure diagnosable | labels or payloads may leak secrets or explode cardinality |
| release artifact manifest | connects source, dependencies, binary, and smoke evidence | manifest can become decorative unless verified |

### P2: Improve Runtime Ownership and Publication Semantics

P2 changes internal protocols but should preserve observable business meaning.

| Candidate | Expected value | Required invariant |
|---|---|---|
| committed change sequence | reliable catch-up and correction detection | sequence is published only after authoritative commit |
| durable attempt vocabulary | common pending/running/cancelling/terminal truth | status never releases physical capacity before work stops |
| lifecycle ownership manifest | explicit startup, readiness, shutdown, and role gates | authority services still fail closed |
| dataset completeness envelope | prevents partial acquisition appearing complete | replacement requires validated requested coverage |
| generation manifest publication | coherent multi-part analytical snapshot | pointer changes last and readers stay on prior valid generation |
| provider capability envelope | separates configured, entitled, healthy, and permitted | descriptive capability never grants execution authority |

### P3: Add an Analytical Plane Only for a Measured Workload

P3 is conditional. Parquet, DuckDB, Polars, or matrix caches are not goals by
themselves.

Promotion into this band requires evidence of all of the following:

- a named Alpha or research workload with repeatable input and output;
- a measured SQLite/read-model bottleneck or unacceptable repeated CPU/memory
  cost;
- an exact export watermark from committed canonical facts;
- a one-way, reconstructible representation;
- version, coverage, provenance, and completeness metadata;
- equivalence checks against the authoritative result;
- bounded memory/disk ownership and supervised rebuild behavior.

### Deferred or Rejected

The study does not justify generic Postgres/Redis/vector infrastructure, a
plugin marketplace, broad frontend framework replacement, universal ECharts
migration, canonical Parquet trading state, live-runtime pip/npm mutation, or
AI strategy execution in the server process.

## Experiment Card Template

Each experiment should produce an evidence note, not merely a code branch.

| Field | Required content |
|---|---|
| Question | one falsifiable question |
| Boundary | exact subsystem and invariant that remains unchanged |
| Fixture/workload | repeatable input with sensitive values removed |
| Baseline | current latency, state transitions, output, or failure behavior |
| Candidate | smallest mechanism needed to test the hypothesis |
| Observations | timings, cardinality, memory, events, semantic outcomes |
| Acceptance evidence | what would support adoption |
| Rejection evidence | what would stop or defer adoption |
| Cleanup | how experimental artifacts are removed or quarantined |
| Decision | adopt, adapt, preserve, defer, investigate, or reject |

## Experiment 1: Critical API Contract Slice

### Question

Can one safety-relevant endpoint carry the same typed success, typed failure,
confirmation, and correlation semantics from Python producer to rendered UI?

### Boundary

No endpoint behavior, authorization rule, or trading authority changes. The
experiment only maps and validates an existing contract.

### Method

Choose one endpoint with a proven cross-layer mismatch, such as a guarded live
operation. Capture:

- backend request and response models;
- validation and domain error codes;
- sanitization behavior;
- actual wire fixtures for success and representative failures;
- frontend request type, runtime decode, mutation handling, and rendered state;
- confirmation identity and audit/correlation identity.

### Evidence that supports adoption

One narrow schema/code path eliminates handwritten divergence without erasing
safe validation detail or turning backend authority into a frontend boolean.

### Stop conditions

Stop if generation requires exposing internal exception data, creates a second
schema authority, or broadens into an all-endpoint rewrite.

## Experiment 2: Typed Query and Change Graph

### Question

Can a small typed registry make realtime and mutation invalidation complete,
reviewable, and cheaper for one bounded domain?

### Boundary

REST remains authoritative. No websocket payload becomes the business state.

### Method

For one domain, enumerate:

- query-key families and parameter identity;
- mutations and committed backend events;
- the read models affected by each change;
- current invalidation/refetch count;
- duplicate observers, polling owners, and hidden-tab behavior;
- the visible state when refetch fails after notification.

Compare the current trace with a declarative change-to-query map. Include an
unknown-event path and a reconnect/catch-up path.

### Evidence that supports adoption

The graph detects missing edges, reduces redundant refetches, preserves exact
REST reconciliation, and makes degraded state visible.

### Stop conditions

Stop if the registry needs to encode business authorization, if whole-cache
invalidation remains the only reliable result, or if identity cannot be kept
stable across parameters.

## Experiment 3: Semantic State Family

### Question

Can one operator workflow distinguish all materially different data states
without increasing visual noise?

### Boundary

The UI may render only facts the backend already supplies or facts derived by a
documented client policy such as request age. It may not invent health.

### Candidate state vocabulary

```text
initial loading
background refreshing
ready/current
ready/stale
empty/valid
partial/truncated
unavailable/not configured
permission limited
transport degraded
failed/retryable
failed/terminal
```

### Evidence that supports adoption

Representative fixtures produce distinct, accessible states; the operator can
tell whether action is possible; and a stale prior value is not mistaken for a
fresh result.

### Stop conditions

Stop if multiple labels collapse to identical operator consequences or if the
backend cannot supply the fact needed to support the distinction.

## Experiment 4: Committed Change Sequence

### Question

Does an ordered committed identity close correction and reconnect gaps that a
`MAX(timestamp)` fingerprint cannot detect?

### Boundary

The sequence identifies committed change; it does not replace domain identity,
event time, or the canonical record.

### Fixtures

- insert a newer record;
- correct a record without changing its event timestamp;
- commit two different changes at the same timestamp;
- disconnect, commit several changes, then reconnect;
- receive duplicate and out-of-order notifications;
- fail publication after business commit and recover through durable evidence.

### Evidence that supports adoption

The client can detect a gap, refetch exact authority, and advance its observed
watermark only after reconciliation. Duplicate delivery remains harmless.

### Stop conditions

Stop if the sequence is emitted before commit, resets ambiguously across
restart, or becomes another business authority.

## Experiment 5: Dataset Publication Fixture

### Question

Can a small multi-part derived dataset be published so readers observe either
the old complete generation or the new complete generation, never a mixture?

### Boundary

The dataset is reconstructible and non-authoritative. Canonical SQLite state is
read-only input to the experiment.

### Method

Build a fixture with several partitions and inject failure after each stage:

```text
read exact canonical watermark
  -> write generation-scoped temporary partitions
  -> validate schema, counts, ranges, hashes, and completeness
  -> fsync according to authority class
  -> publish immutable generation manifest
  -> atomically advance current-generation pointer
  -> notify readers
```

Run readers concurrently during publication and after simulated restart.

### Evidence that supports adoption

No reader sees mixed generations; failed publication leaves the prior pointer
valid; incomplete scope is represented explicitly; and the new generation can
be reconstructed from the recorded watermark and versions.

### Stop conditions

Stop if recovery relies on file modification time, if a non-empty subset can
advance the pointer, or if the derived store accepts reverse writes.

## Experiment 6: Named Analytical Workload Benchmark

### Question

For one real research workload, does a purpose-built representation create
enough value to justify its lifecycle and consistency cost?

### Boundary

The benchmark cannot move orders, positions, risk, settlement, outcomes, or
audit authority out of SQLite.

### Candidate comparison

Compare the current authoritative/read-model path with only the representations
suited to the workload:

- projected SQLite query;
- Polars in-memory transformation;
- partition-pruned Parquet scan;
- DuckDB ad hoc view over Parquet;
- versioned byte-bounded matrix cache for repeated dense work.

Record cold/warm latency, peak RSS, bytes scanned, serialized result size,
cache identity, build cost, concurrent identical request behavior, and output
equivalence.

### Evidence that supports adoption

The candidate materially improves the named workload, preserves exact output
semantics, has a bounded resource owner, and can be rebuilt without operator
guesswork.

### Stop conditions

Stop if the baseline already meets the workload, if improvement depends on an
unbounded cache, or if provenance/completeness cannot be attached to the
result.

## Experiment 7: Background Attempt and Cancellation Truth

### Question

Can one memory-heavy analytical job expose truthful progress, survive client
disconnect, and prove when capacity is safe to reuse?

### Boundary

This is not authorization for a generic scheduler. It targets one named job
whose failure semantics are understood.

### State model under study

```text
reserved -> pending -> running
running -> cancellation_requested -> stopping -> cancelled
running -> succeeded | partial | failed | timed_out
timed_out -> still_executing | termination_proved
```

Progress may be compacted, but terminal outcome, stage failure, publication
identity, and physical worker ownership remain durable.

### Evidence that supports adoption

Reconnect reconstructs the attempt; cancellation distinguishes request from
termination; a timeout cannot create overlapping writers; and restart
reconciliation classifies abandoned work without inventing success.

### Stop conditions

Stop if status lives only in process memory, if a database flag is treated as
process termination, or if the implementation cannot name who owns cleanup.

## Experiment 8: Provider Result and Capability Envelope

### Question

Can one observational provider path report exact identity, limits, provenance,
and partial scope without changing provider selection policy elsewhere?

### Boundary

Capability is descriptive. It never grants execution permission or permits
silent venue/market/timeframe substitution.

### Envelope under study

```text
requested identity and scope
actual provider, venue, instrument, market type, resolution
configured, entitled, healthy, permitted
request/page/rate/history budgets
received, rejected, missing, and failed scope
complete, partial, empty, failed
source time, event time, ingest time, freshness
last success and capability observation age
```

### Evidence that supports adoption

The caller can make a consumer-specific fallback decision without parsing log
messages, and persisted/returned facts retain source identity.

### Stop conditions

Stop if unknown required values are converted to null-and-continue, if partial
data can replace full coverage, or if capability facts bypass safety policy.

## Experiment 9: Final Artifact Property Verification

### Question

Does the exact distributable artifact contain and start with every property the
source tree assumes?

### Boundary

This experiment observes and verifies packaging; it does not publish or update
a user installation.

### Property classes

- source commit and immutable dependency/tool identities;
- native host and dashboard assets;
- trusted update key and release manifest;
- SQLite, cryptographic, numerical, and platform-specific runtimes;
- secret and development-artifact exclusion;
- clean isolated install and first start;
- liveness, readiness, business health, and orderly shutdown;
- supported-schema upgrade preflight;
- verified backup restoration and failed-activation rollback.

### Evidence that supports adoption

The tested digest is the published digest, properties are checked against the
final platform package rather than the build directory, and failures block
promotion.

### Stop conditions

Stop if checks run on a different artifact, if dependencies can drift after the
check, or if startup success is treated as business readiness.

## Experiment 10: Safe Extension Boundary

### Question

Can an experimental analytical extension run without inheriting server
credentials, filesystem reach, or trading authority?

### Boundary

The experiment does not create a public plugin system. It tests the minimum
isolation contract for one controlled extension.

### Properties under study

- immutable, locked, reviewed artifact identity;
- separate worker process and restricted working directory;
- no exchange credentials or backend service locator;
- explicit input/output schema and maximum payload size;
- CPU, memory, wall-clock, and child-process limits;
- network denied by default or destination allowlist;
- cancellation and forced termination ownership;
- result quarantine and validation before publication;
- audit record connecting requester, artifact, input, run, and outcome.

### Evidence that supports adoption

An intentionally hostile fixture cannot read credentials, mutate canonical
state, escape the network/file policy, or remain alive after forced
termination.

### Stop conditions

Stop if AST inspection is the only boundary, if pip/npm mutates the live
runtime, or if a worker can call the Execution Gateway.

## Evidence Ledger for Any Future Adoption

For every practice promoted from research into implementation, preserve four
distinct evidence classes:

| Evidence class | What it proves | What it does not prove |
|---|---|---|
| static/source | intended contract and reachable code paths | actual runtime behavior |
| deterministic fixture | behavior for controlled cases | production scale or platform packaging |
| runtime observation | behavior of a named process/build/environment | all releases and failure modes |
| final artifact/recovery | shipped identity, install, activation, rollback | business profitability or future compatibility |

No green result should be promoted beyond the boundary it actually observed.

## Recommended Sequencing Logic

The useful order is causal rather than cosmetic:

```text
repair proven contract/truth gaps
  -> make existing state visible
  -> centralize identities and change routing
  -> prove publication and work ownership
  -> benchmark a named analytical bottleneck
  -> add only the representation that wins
  -> verify the final artifact and recovery path
```

This ordering captures TickFlow's best ergonomics and analytical practices
without letting optional infrastructure outrun Aletheia's evidence.
