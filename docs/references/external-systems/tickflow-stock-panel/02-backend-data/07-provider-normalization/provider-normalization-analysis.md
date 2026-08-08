# TickFlow Provider and Normalization Analysis

## Scope

The useful question is whether a consumer can know what data it received—not
merely whether a provider call returned a DataFrame. This requires tracing
capability, requested dataset, actual source, identity, units, time semantics,
coverage, row rejection, partial failure, and fallback.

## Capability Discovery

TickFlow treats provider entitlement as runtime data. `detect_capabilities()`
probes representative endpoints, retries errors classified as transient, records
explicit denial separately, classifies a tier, applies its RPM/batch/subscription
limits, and persists a versioned snapshot.

The anti-downgrade behavior is thoughtful. If a tier-dividing capability such as
single-symbol daily data or adjustment factors fails transiently and the prior
snapshot was paid, the detector retains the prior snapshot instead of converting
a network wobble into a false entitlement downgrade. Unknown cache schema
versions are discarded and re-probed.

Capability and plan are not treated as identical. The plan label is derived from
the capabilities actually observed, can report missing and extra abilities, and
business code uses `CapabilitySet` rather than reading tier YAML directly.

There are limits:

- WebSocket availability is inferred from other Expert abilities rather than
  connected and verified.
- Classification uses exception class, status, and message fragments; an
  upstream exception that changes shape can be misclassified.
- A general detector exception persists a none baseline, while only selected
  transient divider failures retain paid state.
- A custom minute dataset grants the generic minute-batch capability without
  carrying that provider's configured batch/RPM limits into `CapabilitySet`.
- the snapshot says an ability is present, but does not include probe time,
  last-success age, provider identity per ability, or current health.

The durable lesson is to separate “supported,” “entitled,” “configured,” and
“healthy.” TickFlow is substantially along that path, but its `CapabilitySet`
still combines several axes.

## Provider Abstraction: Valuable but Incomplete

The nominal `MarketDataProvider` protocol is a useful shape: callers can request
instruments, daily bars, adjustment factors, minute bars, and realtime quotes
against stock/index/ETF asset types.

The application has not completed the migration to that boundary:

- the built-in registry contains only `TickFlowProvider`;
- custom providers live in a separate loader/registry;
- `kline_sync`, instrument/index sync, and financial sync often call the
  TickFlow SDK directly;
- `TickFlowProvider.get_minute()` returns an empty frame and explicitly leaves
  real minute retrieval in `kline_sync`;
- `ProviderCapabilities.financial` exists, but the protocol does not declare
  `get_financials()`;
- some custom plugins may expose instruments, while the generic HTTP provider
  does not implement the protocol's instrument method.

This is an adapter layer under construction, not yet the single data boundary.
Adding another provider still requires checking service-specific branches and
preferences, not only implementing one protocol.

## Declarative Custom HTTP Sources

This is one of the reference application's most reusable ideas. A YAML source
can declare:

- endpoint and GET/POST method;
- authentication placement through an environment-variable name;
- batch and requests-per-minute limits;
- bounded timeout;
- response dot path;
- source-to-canonical field map;
- fixed query/body values and parameter names;
- a small transform set for scale and date/datetime parsing.

The loader validates that every configured dataset maps required canonical
field names and that request parameter names do not collide. The mapper selects
only declared fields. Transforms are allowlisted and do not evaluate arbitrary
expressions. Temporary configurations can be validated/tested without first
registering them.

These choices make a narrow class of provider integration possible without new
Python code. The limitations define the safe operating envelope:

- validation proves mapping declarations, not that live values are present or
  meaningful;
- there is no retry/backoff, response-size bound, pagination contract, circuit
  breaker, idempotency token, content-type check, or per-request health record;
- missing auth logs a warning and still attempts the request;
- financial data requires only `symbol`, leaving units, currency, period type,
  consolidation scope, restatements, and announcement/as-of semantics open;
- source provenance is not added by the active daily/adjustment normalizers;
- a synchronous `httpx.Client` and `time.sleep` pacing make each source a
  blocking workflow;
- dataset previews show mapped rows but do not run the full daily/minute
  normalizer or publish rejected-row counts.

The approach is appropriate for owner-configured analytical sources when their
outputs remain observational until quality is established. It is not enough for
transactional or execution authority.

## Canonical Schemas and Drift

TickFlow currently has multiple competing schema declarations.

The active daily normalizer keeps:

```text
symbol, date, open, high, low, close, volume, amount, quote_ts
```

`schemas.py` declares daily columns including `asset_type`, `source`,
`pre_close`, and `change_pct`, but excludes `quote_ts`. The active instrument
normalizer keeps `code` and omits the declared `list_date` and `status` fields.
Adjustment and minute declarations similarly include provenance fields that the
normalizers do not produce.

The `source` parameter to `normalize_daily()` and
`normalize_adj_factors()` is currently unused. That means a custom provider can
be selected, yet the normalized frame itself does not carry a source fact.

This is more than documentation drift. A downstream union can accept columns by
name while losing the ability to explain which source, adjustment convention,
or asset type produced a row. Domain 8 must determine what the stored Parquet
actually contains; the provider layer alone does not enforce the broader
declaration.

## Type Conversion and Row Quality

The normalizers handle common response variation efficiently:

