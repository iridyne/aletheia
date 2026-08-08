# Domain 8 Lessons and Reflections

## Reuse Directly

### Name read models by consumer workload

Distinguish latest cross section, live aggregate, bounded trading-session
history, exact trigger dates, broad panel, and dense matrix. Make their coverage
and refresh semantics visible in the API.

### Single-flight expensive identical reads

Let one leader perform a provider fetch, file scan, or matrix build while
compatible followers await and reuse the result. Keep unrelated keys concurrent,
propagate the leader's failure, and do not cache failures as data.

### Bound caches by the resource they consume

Use entry/TTL bounds for small request frames and byte plus per-item bounds for
large arrays. Record current/peak size, hits, misses, evictions, skips, build
time, and follower reuse.

### Use monotonic time for TTL

Keep Aletheia's use of monotonic time so wall-clock correction cannot make cache
entries unexpectedly immortal or expired.

### Project columns before materialization

Read only identity, time, OHLCV, and strategy-required feature columns. Apply
date/symbol predicates before collection and use streaming/batched collection
for large ranges.

### Select sparse partitions explicitly

When a consumer knows exact event dates, read those partitions instead of the
entire surrounding interval.

### Use actual session axes

Define lookback in observations/trading sessions when model semantics require
bars, not calendar days. Preserve separate warm-up coverage.

### Manifest every durable derived cache

Record loader/schema/computation version, source identity, coverage, symbols,
fields, instrument fingerprint, build timing, array layout, and completeness.
Make missing or incompatible evidence a cache miss.

### Build then atomically publish replaceable artifacts

Construct a complete temporary file/directory, flush and close handles, write
the manifest last within the temporary artifact, then atomically rename into the
published path. Domain 9 will refine the crash boundary.

### Separate source decoding cache from job computation cache

Cross-request cache compatibility can use scope/coverage/version evidence.
Derived array operations should remain inside a job/matrix identity unless
their compatibility proof is equally strong.

### Detect per-instrument lag

A global latest timestamp can hide instruments whose updates repeatedly fail.
Compute coverage and lag by exact instrument/timeframe.

## Reuse After Adaptation

### A one-way analytical mirror

If profiling demonstrates repeated wide OHLCV scans, project committed,
confirmed SQLite bars to a date-partitioned columnar mirror. The mirror is
research-only, reconstructible, and never an execution or audit authority.

### A typed analytical result

Return more than a DataFrame/list:

```text
status: ready / warming / empty / partial / failed / unavailable
requested and obtained scope
coverage and missing partitions
source identity and watermark
schema and computation versions
row/rejection counts
payload
```

Consumer policy then decides whether partial/unavailable data is displayable,
research-only, or fail-closed.

### Staged readiness

Expose canonical database readiness independently from optional market-history,
feature, and backtest projections. A slow research warm-up must not block safe
gateway readiness, and a missing research cache must not masquerade as data.

### Coverage-aware cache reuse

A larger chronological bar set can answer a compatible suffix request; a wider
field/coverage matrix can answer a narrower projection. Reuse only after exact
identity, close status, source, computation version, and coverage checks.

### Versioned narrow-fact/wide-feature split

Persist stable facts and selected high-value semantic snapshots. Recompute broad
technical features into disposable views, but bind results to input and code
versions so historical research remains reproducible.

### Supervised prewarming

Warm one proven profile after source publication. Use Aletheia's named lifecycle
tasks, cancellation, role ownership, readiness state, and shutdown ordering
instead of an anonymous daemon thread.

### Source generation tied to the publisher

Generation is valuable only when every canonical mutation updates it. Prefer a
publication ledger or manifest committed by the sole exporter, including
coverage and counts, over a reader-created random marker.

### Tolerant physical reads with strict semantic admission

Schema-by-name union can help open historical files, but admit a dataset only
after checking required columns, units, identities, versions, and completeness.
Quarantine incompatible partitions rather than filling required facts with null.

### Read-only memory-mapped matrices

For a measured multi-year vectorized backtest bottleneck, a typed read-only
matrix can eliminate repeated pivots and copies. Keep it reconstructible,
byte-bounded, manifest-led, and outside the live trading authority.

## Aletheia-Specific Design Questions

- Which current Alpha/research query is measurably limited by SQLite scans or
  repeated Python indicator computation?
