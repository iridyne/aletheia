# TickFlow Realtime Synchronization Analysis

## 1. TickFlow Uses Push Invalidation, Not Push Data

The most important architectural choice is that `quotes_updated` carries a
timestamp and count, not thousands of quote rows. The frontend invalidates
selected React Query caches and obtains current data from REST-backed read
models.

```text
provider poll
  -> local daily/enriched publication
  -> notification: category changed
  -> React Query invalidation
  -> REST read from local authority/cache
  -> component keeps or replaces prior view
```

This preserves one data API, avoids defining parallel SSE and REST row schemas,
and makes reconnect recovery possible through refetch. It is one of TickFlow's
best directly reusable patterns and matches Aletheia's WebSocket philosophy.

## 2. One Subscriber Per Connection Fixes Broadcast Semantics

TickFlow's comments document an earlier shared `Event`/pending-list design where
the first client to pop an alert consumed it for every other tab/device. The
current design registers a `QuoteSubscriber` for each connection and broadcasts
to a snapshot of all subscribers.

Each subscriber owns:

- one wake event;
- coalesced booleans for quote, strategy-result, and depth changes;
- a bounded alert list (default 1,000);
- a bounded review list (default 200).

This is a clean distinction between state invalidation and event payload:

- repeated “quotes changed” signals can coalesce safely because the consumer
  refetches current state;
- alerts/review deltas cannot coalesce into one bit, so they need queues;
- bounded queues prefer recent events under backpressure.

The queues still lack sequence IDs and drop counters, so a client cannot know
that old payload events were discarded.

## 3. Per-Client Queues Provide Isolation but Consume Threads

The SSE generator calls `sub.wait` via `asyncio.to_thread` with a five-second
timeout. Each connection can occupy a worker thread while waiting. For a local
desktop with a handful of tabs this is likely acceptable; it is not a scalable
fan-out design for many remote clients.

An asyncio condition/queue per subscriber could retain semantics without tying
idle connections to the thread pool. The current implementation should be
judged against its local deployment, not internet-scale expectations.

## 4. Polling Is Market-Phase-Aware

The backend distinguishes:

- preopen;
- morning continuous trading;
- a morning-final state;
- pre-afternoon;
- afternoon continuous trading;
- close-final;
- closed/weekend.

Morning/close final states keep polling until one fetch reports success, then
stop for that date/boundary. This avoids freezing the last in-session sample as
the official boundary merely because the clock crossed 11:30 or 15:00.

The status API separately exposes continuous trading and polling window. The UI
can therefore avoid equating “service is still doing a final fetch” with “market
is trading.” This is excellent semantic detail.

Holiday awareness is only partially visible: weekday/time drives phase, while
monitor evaluation uses current snapshot date as a freshness backstop. A formal
exchange calendar is not evident in this path.

## 5. Fetch Serialization Is Correctly Scoped

Manual refresh and the background poll both call `_fetch_quotes`; a dedicated
lock ensures only one acquisition/publication path runs at a time. Poll sleep is
split into half-second pieces so stop or interval changes respond faster.

Pipeline work uses a separate pause context to avoid realtime publication racing
with batch reconstruction. Domain 9 records that pause-based exclusion must
cover every entry point and cannot substitute for one dataset publisher, but it
is a practical local coordination technique.

## 6. Acquisition Success Is Recorded Before Read-Model Success

`_process_full_market_records` updates `_fetched_at`, counts, and index cache as
soon as records are normalized. It then attempts daily persistence and enriched
calculation. Those later failures are caught/logged. The service broadcasts
`quotes_updated` afterward and `_fetch_quotes` reports success by comparing
`_fetched_at`.

This creates several different success facts:

- provider response obtained;
- normalized records non-empty;
- daily partition published;
- enriched read model published;
- monitor evaluated;
- notification emitted.

Only the first two govern fetch success/final-sync completion. A final boundary
can be marked done even if daily or enriched publication failed. A frontend
receiving `quotes_updated` may refetch the previous enriched cache.

The timestamp is honestly named “last fetch” in much of the UI, but the terminal
`final_sync_done` label overstates downstream completion.

## 7. Notifications Follow Publication Attempts, Not Verified Version

The backend emits `quotes_updated` after file/enriched calls return, but those
calls may swallow errors. The event does not contain a publication generation or
the latest read-model timestamp.

A safer state notification would include:

```text
publication_id
source_observed_at
read_model_as_of
complete / partial / failed
affected asset scopes
```

The REST response remains authoritative, but generation lets the client decide
whether it actually caught up and lets telemetry measure notification-to-read
latency.

## 8. Watchlist Mode Is Deliberately Partial

Free/watchlist mode fetches symbols in provider-limited batches and continues
after a batch failure. Successful symbols merge into today's partitions so
failed symbols retain their previous rows. This is better than replacing the
whole date with a partial watchlist.

The service updates counts and broadcasts based on successful records without
returning failed symbols/status to clients. The UI does, however, explicitly
warn that watchlist realtime does not make the broad market dashboard realtime.

Scope honesty is good; per-cycle completeness remains missing.

