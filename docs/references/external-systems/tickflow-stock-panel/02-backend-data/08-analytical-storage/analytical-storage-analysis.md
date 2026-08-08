# TickFlow Analytical Storage Analysis

## 1. The Important Idea Is a Read-Model Pipeline

TickFlow is not one database with a faster query library. Its code expresses a
sequence of increasingly consumer-specific representations:

```text
provider-normalized frames
  -> date-partitioned Parquet datasets
  -> DuckDB convenience views / Polars lazy scans
  -> latest-day, live-aggregate, and history DataFrames
  -> backtest panel
  -> typed dense matrix
  -> job-scoped derived matrix cache
```

Each step exchanges generality for lower repeated work. Parquet remains the
reconstructible analytical base; the latest frames serve APIs; the dense matrix
serves vectorized simulation. This separation is the strongest architectural
lesson in this domain.

It also prevents an attractive but harmful abstraction: asking one repository
method to serve interactive quotes, a 90-trading-day filter, sparse minute
triggers, and multi-year all-symbol backtests with the same physical query.

## 2. Physical Layout Is Consumer-Aware

TickFlow separates stock, index, ETF, daily, minute, adjustment, instrument,
financial, depth, and enriched datasets into directories. Daily and enriched
market histories are partitioned by date with one `part.parquet` per date.

This layout is well matched to its dominant axes:

- cross-sectional screeners need all symbols for a recent date;
- backtests need a date interval and often the full symbol universe;
- sparse minute confirmation needs a small set of exact trigger dates;
- instrument and share-capital data change at a different cadence;
- raw and enriched histories have different rebuild/version semantics.

Date partitioning avoids one file per symbol and lets the backtest reader bound
work using Hive partition metadata. Exact-date minute loading is particularly
good: an event strategy with ten trigger dates need not scan every minute bar in
a five-year interval.

The layout is not universally optimal. A single-symbol deep chart may touch many
date partitions, and a large number of tiny daily files adds metadata cost.
TickFlow's target universe and cross-sectional workload make the trade-off
reasonable; Aletheia should not copy the directory tree without first naming its
own query shapes.

## 3. DuckDB Is a Query Facade, Not the Authority

`DataStore` opens DuckDB in memory. The durable facts are the files, while views
provide SQL over current Parquet globs. This has useful consequences:

- no second analytical database file must be migrated or repaired;
- column pruning and Parquet predicate pushdown stay available;
- SQL can join or aggregate across datasets without loading whole frames;
- the process can recreate the query layer from files.

It also creates lifecycle obligations. A view may not exist on first boot when
its glob is empty. The code therefore tolerates failed registrations and
re-registers after selected sync operations. Callers cannot safely infer that a
named view always exists merely because the repository object exists.

The single connection is guarded by a lock. That is a legitimate local-desktop
choice, but it means concurrent SQL reads are serialized through one connection.
Polars scan paths provide another lane, yet the application now has two query
engines whose transformation semantics must remain aligned.

## 4. `union_by_name` Is Useful Compatibility, Not Schema Governance

Every registered Parquet view uses `union_by_name=true`. This makes additive
columns and differently ordered files survivable. It is a practical reader-side
compatibility feature during product evolution.

It does not prove that the union is semantically valid. Missing columns become
null, compatible-looking names can carry changed units, and source/as-of fields
can disappear unnoticed. The same mechanism that keeps the UI available can
hide a partial migration.

A robust interpretation is:

```text
union-by-name = tolerant physical reader
schema/version/quality manifest = semantic admission authority
```

TickFlow implements the first strongly. Its backtest matrix adds a schema
validator and cache manifest, but the general repository views do not expose an
equivalent dataset manifest.

## 5. Unified Views Fabricate Provenance

The convenience views add `source='tickflow'` for stock, index, ETF, minute,
enriched, and instrument rows. Domain 7 proves that TickFlow can select a custom
HTTP provider for some datasets. Therefore, the view's source value is not
derived from the physical row or the active provider result.

This is a static semantic defect even if a particular installation happens to
use the default provider. A field named `source` appears row-level and factual,
but it is actually a view label. Downstream consumers cannot distinguish:

- provider that observed the value;
- normalizer implementation;
- local product that owns the read model;
- dataset family.

Those should be separate fields. Aletheia should learn from the unified view
shape but explicitly reject the provenance implementation.

## 6. Narrow Enriched Storage Reduces Persistent Schema Weight

TickFlow stores a relatively narrow enriched row set, then runs indicator and
signal computation when building the hot enriched cache. The result is a useful
split:

