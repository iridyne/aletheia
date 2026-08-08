# Domain 7 Lessons and Reflections

## Reuse Directly

### Versioned runtime capability snapshots

Probe low-cost representative operations, distinguish explicit denial from
transient failure, retain a prior known entitlement under bounded uncertainty,
and invalidate stale snapshot schemas deliberately.

### Dataset-specific provider choice

Daily bars, minute bars, adjustment factors, realtime prices, and financial
statements do not have identical quality, cost, and entitlement boundaries.
Represent their sources separately.

### Safe declarative mapping for observational sources

Allow owner-configured endpoint, response path, field map, fixed parameters,
bounded timeout, and a small no-eval transform set. Validate declarations before
registration and keep outputs observational until quality evidence exists.

### Segmented history acquisition

Split large history by provider-safe temporal windows and stream each segment to
the next stage. Preserve progress and failed scope without retaining the entire
history in memory.

### Exact instrument identity

Retain Aletheia's exchange + canonical symbol + market type identity from request
through cache, persistence, freshness, scoring, and protective action.

### Durable collector outcome taxonomy

Preserve success, partial, empty, failed, timed out, not configured, budget
skipped, and disabled as different facts. Record health only after required
persistence succeeds.

### Bounded coverage repair

Define required bar counts per exact instrument/timeframe, persist repair state,
back off repeated failures, and declare ready only after re-reading coverage.

## Reuse After Adaptation

### Provider descriptor

Add a small descriptive contract separate from execution permission:

```text
provider_id and physical venue
dataset and instrument support
requested and actual resolution
currency, units, and adjustment convention
history/page/batch/RPM limits
configured/entitled/healthy states
last success and current cooldown
```

Start with the existing public market-data chain. Do not attempt a universal
plugin platform.

### Typed provider result envelope

A provider operation should return data plus:

```text
requested_scope
obtained_scope
actual_provider and venue
fallback_reason
complete / partial / empty / failed
accepted and rejected row counts
quality reasons
observed_at and source_as_of
retry guidance
```

The payload type may remain dataset-specific; the outcome facts should be
consistent.

### Semantic fallback policy matrix

Decide allowed substitutions by consumer:

| Consumer | Venue change | Market-type change | Timeframe change | Stale fallback |
|---|---:|---:|---:|---:|
| dashboard reference | possibly, visibly | no | no | bounded, labelled |
| research context | possibly, with provenance | no | only explicit resampling | bounded by domain |
| indicator/scoring | only if model permits | no | no silent change | fail closed |
| protective close | no | no | not applicable | exact bounded snapshot only |

The final policy must be source- and strategy-specific; the table expresses the
questions, not authorization for new fallback.

### Canonical schema registry

Choose one executable schema per dataset. Include identity, source, source time,
actual resolution, units, adjustment convention, and quality metadata. Generate
documentation or checks from it instead of maintaining divergent column lists.

### Reject/quarantine rather than silent null

Type conversion should return accepted rows, rejected rows or bounded samples,
and reason counts. Invalid required identity/time/OHLC fields must not silently
enter decision datasets as null.

### Central limits with local execution adapters

Centralize declared budgets and provider health, but let sync/async adapters
enforce them in their natural concurrency model. Account for process, host/IP,
credential, and upstream-account scope separately.

### Last-known capability with explicit uncertainty

Retaining paid capability after a transient probe failure is useful, but publish
`known_available` plus `probe_degraded` and snapshot age. Do not present stale
entitlement as freshly verified.

### Source-aware candle contract

Extend or wrap Candle so a caller can see actual venue/provider, requested and
actual timeframe, close status, and source timestamp. Cache keys must use actual
identity or carry an explicit substitution marker.

## Study Further

- Trace which TickFlow canonical schema reaches each stored Parquet dataset and
  whether union-by-name masks provenance drift.
- Determine whether `failed_out` reaches operator/job status for every daily
  pipeline caller or remains only a warning.
- Inspect TickFlow provider plugins that add instruments to learn whether their
  return contract matches the generic HTTP adapter.
- Quantify financial overwrite behavior and last-sync semantics in Domain 9;
  do not patch during analysis.
