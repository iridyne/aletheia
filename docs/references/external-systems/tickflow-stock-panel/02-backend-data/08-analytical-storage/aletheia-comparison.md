# Aletheia Comparison: Analytical Storage and Read Models

## 1. Different Workloads Explain Much of the Difference

TickFlow targets an A-share-scale cross section: thousands of stocks plus
indices and ETFs, long daily histories, minute confirmation, rich screeners,
and parameter-heavy backtests. Its code comments describe roughly 1.0-1.3
million enriched rows and a latest cross section around 5,500 rows.

Aletheia currently focuses on a much smaller crypto trading universe but stores
a much richer causal and operational history: opportunities, debates, risk
decisions, orders, fills, positions, outcomes, exposures, feature snapshots,
collector health, runtime sessions, and learning evidence.

This means TickFlow's columnar sophistication and Aletheia's transactional rigor
are responses to different dominant costs. A useful comparison must not rank
technologies without ranking the facts they protect.

## 2. Current Authority Models

### TickFlow

- Parquet is the durable analytical base for market/instrument/financial data.
- DuckDB is an in-memory SQL facade.
- Polars frames are hot read models.
- matrices and panels are replaceable caches.
- some user/results state is file-based and needs Domain 9 scrutiny.

### Aletheia

- SQLite is the durable authority for market history and business evidence.
- repository methods define explicit transactional write patterns.
- Python objects and a small TTL cache accelerate current reads.
- feature and context snapshots are durable semantic evidence.
- there is no current general Parquet/DuckDB analytical plane or backtest matrix
  architecture in the inspected main package.

Aletheia's model is simpler and safer for the present data volume. It becomes
less efficient when a reconstructible market-history scan or feature expansion
is repeated across many strategies or experiments.

## 3. What Aletheia Already Does Better

### Exact identity in the persisted key

`market_ohlcv` keys each bar by exchange, canonical symbol, market type,
timeframe, and timestamp. Legacy ambiguity is migrated to `legacy_unknown`
rather than relabelled as a real venue.

TickFlow's physical datasets commonly retain symbol/date but its unified views
inject a source constant. Aletheia's explicit source identity is more honest and
must survive any future analytical export.

### Closed-bar admission

The CCXT path marks the currently forming candle and persists only confirmed
bars. This protects historical indicator and research inputs from later-mutating
bar values.

Any Parquet read model should be built from the same closed-bar authority. A
fast exporter must not bypass that invariant.

### Transactional and causal evidence

Aletheia's analytics consume normalized business records whose meaning depends
on transaction boundaries and stable identity. An opportunity, risk approval,
execution, fill, settlement, and attribution chain cannot be recreated merely
by re-downloading market bars.

Those records belong in SQLite even if a separate columnar projection is later
introduced for offline analysis.

### Durable operational state

OHLCV backfill and collector health retain attempt, failure, retry, coverage,
partial, empty, and timing facts. TickFlow has strong data caches but often lets
read failures collapse to empty frames.

### Feature snapshots as evidence

`UniverseFeatureSnapshot` is not just an optimization. It records what the
scanner observed/computed for an asset at a particular time and supplies a
stable snapshot identity downstream. Change suppression plus a heartbeat limits
growth without converting it into an invisible ephemeral cache.

### Bounded retention and compatibility

Aletheia prunes each exact OHLCV series to a configured rolling window and
supports paginated historical reads. This bounds database growth and maintains a
simple indexed access path.

## 4. Where TickFlow Is More Mature

### Workload-specific read models

Aletheia's OHLCV repository exposes chronological lists. Feature extraction then
fetches several timeframes per asset and calculates indicators in Python.
TickFlow explicitly precomputes latest cross-section, live aggregate, history,
panel, and matrix representations.

For repeated research, Aletheia currently pays more decoding, object allocation,
and indicator computation per consumer.

### Column projection and partition pruning

TickFlow can ask for only symbol/date/OHLCV plus the fields required by a
strategy and can open only relevant date partitions. SQLite indexes bound
Aletheia's row lookup well for one instrument, but a broad multi-instrument,
multi-year feature scan is not its strongest workload.

### Cache compatibility evidence

TickFlow's matrix manifest names source root, coverage, symbols, fields,
instrument fingerprint, loader/profile/source versions, partitions, and array
layout. Aletheia's process-global OHLCV cache uses a request tuple and TTL only.

The latter is adequate for short-lived fetch reuse but cannot prove compatibility
of a derived analytical artifact across code/data revisions.