- stable market facts and selected derived inputs are durable;
- numerous presentation/strategy indicators remain reconstructible;
- the UI gets a rich one-row-per-instrument frame;
- indicator changes do not require every possible derived column to be kept
  permanently.

This can be superior to an ever-widening table. It only remains reproducible if
the computational version and input coverage are known. A backtest result built
with indicator implementation A can differ after implementation B recomputes
the same narrow files. The matrix cache has loader/profile/source generations,
but static inspection did not establish a universal indicator-code fingerprint
for every enriched cache and persisted result.

The pattern to borrow is narrow durable facts plus versioned derived models, not
unversioned recomputation.

## 7. Hot Caches Match Named Workloads

`KlineRepository` does not maintain one generic dictionary. It names the hot
models:

- latest enriched stock frame;
- precomputed live aggregate frame;
- full enriched history coverage;
- stock/index/ETF instrument frames;
- index and ETF enriched variants;
- historical share-capital frame;
- index/ETF symbol-set memos.

That explicitness makes performance intent readable. An engineer can see why a
roughly 5,500-row frame exists and which event invalidates it.

The live aggregate cross-day guard is a fine example of cost shaping. Every
one-second read performs an inexpensive in-memory date comparison. Only the
first read after the date changes asks DuckDB for the latest persisted date and
rebuilds when its baseline moved.

The history cache understands trading days. It checks broad calendar coverage
including warm-up, then selects the requested number of actual trading dates.
This avoids a subtle divergence where a 90-day live filter sees only roughly 64
trading days while a backtest uses 90 bars.

## 8. Warm-Up Makes Readiness Multi-Dimensional

The expensive enriched computation is moved out of startup's critical path into
a daemon thread. Instrument dimensions become available quickly, while rich
indicators warm in the background. During warm-up, selected reads return an
empty frame rather than triggering a second synchronous computation.

The latency benefit is real, but `empty` now has at least four possible meanings:

- legitimate no rows;
- warming;
- missing files/view;
- read or computation failure.

An upper layer that only tests `is_empty()` cannot make an honest availability
statement. The warm-up callback and `indicators_ready` state partially address
this, but the repository return type still erases the distinction.

The transferable design is staged readiness. The anti-pattern is encoding
readiness as a zero-row data frame.

## 9. Read Errors Frequently Collapse Into Empty Data

Several Polars and DuckDB query helpers catch broad exceptions, log a warning,
and return an empty frame/list. This keeps a desktop UI running when optional
datasets are absent, but it can silently change a backtest universe, skip
signals, or present “no matches” after a corrupt partition.

The semantics should depend on the consumer:

- an optional card may display unavailable;
- a screener should distinguish no matches from incomplete data;
- a backtest must fail or record an explicit incomplete coverage policy;
- a trading decision must fail closed for required inputs.

Availability-oriented fallback at the repository boundary is too early to make
all of those decisions.

## 10. Cache Invalidation Is Layered but Mostly Imperative

The repository invalidates related caches after sync and exposes callbacks that
schedule other warm-ups. Share history watches file mtime. Live aggregate uses a
date/baseline check. The matrix layer uses persistent source generations or
partition fingerprints.

This progression reflects increasing consequence:

| Layer | Current invalidation evidence | Strength |
|---|---|---|
| symbol-set memo | cleared with instrument cache | direct but manual |
| share history | file mtime nanoseconds | cheap, file-specific |
| latest/live frame | refresh callback and date baseline | workload-specific |
| history frame | explicit refresh/coverage checks | manual plus bounds |
| panel cache | TTL, LRU, exact request key | bounded staleness |
| disk matrix | manifest + source generation/partition fingerprints | durable and inspectable |
| derived operation arrays | matrix lineage + job-local byte LRU | deterministic job scope |

The matrix design is the most robust because it turns cache identity into data.
The general repository caches depend more heavily on every writer remembering
to notify every affected layer.

## 11. Generation Is Simple and Coarse

Managed enriched writes bump a UUID stored through temporary-file replacement.
Every generation change invalidates matrices for the asset family, avoiding the
need to hash a large dataset on every request.

Benefits:

- constant-time freshness comparison;
- easy invalidation after known writes;
- generation is durable across restarts;
- a complete old generation can be pruned as a unit.

Limitations:

- an out-of-band write that does not bump generation can leave stale caches;
- creating a generation when the file is absent labels the current files but
  does not prove how they were produced;
- one changed day can invalidate otherwise reusable years of cached coverage;
- generation identifies “changed,” not what changed or whether publication was
  complete.

Generation should therefore be tied to the only permitted dataset publisher
and ideally accompanied by a dataset version/coverage manifest.

