# Domain 7 Aletheia Comparison

## Different Market Domains

TickFlow specializes in Chinese equities, indices, ETFs, adjustment factors,
and company financial statements. Aletheia currently focuses on crypto spot and
perpetual instruments plus macro, on-chain, microstructure, derivatives, and
selected tokenized-equity observations. Financial-statement adapters are not a
like-for-like Aletheia gap.

The comparison therefore focuses on reusable provider semantics: exact identity,
capability, limits, provenance, fallback, freshness, partial results, and data
quality.

## Identity and Canonicalization

Aletheia is stronger at instrument identity. `InstrumentId` combines exchange,
canonical `BASE/QUOTE`, and market type. Venue-specific strings are created only
at adapter boundaries. OHLCV storage includes exchange and market type in its
primary key, and protective pricing uses a separate exact-instrument cache.

This directly prevents a fresh BTC/USDT spot quote on one exchange from proving
that a perpetual on another exchange is usable. Aletheia's universe freshness
check has no symbol-only fallback.

TickFlow's A-share symbols often encode exchange in the symbol, but normalized
daily/minute schemas can omit explicit asset type and source. Its broader
`schemas.py` intends those fields, yet the active normalizers do not enforce
them. Aletheia should preserve its exact identity and extend provenance rather
than adopt the looser frame shape.

## Provider Lifecycle

Aletheia constructs a fresh CCXT provider per operation because CCXT/aiohttp
sessions bind to the event loop that created them, while scheduler jobs use
fresh loops. Every path is expected to close the provider in `finally`, and
cleanup has bounded timeouts for both CCXT clients and injected sessions.

TickFlow uses process-level synchronous SDK clients and long-lived custom HTTP
providers. That is efficient for its lifecycle but not directly suitable for
Aletheia's event-loop topology. The reusable idea is to make client lifetime
match the library's concurrency model and record the reason near the factory.

## Capability and Limit Modeling

TickFlow is stronger here. It has named capabilities, observed entitlement,
batch/RPM/subscription limits, cache schema versioning, and a UI-facing snapshot.
Aletheia's generic provider protocol has only methods. Support is commonly
expressed through configuration, provider-specific knowledge, or
`NotImplementedError`; limits live in scattered throttles, budgets, serial loops,
and venue breakers.

Aletheia would benefit from a small provider-descriptor contract containing:

```text
provider and physical venue
supported instrument/market types
supported datasets and exact resolutions
batch and request budgets
history/pagination limits
source currency/unit/adjustment semantics
health and last successful observation
```

This should describe public market-data and research sources. It must not become
an authorization system for Execution Gateway or replace trading permissions.

## Fallback Safety

Aletheia has important safeguards:

- spot-only fallbacks are excluded for derivative `InstrumentId` requests;
- protective-close price refresh pins primary and fallback to the same physical
  venue, then verifies the returned ticker provider;
- protective SQLite fallback is exact by venue/market type and bounded to 120
  seconds;
- venue refusals open a process-wide cooldown and emit operator-visible events;
- fallback ticker use can notify the operator.

These are stronger than a generic “try the next provider” chain.

There are remaining semantic holes.

### Resolution substitution

`CoinGeckoProvider.get_ohlcv()` maps a requested `1h` timeframe to the same
seven-day endpoint used for 4-hour data, then returns ordinary Candle objects.
Neither Candle nor the fallback result says the actual resolution differs from
the request. A downstream indicator can therefore believe 4-hour bars are
1-hour bars.

### Provenance loss in memory

Candle has no provider, physical exchange, requested timeframe, actual
timeframe, quote currency, or quality fields. Persistence receives the actual
CCXT exchange from the adapter, but non-CCXT fallback candles are not persisted
through that same code path.

### Cache identity after venue fallback

CCXT OHLCV persistence uses the actual successful venue, but the in-memory cache
key uses the configured primary exchange even when CCXT fallback returned the
bars. A subsequent primary-scoped read can receive fallback-venue data without
seeing that substitution.

### Empty and zero values

The fallback chain treats any returned Candle list—including empty—as success,
and ticker adapters can create zero-valued fields. Some consumers validate
positive finite price, but the provider contract does not require a quality
verdict before stopping fallback.

The correct response is not to prohibit all fallback. It is to state which
dimensions may change for each consumer. Dashboard context may accept
cross-venue spot reference prices; protective execution pricing may not; OHLCV
indicator input must not silently change timeframe.

## Freshness and Closed-Bar Semantics

Aletheia is substantially stronger.

`assess_freshness()` classifies missing, invalid, future, stale, and fresh rows
against domain-specific decision-use ages. Malformed timestamps fail closed,
and future timestamps beyond bounded clock skew are rejected rather than
remaining fresh indefinitely.

CCXT marks the trailing still-forming bar as open, persists only confirmed bars,
and lets consumers distinguish the live bar. OHLCV history is stored by exact
instrument identity and pruned to a bounded rolling window.