### Single-flight and byte bounds

TickFlow prevents identical concurrent panel misses from performing duplicate
scans. Its matrix-operation cache has total and per-item byte limits.

Aletheia's OHLCV TTL cache is thread-safe but unbounded by entries/bytes and has
no single-flight. Concurrent cold callers can request the same provider data and
later store duplicate results.

### Sparse minute access

TickFlow's exact-date partition reader is tailored to event-trigger backtests.
Aletheia has bounded timeframe history, but no equivalent consumer-native sparse
minute projection in the inspected code.

### Cache telemetry

TickFlow measures real scan time, hits, follower reuse, evictions, bytes, and
build stages. Aletheia does not expose corresponding OHLCV cache hit/miss/size or
repeated analytical computation cost in the inspected cache module.

## 5. Aletheia's Current Cache Hazards

### Request identity can differ from obtained identity

The CCXT provider tries primary then fallback venue, persists using the actual
successful venue, but writes the in-memory cache under the configured primary
venue. A later primary-identity request may receive fallback observations
without the cache value exposing that substitution.

This is both a Domain 7 provenance flaw and a Domain 8 cache-key flaw. Cache
compatibility must be based on actual result identity or an explicit allowed
substitution envelope.

### TTL is read repeatedly but capacity is not bounded

Each lookup can re-read configuration to determine expiry. Entries are removed
when the exact key is read after expiry, but there is no general sweep or maximum
size. A process that explores many exchange/symbol/timeframe/limit combinations
can retain cold entries indefinitely until those keys are revisited.

### Limit is part of the key without covering reuse

A cached 500-bar response cannot answer a later 100-bar request, even though the
chronological suffix might be compatible. TickFlow's matrix cache explicitly
finds covering coverage/field supersets and projects them.

For the small live universe this may not matter. It is a clear future read-model
opportunity if repeated historical analysis grows.

### No single-flight

The lock protects the dictionary, not the acquisition. Multiple scheduler or API
callers that miss together can perform identical network and persistence work.

### Cache invalidation is test-oriented

The only explicit invalidation clears the whole cache and is described as a test
reset. No source generation or data-publisher event ties the cache to corrected
historical bars.

## 6. Aletheia's Current Analytical Cost Shape

`_fetch_single_feature` obtains 15-minute, 1-hour, and 4-hour data for each asset,
converts row arrays into Python lists, then computes RSI, ATR, Bollinger width,
volume ratios, returns, beta, and several cross-source enrichments. It writes a
semantic snapshot when change/heartbeat rules require it.

That is appropriate for producing a current decision observation. It is less
appropriate as the basis for repeatedly replaying thousands of candidate/time
pairs. The durable snapshot table also should not be repurposed as a dense
feature matrix: its job is causal evidence, and change suppression means it is
not a uniform time series.

The missing concept is a separate, explicitly reconstructible research read
model.

## 7. Recommended Authority Boundary

The most important adoption decision is categorical:

| Data family | Canonical authority | Optional analytical projection | May be evicted/rebuilt |
|---|---|---|---:|
| orders, intents, approvals, fills, positions | SQLite | read-only offline export | no |
| runtime sessions, audit, infra events | SQLite/files under current contract | bounded report projection | no |
| opportunities, debates, exposures, outcomes | SQLite | versioned analytical export | no |
| feature snapshots used by decisions | SQLite | denormalized research projection | no canonical deletion |
| exact closed OHLCV bars | SQLite today | date-partitioned columnar mirror | mirror only |
| reconstructed indicators/features | versioned computation over canonical inputs | Parquet/Arrow matrices | yes |
| backtest panels/matrices | none; derived | disk/memory cache | yes |
| dashboard latest aggregates | none; derived | in-memory read model | yes |

This lets Aletheia borrow TickFlow's analytical efficiency without weakening
the transactional business chain.

## 8. A Minimal Aletheia Read-Model Shape

If profiling later proves a need, a restrained design could contain:

```text
SQLite market_ohlcv authority
  -> one supervised exporter reads an exact committed watermark
  -> date-partitioned closed-OHLCV analytical mirror
  -> manifest: schema, identity, coverage, source watermark, code version
  -> projected reader for research/backtest only
  -> optional matrix cache with byte budget and compatibility manifest
```

It should not introduce Redis, Postgres, another writer authority, or a generic
data lake. It should begin with one expensive proven query, one reconstructible
dataset, and one measured consumer.

## 9. Required Semantic Envelope

