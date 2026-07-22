# Market Data Observability

## Two different questions

Market-data operations have two independent truths:

1. Did each scheduled collector run and how did it finish?
2. What data is actually present and recent enough in SQLite?

`/api/market/collector-health` answers the first question. It can honestly report
an `empty` completed call. `/api/market/data-coverage` answers the second question
with exact retained row count, distinct symbol count, source/exchange counts, newest
observation, age, and freshness state.

Collector health also separates a source failure from a confirmed shared
infrastructure cause. An independent neutral-endpoint probe establishes external
connectivity without treating one exchange outage as a global network failure.
Collector failures observed around a confirmed outage retain their raw status but
are presented as informational dependent consequences under one connectivity
incident. A bounded recovery state remains visible while those collectors wait for
their next scheduled retry.

The Overview System card shows both rows. Collector failure is operational
degradation; data coverage is yellow when a decision-critical persisted domain is
empty, malformed, or stale. Liquidations are an event stream, so no recent event is
`idle`, not a fabricated collector failure.

## Coverage domains

The report separates primary-venue funding and OI from Hyperliquid and Coinalyze
secondary/aggregate rows. LSR has its own coverage entry because an OI row does not
guarantee that the optional account-ratio fetch succeeded.

Covered domains are orderbook, primary funding, primary OI, LSR, liquidations,
macro, on-chain, and OHLCV. OHLCV freshness is based on candle-open milliseconds;
other domains use their persisted observation timestamp.

The endpoint is a synchronous FastAPI route, so SQLite work runs in the framework
thread pool. Its exact counts scan the retained funding/OI/LSR/OHLCV indexes. A
30-second in-process cache prevents duplicate Overview scans. The response's
`Server-Timing` header names every domain, cache hits, report total, and global app
wall time. A refresh over 500 ms writes `data_coverage_refresh_slow` with only the
three slowest domain durations; it never logs SQL parameters or row values. This
separates actual scan time from cache-lock, thread-pool, or surrounding request wait.

## OHLCV response provenance

`GET /api/market/ohlcv` keeps the existing candle-array response for frontend
compatibility and adds headers:

- `X-Aletheia-Data-Source: memory_cache`
- `X-Aletheia-Data-Source: provider_fetch`
- `X-Aletheia-Data-Source: database_fallback`
- `X-Aletheia-Data-Source: unavailable`
- `X-Aletheia-Data-Instrument: exchange:symbol:market_type:timeframe`

Provider failure falls back only to the exact requested exchange, symbol,
spot/perp market, timeframe, and historical `before` boundary. The SQLite read runs
through `asyncio.to_thread`; it cannot block the uvicorn event loop. Database rows
are not inserted into the provider's Candle-object memory cache, avoiding a hidden
type change for other provider consumers.

## CLI diagnostics

`aletheia inspect microstructure --json` reads the real `imbalance_ratio` column,
queries funding and OI by the known active-member venue, and reports each source,
observation time, and freshness state. Diagnostics retain stale rows and label them;
decision consumers omit them according to `decision_context_freshness.md`.

## Non-goals

- No collector cadence, timeout, retry, or concurrency change.
- No new market-data provider or exchange.
- No scanner, opportunity, risk, sizing, execution, or live behavior change.
- A fresh row is observable, not guaranteed economically correct.
