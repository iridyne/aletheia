# Domain 10 Source Map

## Reference Snapshot

- Repository: `/Users/yixian/Projects/references/tickflow-stock-panel`
- Git snapshot: `99bdec875d4bbf5c30ddc43534b81ada0f3b0f6b`
- Primary realtime transport: server-sent events (notifications, not quote rows)
- Primary data retrieval after notification: REST + React Query

## TickFlow Backend Realtime Path

### `backend/app/services/quote_service.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 48-130 | `QuoteSubscriber` | One subscriber per SSE connection; booleans coalesce quote/strategy/depth signals, alerts/reviews use bounded per-client lists. |
| 133-143 | `_persist_last_fetch` | Writes acquisition timestamp to preferences outside the main lock. |
| 145-201 | `QuoteService.__init__` | Owns state lock, fetch lock, lifecycle flags, subscriber set, metadata, index cache, and final-sync status. |
| 203-305 | lifecycle/pause | Starts a daemon polling thread, persists enabled state, and supports temporary pipeline pause/resume. |
| 331-389 | subscriber management | Snapshots the subscriber set and broadcasts notification or queued payload to every connection. |
| 396-425 | mode/interval | Maps entitlement/provider choice to none/watchlist/full-market and clamps tier-specific polling interval. |
| 431-505 | read/status API | Exposes cached enriched/index data and status including running, paused, mode, counts, quote age, market phase, and final-sync outcome. |
| 511-550 | poll loop/fetch lock | Polls by phase, retries final boundary sync, sleeps in 0.5-second slices, and serializes manual/background fetches. |
| 552-659 | full-market acquisition | Selects stock/ETF/index scopes and normalizes quote response fields. |
| 660-743 | `_process_full_market_records` | Updates fetch metadata first, persists daily data, computes/publishes enriched rows, broadcasts `quotes_updated`, then evaluates monitors. |
| 745-870 | watchlist acquisition | Fetches batches independently, continues after failed batches, merges successful asset subsets, broadcasts, and evaluates monitors. |
| 990-1049 | market phases | Separates preopen, continuous sessions, final boundary states, and closed; final state remains polling until one fetch reports success. |
| 1058-1410 | monitor/alert path | Evaluates current data, persists alerts, updates strategy results, broadcasts alerts/results, and offloads webhook work. |
| 1421 onward | `_flush_live_enriched` | Uses incremental current-day calculation when hot baselines exist, otherwise scans a bounded history; catches errors and logs without raising. |

### `backend/app/api/intraday.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 27-84 | index fallback | Returns latest local daily index closes when the realtime cache is absent and labels response source. |
| 87-121 | status/index REST | Reads only service memory or local fallback; does not trigger provider acquisition. |
| 124-191 | `/stream` | Registers one subscriber per connection and emits named SSE events; waits through `asyncio.to_thread`; unregisters in `finally`. |
| 145-182 | event serialization | Chunks alerts by 20, forwards review messages, and emits coalesced quote/strategy/depth invalidations. |
| 194 onward | manual refresh | Invokes the same fetch-locked service path. |

### `backend/app/main.py`

- Creates one quote service in application lifespan.
- Injects repository/application state.
- Starts/stops quote/depth services with the application.
- Domain 6 records that service ownership is visible but exposed through
  untyped `app.state` dependencies.

## TickFlow Frontend Realtime Path

### `frontend/src/lib/useQuoteStream.ts`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 10-47 | connection store | Module-global `connected/reconnecting/disconnected` state exposed with `useSyncExternalStore`. |
| 49-59 | focused symbol | Registers one dialog symbol for exact K-line invalidation without globally refreshing all charts. |
| 73-107 | hook setup | One top-level hook owns EventSource and current preference refs. |
| 110-129 | connection lifecycle | Connects even when realtime quotes are disabled because alerts/reviews are independent. |
| 130-164 | quote/result invalidation | Filters configured query-key prefixes and separately invalidates the focused K-line or strategy result. |
| 166-207 | depth/alert/review | Routes each event to narrow cache invalidations, toast stores, and the review store. |
| 209-232 | reconnect | Closes failed source, marks reconnecting, warns after three failures, and retries exponentially up to 60 seconds. |

### `frontend/src/lib/queryKeys.ts`

- Centralizes query-key factories.
- Defines a narrow `SSE_INVALIDATE_PREFIXES` list.
- Deliberately excludes strategy-result and minute-batch keys from each quote
  tick to avoid duplicate/expensive/upstream-limited refetches.

### `frontend/src/lib/useSharedQueries.ts`

- Treats SSE as the normal quote-status invalidation path.
- Keeps one 60-second global status poll for session-boundary changes when no
  quote event is emitted.

### `frontend/src/components/Layout.tsx`

- Mounts the stream hook once.
- Shows a persistent reconnecting badge and toast after repeated failures.
- Displays enabled/running/trading/paused/mode/provider distinctions.
- Keeps index fallback data visible when realtime is off if the user pins it.