## 9. Event Taxonomy Is Consumer-Specific

TickFlow does not send one generic `changed` event. It distinguishes:

- `quotes_updated`;
- `strategy_results_updated`;
- `strategy_alert`;
- `depth_updated`;
- `review_progress`.

This lets the frontend avoid refreshing strategy results every one-second quote
tick and refresh them only after monitor computation finishes. Depth changes do
not fan out into unrelated quote pages. Review progress goes to a specialized
store instead of React Query.

The taxonomy encodes causality: source data changed, derived computation
finished, and event occurred are different notifications. That is stronger than
invalidating every market-related key from one tick.

## 10. Query-Key Routing Is Centralized and Cost-Aware

`queryKeys.ts` documents which prefixes realtime changes invalidate. The
frontend filters them by user preference and intentionally excludes expensive or
semantically independent keys.

Notably, minute-batch data is refreshed on a configurable interval rather than
every quote event. This prevents an expert one-second quote feed from causing a
one-second upstream minute-bar request. Focused K-line refresh uses one registered
symbol rather than all charts.

This is a strong combination of correctness and rate-limit awareness:

```text
push frequency != refetch frequency for every derived resource
```

## 11. React Query Remains the Consistency Coordinator

SSE handlers invalidate keys rather than manually mutating every component's
data. Components can retain placeholder data, display loading/error separately,
and share one cache entry across pages.

The centralized mapping reduces duplicate polling and gives one review point for
refresh amplification. Prefix matching is convenient but string-prefix logic can
overmatch future keys; typed factory-based exact families are safer as the app
grows.

## 12. Reconnect Is Visible but Not Recoverable by Sequence

The frontend:

- detects EventSource error;
- closes the source;
- marks global status reconnecting;
- displays a bottom badge;
- emits one error toast after three consecutive failures;
- retries with exponential backoff up to 60 seconds;
- resets counters on open.

This is excellent operator honesty. The toast correctly warns that strategy
alerts may be missed.

What it lacks is catch-up:

- no event ID or `Last-Event-ID` handling;
- no replay buffer;
- no initial “invalidate all/current generation” event on reconnect;
- no REST reconciliation of alerts from the last acknowledged ID;
- no dropped-queue count.

If no new quote arrives after reconnect, data changed during the gap can remain
stale. The 60-second quote-status poll covers status, not every affected query.
Persisted alert history and 15-second total polling may recover the record list,
but not the missed toast/event semantics.

## 13. Heartbeat and Connection Status Are Different

`sse-starlette` emits comment pings to keep the transport alive. The browser's
`onopen`/`onerror` drives the status badge. A connected transport only proves the
path is open; it does not prove quote acquisition, read-model publication, or
fresh data.

TickFlow mostly respects this separation by showing service status, quote age,
market phase, and stream reconnect independently. A future status model should
make the axes explicit rather than relying on a single green dot.

## 14. REST Fallback Is Labelled for Indices

When realtime index memory is empty, the API reads the last two daily closes and
returns `source: index_daily`. The response and UI can therefore retain useful
reference values without claiming they are realtime.

This is a model for graceful degradation:

- preserve a last known value;
- expose its date/source;
- distinguish missing from fallback;
- never silently label a daily close as a live quote.

## 15. UI Mode Honesty Is a Major Strength

The dashboard computes realtime only for the latest date and service-running
state. It displays quote age and non-realtime state. In watchlist-only mode it
adds a prominent warning that the broad market is a post-close snapshot and
only a bounded watchlist is live.

This prevents entitlement/coverage from becoming a misleading visual badge. It
is directly relevant to Aletheia's requirement never to invent reassuring UI
state.

## 16. Error Handling Still Erases Data-Plane Degradation

Several realtime processing steps catch broad exceptions and continue:

- daily persistence;
- enriched computation;
- individual watchlist batches;
- provider/custom-source acquisition;
- monitor substeps and notification adapters.

Some availability-oriented continuation is correct. The missing part is an
outcome object that reports which stage failed. Logs are not enough for the UI,
final-sync policy, or postmortem to distinguish acquisition success from
published-state success.

## 17. Overall Realtime Assessment

TickFlow's realtime architecture is mature for a local data dashboard:

- one acquisition service and fetch lock;
- market-phase/final-boundary logic;
- per-client broadcast with bounded payload queues;
- coalesced invalidation for state changes;
- narrow event taxonomy;
- centralized query-key routing;
- rate-limit-aware refresh selection;
- visible reconnect and mode/freshness labels;
- REST remains data authority.

Its main gaps are recoverability and success semantics:

- no event sequence/replay/initial catch-up;
- no slow-client drop telemetry;
- thread-per-wait scaling;
- acquisition timestamp precedes read-model publication;
- final sync can succeed despite downstream failure;
- notification has no generation/coverage;
- partial watchlist cycles are not exposed;
- broad exception handling turns data-plane failure into logs.

The correct lesson is not “use SSE instead of WebSocket.” The transport is
secondary. The reusable architecture is a typed invalidation plane over a
truthful REST/read-model plane with explicit freshness and recovery.
