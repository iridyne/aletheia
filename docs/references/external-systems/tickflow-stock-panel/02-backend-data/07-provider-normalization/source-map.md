# Domain 7 Source Map

## TickFlow Provider and Capability Sources

| Boundary | Current source | Role |
|---|---|---|
| provider contract | `backend/app/data_providers/base.py` | asset types, six capability booleans, and structural market-data methods |
| built-in registry | `backend/app/data_providers/registry.py` | constructs the TickFlow adapter; currently contains only TickFlow |
| built-in adapter | `backend/app/data_providers/tickflow_provider.py` | maps TickFlow instruments, daily, adjustment, realtime, and nominal minute methods |
| canonical conversion | `backend/app/data_providers/normalizer.py` | converts heterogeneous frames to selected daily, adjustment, and instrument columns |
| alternate schema declaration | `backend/app/data_providers/schemas.py` | declares broader canonical column lists that differ from the active normalizer |
| custom config | `backend/app/data_providers/custom/config.py` | YAML auth, dataset, batch, RPM, timeout, mapping, transform, and request parameter declarations |
| custom loader | `backend/app/data_providers/custom/loader.py` | validates/loads YAML sources and optional built-in plugins into process memory |
| custom HTTP adapter | `backend/app/data_providers/custom/provider.py` | batches synchronous HTTP, maps JSON rows, applies transforms, and normalizes datasets |
| safe field mapper | `backend/app/data_providers/custom/mapper.py` | dot-path extraction, rename/select, and a small transform whitelist without `eval` |
| provider preferences | `backend/app/services/preferences.py` | chooses daily, adjustment, minute, and financial providers |
| capability authority | `backend/app/tickflow/capabilities.py`, `policy.py` | probes features, classifies tiers, persists a versioned snapshot, and exposes limits |
| effective rate limits | `backend/app/tickflow/rate_limits.py` | applies an 80% safety budget, chunks requests, and shares per-process send slots |
| nominal async limiter | `backend/app/tickflow/scheduler.py` | defines per-capability token buckets but has no current application caller |
| daily/minute/adjustment sync | `backend/app/services/kline_sync.py` | provider routing, normalization, batching, partial failure, segmented minute retrieval, and persistence handoff |
| instrument sync | `backend/app/services/instrument_sync.py`, `index_sync.py` | assembles stock/index/ETF universes from free and paid sources |
| financial sync | `backend/app/services/financial_sync.py` | selects TickFlow/custom finance, batches five tables, merges share history, and hands frames to storage |

## TickFlow Provider Flow

```text
startup/settings
  -> detect TickFlow capabilities with minimal probes
  -> classify none/free/starter/pro/expert
  -> persist versioned capability + limits snapshot
  -> augment minute capability from selected custom source

sync request/job
  -> read provider preference
  -> if custom source declares dataset
       -> HTTP request chunks
       -> response-path extraction
       -> configured field map + safe transforms
       -> dataset normalizer
     else
       -> TickFlow SDK path gated by capability
       -> batch/rpm limits
       -> local normalizer
  -> frame passed to repository/file writer
```

## TickFlow Dataset Outcome Semantics

| Condition | Observed behavior |
|---|---|
| selected custom provider lacks a dataset | several paths fall back to TickFlow |
| custom minute resolver/call raises | warns and falls through to TickFlow |
| custom minute call succeeds with empty frame | treated as authoritative empty; no fallback |
| TickFlow daily batch chunk fails | other chunks continue; failed symbols can be surfaced through an output list |
| instrument exchange fetch fails | other exchanges continue; resulting list may be partial |
| financial batch fails | other batches continue; partial frame can still be treated as the table result |
| capability probe has a transient divider failure | prior paid capability snapshot can be retained |
| general capability detection fails outside that protection | none baseline is persisted |

## Static TickFlow Inventory

- the focused provider/capability/sync surface inspected here exceeds 4,300
  lines before route and repository code;
