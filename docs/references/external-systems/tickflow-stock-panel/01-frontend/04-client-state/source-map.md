# Domain 4 Source Map

## TickFlow State Boundaries

| Area | Current source | Role |
|---|---|---|
| global query policy | `frontend/src/main.tsx` | QueryClient defaults and global authentication-error redirect |
| key registry | `frontend/src/lib/queryKeys.ts` | typed key constants/factories and the realtime-invalidated prefix list |
| shared queries | `frontend/src/lib/useSharedQueries.ts` | canonical capabilities, settings, preferences, quote status, version, and data-status observers |
| shared mutations | `frontend/src/lib/useSharedMutations.ts` | common mutation-to-cache synchronization for realtime quotes, intervals, and watchlist batches |
| realtime transport | `frontend/src/lib/useQuoteStream.ts` | one EventSource connection, event routing, cache invalidation, alert delivery, reconnect, and connection-state store |
| stream owner | `frontend/src/components/Layout.tsx` | mounts the quote stream once and renders connection status |
| long-lived review state | `frontend/src/lib/reviewStore.ts` | module-level manual/SSE generation state that survives Review page unmount |
| data/pipeline orchestration | `frontend/src/pages/Data.tsx` | adaptive job polling, mutation invalidation, and one broad reset invalidation |
| dense analytical consumers | `frontend/src/pages/Screener.tsx`, `Watchlist.tsx`, `Review.tsx`, `Monitor.tsx` | parameterized queries, previous-data retention, selective polling, and stream consumers |

## TickFlow Main Flow

```text
QueryClient defaults
  -> QK registry -> shared hooks / page queries
                     -> React Query cache -> UI

backend SSE event
  -> useQuoteStream event router
      -> alert/review side channel when payload is the product data
      -> targeted query-prefix invalidation otherwise
          -> active query refetch -> authoritative REST response -> UI

manual or scheduled review stream
  -> reviewStore module singleton
      -> phase/content/meta/source arbitration
      -> Review page subscribers when mounted
```

## TickFlow Query Policy Snapshot

- default stale time: five seconds;
- refetch on window focus: disabled;
- global QueryCache/mutation error path redirects recognized authentication and
  uninitialized-access failures to Login;
- version query stale time: infinite;
- quote-status fallback polling: 60 seconds only in the designated global
  observer;
- daily/fundamental analytical data commonly uses longer, domain-specific stale
  times;
- job status polling changes or stops according to terminal state.

## TickFlow Realtime Event Routes

| Event | Client effect |
|---|---|
| `quotes_updated` | invalidates configured market prefixes, quote status, and the currently focused stock's K-line |
| `strategy_results_updated` | invalidates screener cached results only |
| `depth_updated` | invalidates limit-ladder and overview-market data |
| `strategy_alert` | delivers alert UI and invalidates alert lists/counts |
| `review_progress` | feeds the review streaming store; completed work invalidates report history |

The general quote prefix set includes watchlist, quote status, index quotes,
overview market, and limit ladder. Minute batches deliberately use a different
prefix so per-second quote invalidation does not repeatedly consume provider
quota.

## Aletheia Comparison Sources

| Area | Current source | Role |
|---|---|---|
| global query policy | `frontend/src/main.tsx` | 30-second stale window, five-minute garbage collection, one abort-aware retry, and focus policy |
| database change router | `frontend/src/lib/useRealtimeUpdates.ts` | maps backend change categories to query-key prefixes over one WebSocket |
| global owner | `frontend/src/App.tsx` | mounts realtime updates once and owns adaptive topbar status polling/restart recovery |
| operator event stream | `frontend/src/pages/CommandCenter.tsx` | merges pushed operator events into cache and invalidates related operator views |
| confirmed control cache | `frontend/src/components/CommandPalette.tsx` | writes server-confirmed kill-switch state before invalidating dependent status |
| request cancellation | `frontend/src/api/client.ts` | combines caller cancellation with a bounded request timeout |
| distributed query users | `frontend/src/pages/`, `frontend/src/components/`, `frontend/src/features/` | define literal keys and mutation invalidation close to consumers |

## Static Inventory Signal

A bounded text inventory found approximately:

- TickFlow: 204 `queryKey: QK...` occurrences, 54 literal-array query-key
  occurrences, and 159 invalidation occurrences;
- Aletheia: no shared `QK` registry, about 340 literal-array query-key
  occurrences, and 165 invalidation occurrences.

These are source-pattern counts, not semantic uniqueness counts. They establish
relative centralization, not runtime request volume or correctness.