- Inventory every Aletheia use of fallback OHLCV and identify whether requested
  `1h` can receive CoinGecko's coarser bars.
- Decide whether fallback-venue OHLCV should be cached under actual venue,
  primary request identity plus provenance, or not cached for exact consumers.
- Audit generic Ticker/Candle and collector model fields for finite/range/OHLC
  validation, prioritizing data that reaches scoring or protective behavior.
- Check cross-process request volume between Aletheia gateway, standalone
  collector, and any remote workers before claiming rate-limit safety.
- Compare collector raw errors with stored safe error codes to ensure credentials,
  URLs, or sensitive response bodies cannot reach operator surfaces.
- Determine whether venue listing fail-open is appropriate for each consumer;
  filtering a research universe and proving execution listing are different.
- Define explicit resampling if a coarser source is valuable; never relabel it as
  the requested timeframe.

## Do Not Reuse

### Divergent canonical schema declarations

Do not keep one list for documentation and another in the active normalizer.
Schema authority must be executable and unique.

### Capability as a plan-name shortcut

Do not assume subscription tier, configured key, current provider health, and
feature support are one boolean.

### Unused infrastructure as safety evidence

An implemented token bucket with no caller does not enforce rate limits. Verify
the actual call path before claiming protection.

### Silent timeframe or market substitution

Never return 4-hour bars under a 1-hour request, spot under a perpetual request,
or another venue under an exact-venue request without an explicit compatible
contract and visible provenance.

### Partial frame as full replacement permission

A frame containing successful chunks is not evidence that the requested full
scope was obtained. Do not overwrite an authoritative table or record a full
sync time without completeness proof.

### Source provenance held only in logs

Logs cannot tell a later strategy, audit, or recovery process which provider
produced a stored row.

### Silent `strict=False` conversion for required facts

Turning malformed identity, timestamp, or price into null without a rejection
fact hides upstream schema drift.

### Generic fallback for protective actions

Keep Aletheia's exact venue and market-type requirements. Availability must not
win over execution identity.

### Custom financial mapping based only on symbol

Financial values need exact period, currency, unit, consolidation,
announcement/as-of, source, and restatement semantics before cross-provider use.

### One giant provider framework migration

Do not stop Alpha work to force every specialized collector through one
interface. Start with a bounded descriptor/result contract where semantic drift
or operational pain is already visible.

## Durable Design Reflections

### A DataFrame is not a data contract

Column names do not express actual provider, coverage, units, as-of time,
resolution, partial failure, adjustment, or fallback. Those facts belong beside
the rows.

### Fallback is a semantic operation

Changing provider can change venue, liquidity, currency proxy, timeframe,
adjustment convention, revision policy, and legal availability. Retry and
fallback are not interchangeable.

### Empty is information

An honest empty market, an unsupported dataset, a missing key, an upstream
failure, and a parser rejecting every row demand different recovery. Preserve
the distinction end to end.

### Freshness is consumer-relative

A 30-minute macro observation and a 30-minute order book do not have equal
decision value. Aletheia's per-domain policy is the right direction; provider
cadence alone cannot define usability.

### Provenance must survive caching

Persisting the actual exchange is not enough if an in-memory cache later returns
the same bars under a primary-exchange key. Provenance is part of cache identity
or cache value.

### Completeness authorizes replacement

Before replacing a universe or full table, prove the requested scope was fully
obtained or use merge semantics that preserve known-good missing partitions.

### Quality checks should be observable

Dropping or nulling bad rows is sometimes necessary. The system should still
record how many, why, from which provider, and whether the remaining result is
safe for its consumer.

### Operational health and data usability are separate

A provider may be reachable yet return stale or semantically incompatible data;
it may be temporarily unreachable while last-known data remains valid for a
noncritical view. Model both axes.

### Provider abstractions should follow real equivalence

An interface is useful only where implementations can honor the same semantics.
Specialized collectors should remain specialized when their units, cadence, or
authority cannot be honestly generalized.

## Evidence Boundary

These lessons are static architectural conclusions. No live data quality,
provider response, rate-limit event, fallback, or storage outcome was observed.