- `ProviderCapabilities` declares six boolean dataset abilities;
- `CapabilitySet` exposes thirteen named TickFlow capabilities with optional
  RPM, batch, and subscription limits;
- the custom provider accepts five dataset names: daily, adjustment factor,
  realtime, minute, and financial;
- custom timeouts are constrained to greater than zero and at most 300 seconds;
- the effective batch limiter has eighteen current callers; the separate async
  token-bucket `Scheduler` has no current application instantiation;
- the active normalizer and `schemas.py` do not declare the same canonical
  columns.

Counts are source-pattern inventory, not runtime coverage or correctness proof.

## Aletheia Provider and Quality Sources

| Boundary | Current source | Role |
|---|---|---|
| value/protocol contract | `aletheia/data/providers.py` | Ticker, Candle, and structural async provider interface |
| exact identity | `aletheia/data/symbols.py` | canonical symbol plus exchange and market type through `InstrumentId` |
| CCXT adapter/factory | `aletheia/data/ccxt_provider.py` | fresh event-loop-local clients, primary/fallback venues, closed bars, persistence, and cleanup |
| fallback chain | `aletheia/data/sources/chain.py`, `coingecko.py`, `binance_public.py` | spot fallback composition, throttling, source switching, and provider-specific resolution |
| in-memory OHLCV cache | `aletheia/data/cache.py` | TTL cache keyed by exchange, symbol, market type, timeframe, and limit |
| exact protective pricing | `aletheia/data/price_feed.py` | separate exact-instrument cache and fail-closed SQLite fallback for protective closes |
| durable OHLCV authority | `aletheia/storage/repositories.py`, migrations | exact-instrument primary key, upsert, rolling window, coverage, and retry state |
| freshness policy | `aletheia/data/freshness.py`, `universe/snapshot_freshness.py` | fail-closed domain age and future-time checks with exact market identity |
| venue refusal breaker | `aletheia/data/venue_health.py` | process-local cooldown and operator events after explicit 418/429/refusal signals |
| listing cache | `aletheia/data/venue_listing_cache.py` | six-hour active-symbol whitelist, single-flight load, and fail-open filtering |
| collector models/parsers | `aletheia/data/models.py`, `collectors/*.py` | specialized microstructure, derivatives, macro, on-chain, and partner-source normalization |
| durable collector health | `aletheia/data/collector_health.py` | success/partial/empty/failure taxonomy, cadence, counters, and incident attribution |
| coverage repair | `aletheia/data/active_ohlcv_backfill.py` | bounded exact-instrument gaps, retry/backoff state, and minimum-bar verification |

## Aletheia Provider Flow

```text
InstrumentId(exchange, canonical symbol, market type)
  -> fresh CCXTProvider for current event loop
  -> primary venue, then configured CCXT fallback
  -> for spot only: CoinGecko, then Binance public
  -> Ticker or Candle values
  -> confirmed OHLCV bars persisted with actual exchange
  -> freshness/coverage consumers select exact identity

specialized collectors
  -> bounded source-specific requests
  -> Pydantic snapshots + errors
  -> persistence-gated health classification
  -> durable success / partial / empty / failed / timed_out state
  -> repeated failures attributed to provider or shared connectivity incident
```

## Static Aletheia Inventory

- the core protocol, CCXT/fallback, identity, freshness, venue health/listing,
  collector health, and active-backfill files inspected here total more than
  2,400 lines before specialized collector implementations;
- one exact `InstrumentId` includes exchange, canonical symbol, and market type;
- OHLCV storage identity includes exchange, symbol, market type, timeframe, and
  timestamp;
- eight collector domains have durable cadence/health definitions;
- freshness currently has eight decision domains with domain-specific maximum
  ages and a five-minute future-skew limit;
- the generic MarketDataProvider protocol has no declared capability/limit model.

## Evidence Classification

The source proves declared schemas, routing, persistence keys, and failure
branches. It does not prove upstream response shape, entitlement, numerical
quality, actual rate limits, cross-process request volume, or live fallback
behavior.