## 12. The Backtest Matrix Is a Purpose-Built Data Product

The matrix loader goes well beyond caching a DataFrame. It:

- validates required Parquet columns;
- determines available and requested coverage;
- projects only required strategy fields;
- normalizes the symbol request;
- fingerprints instrument metadata;
- scans Arrow batches into typed vectors and two-dimensional arrays;
- derives tradability and limit-state arrays;
- stores array layout and axes in a versioned manifest;
- memory-maps the binary representation;
- can reuse a covering cache and slice/project it for a smaller request;
- publishes through a temporary directory and atomic rename;
- prunes by byte budget and generation.

This is a strong example of replacing repeated parsing, pivoting, alignment, and
type conversion with a consumer-native representation. It is safe to cache
because it is derived from immutable-enough source partitions and can be
reconstructed.

The manifest is at least as important as `mmap`. Without coverage, symbol axis,
field set, instrument fingerprint, versions, and source evidence, a binary
matrix would be fast but epistemically unsafe.

## 13. Two In-Memory Caches Solve Different Problems

`PanelCache` and `MatrixComputeCache` should not be conflated.

`PanelCache` is cross-request and short-lived. It prevents repeated file scans
for identical date/symbol/column scopes. Its single-flight behavior prevents an
optimizer's concurrent workers from all loading the same panel on a cold miss.

`MatrixComputeCache` is job-scoped through a `ContextVar`. It retains results of
deterministic operations on arrays, tracks lineage, enforces a byte budget, and
does not allow unrelated backtests to accidentally reuse context-sensitive
intermediates.

This is a valuable scoping rule:

```text
cache source decoding across compatible requests;
cache derived computation only inside the identity that proves compatibility.
```

## 14. Concurrency Is Explicit but Local

TickFlow recognizes several concrete races:

- shared DuckDB connection access is locked;
- Parquet read-modify-write is serialized;
- `OrderedDict` mutations are locked;
- panel computation occurs outside the lock;
- identical panel misses are single-flight;
- matrix cache builds use a temporary path and tolerate another builder winning;
- NumPy mappings are flushed and closed before publication.

These practices are strong within one process. A `threading.Lock` does not
coordinate two application processes or an external file writer. The desktop
deployment may make single-process ownership a valid assumption, but it should
be declared as an invariant rather than inferred from the lock.

## 15. Telemetry Is Present Where It Informs Optimization

The panel cache records true scan count/time separately from hits and follower
reuse. Matrix computation records hits, misses, evictions, skipped oversized
items, retained/peak bytes, and fingerprint cost. Matrix manifests record build
stage timings.

This is better than treating a cache as self-evidently beneficial. It enables a
future decision to remove a cache, change a byte budget, prewarm a profile, or
optimize the underlying scan based on measured cost.

Static analysis cannot say whether current hit rates justify the complexity.
The instrumentation makes that answer obtainable at runtime.

## 16. Storage Authority Classification

TickFlow's architecture is easiest to reason about when every artifact is
classified:

| Artifact | Intended authority | Reconstructible | Safe to evict |
|---|---|---:|---:|
| normalized Parquet market facts | analytical base | only from provider/re-sync | no, unless upstream recoverable |
| narrow enriched Parquet | derived analytical base | yes, if raw inputs and version retained | conditionally |
| DuckDB view | query facade | yes | yes |
| latest/live/history DataFrame | hot read model | yes | yes |
| backtest panel | request cache | yes | yes |
| disk matrix | derived cache | yes | yes |
| matrix operation cache | job-local acceleration | yes | yes |
| user pools/results/config | product state | varies | generally no |

The code is strongest for reconstructible market artifacts. User data and
backtest-result publication require separate transactional analysis in Domain 9.

## 17. Overall Assessment

TickFlow is worth learning from because its analytical path is shaped around
actual data volume and consumer access patterns. The strongest parts are:

- date-partitioned, column-projected file access;
- named hot read models;
- trading-day-aware history slicing;
- exact-date minute loading;
- single-flight panel scans;
- job-scoped byte-bounded computation caching;
- a versioned, source-aware, memory-mapped matrix manifest.

Its weaknesses are primarily semantic and lifecycle-related:

- hard-coded row provenance;
- tolerant schema union without a general semantic manifest;
- empty frames standing in for several operational states;
- manual cache coupling;
- coarse generation dependent on managed writers;
- reconstructibility that is incomplete unless indicator versions are retained.

The correct lesson is not “replace SQLite with Parquet.” It is “give large,
reconstructible analytical workloads explicit read models while keeping their
identity, coverage, source, and computation version inspectable.”