### `frontend/src/pages/Dashboard.tsx`

- Retains previous React Query data during refresh.
- Labels realtime versus non-realtime and displays quote age.
- Explicitly warns that watchlist-only mode does not make the full market
  dashboard realtime.

### `frontend/src/pages/Watchlist.tsx` and `frontend/src/pages/Screener.tsx`

- Minute data uses bounded periodic refresh only when the preference is enabled
  and realtime service is running.
- Minute queries are intentionally outside the per-quote invalidation list to
  protect upstream request budgets.

## Aletheia Push Hub

### `aletheia/server/routes/ws.py`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 1-25 | transport contract | WebSocket pushes only changed category names; REST remains data authority; some volatile endpoints remain on polling. |
| 35-76 | fingerprint definitions | Regular categories use scalar `MAX(...)` queries; collector error has a separate fast fingerprint. |
| 78-81 | cadence | Regular tier every 500 ms and alert tier every 100 ms. |
| 84-107 | fingerprint helpers | Uses thread-local SQLite connections in executor threads and maps query/schema errors to scalar `0`. |
| 110-160 | `_RealtimeHub` | One process-wide sampler for all clients; client set, two tasks, and one serialized broadcast lock. |
| 163-206 | sampler loops | Polls in-memory infra and database fingerprints, broadcasts changed category names. |
| 207-235 | `/ws` | Sends an initial all-category refresh, adds client, waits for disconnect, and stops sampler tasks after the last client leaves. |

At current declarations, connected clients cause approximately 32 regular
fingerprint SQL statements per second plus 10 alert SQL statements per second,
before counting the in-memory infra check. This is an inference from configured
query count and interval, not a runtime measurement.

### `aletheia/gateway/frontdoor.py`

- Proxies `/api/*` WebSockets to the selected backend.
- Forwards text/binary frames in both directions.
- Cancels the opposite direction when either completes.
- Emits an infra event and closes with 1013 when the backend proxy is unavailable.

## Aletheia Frontend Push and Polling

### `frontend/src/lib/useRealtimeUpdates.ts`

| Lines | Symbol or region | Evidence |
|---:|---|---|
| 14-106 | `CHANGE_MAP` | Explicitly maps backend categories to one or more React Query key prefixes. |
| 108-115 | retry constants | Three-second base and 30-second cap. |
| 124-193 | hook lifecycle | Opens one WebSocket, parses category frames, invalidates matching queries, reconnects with backoff, and closes while the tab is hidden. |
| 174-185 | visibility behavior | Reconnects on visible and relies on the server's initial all-category frame to catch up. |

The hook does not expose connection status, last-message age, or a user-visible
degraded indicator in the inspected source.

### `frontend/src/charts/PriceChart.tsx`

- Loads the newest 300 candles, pages older history on scroll, and polls two
  newest candles every 10 seconds.
- Maintains the chart imperatively so new tail data does not reset the viewport.
- Ignores transient tail polling errors and retries next interval.
- Does not show a stale age/source after initialization; an old chart can remain
  visually `ok` during a prolonged provider failure.

### `frontend/src/components/MarketTicker.tsx`

- Fetches each major symbol independently every 15 seconds so a global newest-N
  window cannot omit majors.
- Keeps the last snapshot visible but dims it and marks it stale after five
  minutes.
- Shows unwired indices as unavailable rather than fabricating values.
- WebSocket invalidation and periodic poll can both refresh the same cheap
  database-backed query.

### `aletheia/server/routes/data.py`

- `/market/snapshots` reads canonical persisted snapshots.
- `/market/ohlcv` enforces exact instrument identity, bounds requests, uses an
  in-memory cache for one default view, otherwise fetches provider data, and
  falls back to exact-scope SQLite rows.
- It sends `X-Aletheia-Data-Source` as memory cache, provider fetch, database
  fallback, or unavailable; the generic frontend client returns JSON and does
  not expose this header to `PriceChart`.

### `aletheia/trading/market_data.py`

- Fetches exact-venue tickers first, accumulates successes, persists snapshots
  in one batch transaction, and returns collected plus error scopes.
- The database snapshot is canonical for dashboard reads.

### `aletheia/data/freshness.py`

- Provides explicit fresh/missing/invalid/future/stale verdicts for decision
  context with domain-specific bounds.
- This fail-closed policy is stronger than visual transport liveness and should
  remain separate from UI “connected” status.

## Runtime Evidence Not Available

- Actual event latency, reconnect time, dropped message count, slow-client
  behavior, DB fingerprint load, REST amplification, and cache hit rate:
  `Not available`.
- Whether production proxies emit/retain heartbeat frames at every layer:
  `Not available` from this static trace.
- Exact number of concurrent dashboards/tabs: `Not available`.
- Whether any TickFlow installation has missed alerts during disconnect or
  observed stale data after a failed enriched flush: `Not available`.
- Whether Aletheia fingerprint omissions have caused visible stale panels:
  `Not available`.