Every analytical projection or cache should retain at least:

- exact exchange, symbol, market type, and timeframe;
- requested and actual resolution where fallback/resampling exists;
- provider and physical venue;
- closed/open status and source timestamp;
- canonical database watermark or input range;
- schema version and computation version;
- coverage start/end and accepted/rejected counts;
- build status and completeness;
- whether the artifact is authoritative or reconstructible.

TickFlow's matrix manifest supplies much of the mechanical half. Aletheia needs
to add its stronger provenance and safety identity.

## 10. Staged Readiness for Aletheia

TickFlow's background warm-up is attractive because Aletheia already separates
process roles and readiness gates. A future derived read model should expose
separate facts such as:

```text
canonical_db_ready
market_history_ready
analytical_mirror_status
feature_model_status
backtest_cache_status
```

The API should return an outcome envelope, not an empty list that could mean
warming, failed, missing, or genuinely empty. Trading readiness must not depend
on an optional research cache.

## 11. Generation Versus Watermark

TickFlow uses a UUID generation bumped by managed enriched writes. Aletheia has a
transactional database and therefore can define a stronger source identity:

- highest committed source timestamp is insufficient because older bars can be
  corrected;
- file mtime is not a database commit identity;
- a random generation works only if every write goes through one publisher;
- an explicit export ledger row committed after the source snapshot can name
  schema version, source bounds, row count, and content/partition fingerprints.

The exact mechanism should be designed only when a projection is approved. The
principle is that publication identity comes from the canonical writer, not from
the cache reader noticing files.

## 12. Query Semantics Must Remain Consumer-Specific

TickFlow demonstrates that “history” means different things:

- latest cross section;
- N actual trading sessions plus warm-up;
- calendar coverage interval;
- exact sparse trigger dates;
- one coherent symbol/instrument series;
- whole-universe dense axes.

Aletheia should express those differences in typed query objects or named
repository methods. A generic `get_history(symbol, range)` becomes ambiguous
once exact venue, fallback, closed-bar, warm-up, completeness, and projection
requirements matter.

## 13. What Not to Port

### Do not move canonical trading state to Parquet

Parquet atomic replacement of one file does not provide relational transactions,
foreign-key-like causal integrity, or coordinated multi-record rollback.

### Do not hard-code source in a read view

Preserve the physical provider/venue from the source row. Product ownership and
data provenance are separate dimensions.

### Do not map failure to empty

Research, screening, and decision consumers require different failure policies.
Return availability and completeness explicitly.

### Do not create caches before measuring repeated cost

TickFlow has a workload that justifies million-row warm-up and memory-mapped
matrices. Aletheia should use the telemetry-first lesson, not assume identical
scale.

### Do not use a daemon thread as the ownership model

Aletheia's lifecycle and process-role design is stronger. Derived-model warm-up
should be supervised, cancellable, observable, and prevented from becoming a
second writer in an API-only process.

### Do not let analytical fallback affect execution identity

A research projection may include labelled cross-venue reference data when a
consumer policy permits it. Risk and execution must continue to use exact
venue/market facts through the Execution Gateway.

## 14. Adoption Priority

### High value, low conceptual risk

- cache outcome telemetry;
- single-flight for identical expensive acquisition/scan;
- byte/entry bounds and eviction facts;
- named coverage-aware read models;
- explicit staged readiness;
- manifests for any derived artifact;
- exact-date/scope reads where consumer access is sparse.

### High value, requires evidence first

- columnar OHLCV mirror for research/backtest;
- projected Arrow/Polars scan path;
- disk-backed dense matrix cache;
- background prewarming of a known research profile.

These require profiling, data-volume evidence, a canonical export boundary, and
crash/rebuild semantics.

### Keep current Aletheia design

- SQLite authority for causal, audited, and trading state;
- exact instrument key;
- closed-bar persistence;
- durable feature and context snapshots;
- bounded rolling history and durable coverage repair;
- process-role and lifecycle gates.

## 15. Comparison Conclusion

Aletheia is not behind because it lacks DuckDB. It is missing a distinct
analytical plane for workloads that are reconstructible, wide, and repeatedly
scanned. TickFlow supplies an excellent pattern for that plane: partitioned
columnar facts, named hot views, request-level single-flight, and a manifest-led
matrix cache.

Aletheia supplies the stronger authority model. The best combined architecture
keeps SQLite as the only business truth and permits a one-way, supervised,
versioned projection into disposable analytical representations when measured
Alpha/research workloads justify it.