- pandas, Polars, dictionaries, and row lists become Polars frames;
- alternative column names are renamed;
- dates, timestamps, and numeric fields are cast;
- known halt-day rows are filtered;
- adjustment rows require all three canonical fields and drop nulls;
- instruments are deduplicated by symbol.

Most conversions use `strict=False`. Invalid text becomes null instead of
raising, but daily/minute rows are not then required to have every identity,
time, and OHLCV field. The layer does not publish input, accepted, rejected, and
reason counts. It also does not visibly enforce:

- finite numeric values;
- positive price constraints;
- `low <= open/close <= high`;
- nonnegative volume/amount;
- monotonic or unique timestamps;
- requested symbol/date coverage;
- timezone/trading-calendar alignment;
- adjustment convention and unit;
- source timestamp versus local observation time.

The result favors ingestion continuity over quality evidence. That can be
reasonable for exploratory analytics, but null-casting must not become silent
truth for ranking or decision inputs.

## Batch and Rate Limits

The active services resolve batch/RPM limits from `CapabilitySet`, reduce RPM to
80% of the advertised budget, split symbols, and pace requests through a
process-local shared next-slot table. Comments honestly state the boundaries:

- the budget is not shared across processes or products;
- concurrent callers' first batches reserve but do not wait and can burst;
- large operations should be scheduled apart;
- limits are grouped by RPM value rather than account/provider/capability key.

A separate async token-bucket Scheduler is implemented but unused. Its presence
should not be mistaken for current enforcement. The actual safety boundary is
the synchronous shared-slot helper plus SDK-internal retry behavior.

The segmented minute path is valuable. It splits time into bounded windows and
can pass each segment to a persistence callback instead of holding an entire
multi-year, all-symbol result in memory. This is a real engineering response to
an observed OOM class, independent of provider brand.

## Fallback Semantics

Fallback is dataset- and path-specific rather than one universal policy.

For daily and adjustment data, selecting a custom source that does not declare
the dataset can fall back to TickFlow. For minute data, resolver and call errors
also fall back; an empty but successful custom result does not. Financial custom
failure becomes an empty result rather than mixing in TickFlow. Instrument sync
can revert to TickFlow when a custom instrument method fails.

This inconsistency contains an important insight: an empty response is not the
same as provider failure, and silently mixing sources can be worse than missing
data. But the policy should be explicit for every dataset and consumer. Current
paths do not return a common outcome containing actual provider, fallback reason,
coverage, and whether semantic equivalence was verified.

Minute fallback is especially consequential. A selected custom provider error
can fall through to TickFlow even when the original capability was granted only
because the custom source existed. The TickFlow attempt may then return no data
or use a different adjustment/coverage convention. Logs carry the reason, but
the returned frame does not.

## Partial Failure and Completeness

TickFlow daily batch sync continues after failed chunks and can return failed
symbols to its caller. Existing data for those symbols is intended to remain.
This is better than failing an entire market refresh or presenting partial work
as complete without a warning.

The pattern is not consistent across datasets:

- instrument retrieval catches exchange failures, combines the remaining
  exchanges, and can hand a partial universe to a full-file overwrite;
- index/ETF sources combine free and paid supplement data but do not attach
  per-exchange completeness;
- financial batch errors are logged and omitted; the partial frame can overwrite
  the full table;
- financial `last_sync` can be recorded after a zero/partial result;
- custom provider batch loops generally let the first exception abort the whole
  method and do not report successful/failed chunks structurally.

The financial path is particularly dangerous as a semantic boundary. “Latest
metrics for every requested symbol” and “whatever batches happened to return”
are not the same dataset. Domain 9 must examine the replacement window, but the
provider contract already lacks the completeness proof required to permit a
full replace.

## Financial Semantics

TickFlow distinguishes current financial statements from share history: metrics,
income, balance sheet, and cash flow request latest records, while the shares
table performs an initial full-history fetch and later merges history for new
symbols with latest rows for existing symbols. Deduplication uses
`(symbol, period_end)`.

That is a useful lifecycle distinction. The custom financial adapter weakens it
because only `symbol` is mandatory and table-specific schemas are unconstrained.
Without period end, report date, currency, scale, statement type, restatement
identity, and source provenance, two providers cannot safely be treated as
equivalent simply because both return a DataFrame.

## Valuable Practices

### Probe capabilities and preserve transiently uncertain paid state

Entitlement should be observed and versioned, not inferred only from a plan
name. Explicit denial and transient unavailability need different recovery.

### Declare owner-configured HTTP mappings safely

A small no-eval transform language and required-field mapping validation can
cover useful analytical sources with bounded complexity.

### Make partial batch failure visible

Continuing successful chunks is valuable when the result includes failed scope
and downstream storage cannot mistake it for full completeness.

### Segment large history by time and stream it onward

Bound memory by one meaningful window and preserve progress/coverage facts.

### Treat provider selection per dataset

Daily, minute, adjustment, realtime, and financial sources can have different
authority and economics. One global “provider” setting is too coarse.

## Cross-Domain Risks Preserved for Later

- partial instrument and financial frames handed to full-file replacement;
- source/provenance fields missing before Parquet union;
- cache/view refresh after partial writes;
- cross-process/account rate limits;
- background custom-provider client cleanup and credential lookup;
- schema evolution across stored partitions.

These are carried into Domains 8, 9, 11, and 12 rather than treated as solved.

## Evidence Boundary

No live provider shape, rate limit, entitlement, empty response, partial failure,
or data value was observed. The analysis proves current code paths and missing
source-level guarantees only.