TickFlow carries dates/timestamps and filters halt days, but the provider layer
does not expose a comparable freshness verdict, closed-bar fact, source
observation time, or future-time check. Its `quote_ts` is useful, yet the
alternate canonical schema omits it and most quality behavior remains implicit.

## Data Quality Validation

Aletheia's quality validation is uneven rather than uniformly strong.

Strong examples include:

- Hyperliquid parsing rejects non-finite and non-positive mark/OI values;
- OI deltas use same-source bounded-age baselines and sanity bounds;
- exact protective prices require finite positive values and sane age;
- partner aggregate import whitelists fields, rejects sensitive key shapes, and
  carries source hash/reference and quality labels;
- listing refresh failure returns indeterminate and filters fail open, avoiding
  mass false delisting.

Weaker examples include:

- generic Ticker/Candle dataclasses accept non-finite values and do not validate
  OHLC geometry or nonnegative volume;
- most Pydantic market-data models constrain types but few numerical ranges;
- microstructure can accept an inverted order book as a negative spread because
  it checks only positive best prices;
- provider fallback does not return accepted/rejected counts;
- canonical symbol parsing falls back heuristically for unknown separator-less
  symbols.

TickFlow's silent Polars casts and Aletheia's permissive dataclasses are variants
of the same issue: schema shape is not data quality proof.

## Partial Result and Health Semantics

Aletheia's collector-health design is one of the strongest practices in this
domain. Collector outcomes distinguish:

- success;
- partial;
- empty;
- failed;
- timed out;
- not configured;
- skipped by budget;
- disabled.

Health is persistence-gated for collectors whose in-memory result is not useful
until stored. Cadence, attempts, successes, partial/empty/timeouts, consecutive
problems, last success, duration, and safe error code are durable. Repeated
failures alert only after a threshold, and failures during a shared connectivity
incident are attributed under that incident rather than multiplied into
misleading root causes.

TickFlow has good local partial-failure comments and failed-symbol reporting in
daily sync, but no unified durable provider-run outcome across datasets. Its
financial last-sync behavior can claim recency after partial/empty results.

Aletheia should preserve collector-health semantics and extend them to provider
requests that feed critical decision contexts, rather than copy TickFlow's
timestamp-only sync status.

## Coverage and Recovery

Aletheia's active OHLCV backfill converts “we have a provider” into a measurable
coverage target. It plans exact exchange/symbol/market/timeframe gaps, bounds
work per run, applies per-pair and run budgets, persists ready/partial/failed
state, backs off failures, and verifies post-fetch bar count before declaring
ready.

This is stronger than assuming a successful fetch means adequate history.
TickFlow's segmented minute retrieval and failed-symbol output are useful inputs
to the same principle. The combined lesson is to make coverage a durable fact
with requested scope, obtained scope, and next repair action.

## Rate Limits and Provider Health

TickFlow centralizes advertised batch/RPM limits better, although enforcement is
process-local and the nominal async token bucket is unused. Aletheia distributes
control among CCXT's own limiter, serial fetch loops, CoinGecko's six-second
throttle, collector deadlines, bounded symbol caps, scheduler lanes, and venue
cooldowns.

Aletheia's venue cooldown reacts well to explicit refusal, but it is process
local and does not coordinate multiple gateway/collector processes or other
applications sharing an IP/account. TickFlow explicitly documents the same
cross-process limitation. Neither system proves account-wide rate safety.

## Financial Data Comparison Boundary

Aletheia does not currently operate the same Chinese-company financial statement
pipeline. It should learn the distinction between current statements and share
history, but should not add a generic financial-provider framework without an
Alpha-relevant use case and exact period/unit/restatement semantics.

## What TickFlow Does Better

- named runtime capabilities with limits and versioned cached detection;
- separation of plan label from observed capability set;
- declarative owner-configured HTTP field maps and safe transforms;
- provider choice per dataset rather than one global setting;
- segmented large-history acquisition with progress callbacks;
- visible failed-symbol scope in selected batch workflows.

## What Aletheia Does Better

- exact exchange/symbol/market identity;
- venue-safe protective pricing and fail-closed freshness;
- derivative-safe restriction of spot fallback;
- closed-versus-forming candle semantics;
- actual-exchange OHLCV storage identity and bounded rolling coverage;
- durable collector outcome/health taxonomy with incident attribution;
- bounded coverage repair with durable retry/backoff;
- whitelisted observation-only partner import with provenance.

## Invariants Aletheia Must Preserve

- public/research market data never authorizes execution;
- protective prices remain exact by execution venue and physical market;
- Risk and Execution Gateway authority remain unchanged;
- spot data never establishes perpetual readiness;
- stale, future, malformed, or unknown decision inputs fail closed;
- unavailable facts remain unavailable instead of receiving plausible values;
- fallback source and semantic substitutions remain observable;
- transactional trading authority remains SQLite, not provider cache or Parquet.

## Evidence Boundary

The comparison is static. The resolution substitution and cache-key provenance
issues follow directly from current code, but their downstream runtime impact was
not exercised. No upstream response or live collector outcome was inspected.