- Is the desired axis one instrument over time, many instruments at one time,
  or a dense two-dimensional panel?
- Which exact closed-bar database commit/watermark defines an export?
- Can older bars be corrected, and how will that invalidate covered partitions?
- Which provider/venue/timeframe substitutions, if any, are admissible for the
  research consumer?
- Which derived features are reconstructible and which are causal observations
  that must remain durable?
- Which code/config version identifies a feature computation?
- Can the projection be removed and rebuilt without affecting readiness,
  execution, risk, or audit?
- What runtime metrics would prove the cache pays for its complexity?
- Which process owns export, warming, pruning, and recovery?

## Study Further

- Measure top SQLite analytical queries by rows, elapsed time, frequency, and
  writer-lane impact before designing a columnar mirror.
- Trace all callers of `get_ohlcv_candles` and classify exact-scope versus legacy
  partially scoped access.
- Inventory repeated multi-timeframe fetch/computation within one universe scan
  and determine whether single-flight or batch collection removes more cost than
  a new storage layer.
- Establish whether OHLCV corrections are append-only, replacement, or
  backfilled and identify a robust projection invalidation watermark.
- Define a content/version fingerprint for technical indicator code and
  strategy configuration.
- Quantify the process-global OHLCV cache's entry count, retained bytes, hit
  rate, duplicate cold fetches, and fallback-venue contamination.
- Determine whether a cache covering a larger `limit` can safely supply a
  smaller request after checking closed-bar and exact-source semantics.
- Inspect TickFlow's cache-profile generation and prewarm policy alongside job
  scheduling in Domain 11.
- Inspect all TickFlow writers that can mutate enriched partitions and verify
  whether each bumps matrix generation in Domain 9.
- Determine whether matrix publication can leave an orphan temporary artifact
  or a generation/file mismatch after a crash.
- Trace TickFlow backtest result persistence separately from disposable input
  matrices; results and experiment metadata have a different authority class.
- Compare indicator outputs between repository hot-cache recomputation and
  direct matrix derivation to identify potential semantic drift.

## Do Not Reuse

### Hard-coded provenance in unified views

Never synthesize `source='product-name'` when the physical provider may differ.
Keep data producer, normalizer, storage owner, and dataset family separate.

### Empty payload as operational status

Do not use zero rows to represent warming, missing files, permission failure,
corruption, computation failure, or legitimate no matches.

### Parquet as a transactional trading database

Do not move orders, approvals, fills, positions, settlements, causal evidence,
or audit state into replaceable columnar partitions.

### A second writer authority

An analytical mirror must be one-way from committed SQLite truth. No strategy,
UI, or recovery path may write back through it.

### Unbounded process-global caches

Do not let key diversity retain unknown memory indefinitely. TTL without a
capacity bound is not a complete eviction policy.

### Cache keys based only on requested identity

If fallback or resampling occurred, requested venue/timeframe is not sufficient
to identify the value. Include actual identity/provenance or refuse caching for
exact consumers.

### Manual invalidation as the only correctness mechanism

Every new writer will eventually forget one callback. Bind durable derived
artifacts to publisher evidence and make incompatible caches self-reject.

### Random generation created by the reader

A reader-created generation proves only that a marker now exists. It does not
prove how current source files were published or that all expected partitions
are complete.

### `union_by_name` as proof of compatibility

Name alignment cannot validate units, adjustment convention, provider, actual
timeframe, nullability, or computation meaning.

### Broad exception-to-empty fallback in research or trading

Required data corruption or scan failure must remain visible. A “no signal”
result based on unavailable inputs is semantically false.

### Prewarming without lifecycle ownership

Do not start unsupervised daemon work that can outlive readiness transitions or
compete with writer roles. Use explicit task ownership and shutdown.

### Technology-first migration

Do not add DuckDB, Parquet, or a dense matrix merely because TickFlow benefits
from them. Start from a measured Alpha/research bottleneck and preserve the
smallest architecture that removes it.

## Final Reflection

The deepest reusable idea is representation discipline. A single fact can have
several optimized representations, but only one authority; every derivative
must state its source, coverage, version, completeness, and eviction status.

TickFlow demonstrates how far that discipline can improve interactive analysis
and backtests. Aletheia demonstrates why causal trading evidence needs stronger
transactional truth. Combining those strengths means adding disposable speed
around the authority, never replacing the authority with the cache.
