# Aletheia Comparison: Realtime Synchronization

## 1. Both Systems Made the Same Strong Core Choice

TickFlow SSE and Aletheia WebSocket both push “something changed,” then let React
Query refetch the actual data through REST. Neither transport is a second market
data authority.

This convergence is more important than SSE versus WebSocket:

```text
push plane: low-volume invalidation and exceptional events
REST plane: typed current state and errors
storage/read model: canonical or reconstructible authority
```

Aletheia should preserve this design.

## 2. TickFlow Uses Domain Events; Aletheia Uses Fingerprint Categories

TickFlow publishers explicitly call quote, strategy-result, depth, alert, and
review notifications at semantic points in their workflow.

Aletheia polls database/in-memory fingerprints and emits generic category lists.
It does not require every business writer to remember a WebSocket callback, and
the REST database remains the source of truth. This is robust against missed
in-process event calls and process separation.

The trade-off is semantic precision:

- TickFlow knows “monitor computation finished.”
- Aletheia often knows only “the maximum timestamp in this table changed.”
- TickFlow can carry bounded event payloads.
- Aletheia avoids duplicating data/event schemas.

A hybrid can use committed durable change/outbox rows for high-value semantic
events and fingerprints for broad low-consequence cache invalidation.

## 3. Aletheia's Initial Catch-Up Is Stronger

When a WebSocket connects, the server sends every known category immediately.
The frontend invalidates all mapped queries. Hidden tabs close the socket; when
visible again, reconnection triggers the same catch-up.

TickFlow's SSE has no initial refresh event. It relies on current component
loads and future events. A disconnect across the last update of a session can
leave some caches stale.

Initial catch-up is cheap and should remain in Aletheia.

## 4. TickFlow's Connection Honesty Is Stronger

TickFlow exposes a global connected/reconnecting/disconnected store, renders a
reconnecting badge, and warns after repeated failures that alerts may be missed.

Aletheia's `useRealtimeUpdates` reconnects silently. Existing polling is
described as a fallback, but the inspected hook does not expose:

- connection state;
- last successful frame time;
- retry count/delay;
- degraded/polling-only mode;
- user-visible warning.

Not every mapped query's fallback cadence is proved by the hook. The UI should
not imply immediate updates when the push plane is down.

## 5. TickFlow's Per-Client Backpressure Is Stronger

Each TickFlow connection has independent bounded payload queues and coalesced
state-change flags. A slow/disconnected client cannot consume another client's
alerts.

Aletheia's hub serializes all client sends under one `send_lock` and awaits each
client sequentially. One slow socket can delay broadcasts to every other client.
There are no per-client queues, send timeout, coalescing window, drop count, or
disconnect threshold in the inspected hub.

Because Aletheia payloads are tiny category lists, normal risk is lower. The
fan-out ownership is still weaker.

## 6. Aletheia's Sampler Is Shared Across Clients

Fingerprint queries run once per backend process, not once per connection. Tasks
start with the first client and stop after the last. This is an important
efficiency property.

At static configured cadence, the regular and alert tiers issue roughly 42 SQL
statements per second while any dashboard is connected. These are scalar indexed
or aggregate queries, but actual query plans/cost are `Not available` here.

TickFlow has no equivalent database sampling load because publishers notify
directly. Aletheia should measure writer/read contention and event latency before
increasing categories or cadence.

## 7. Scalar `MAX` Fingerprints Can Miss Real Changes

The fingerprint is only as correct as the column chosen. Examples from the
current map:

- opportunities use `MAX(created_at)`, so an existing opportunity status/update
  that does not change the max may be invisible;
- proposals/risk decisions similarly emphasize insertion, not every mutation;
- deletion of a non-max row may not change the scalar;
- two writes sharing the same timestamp representation may collapse;
- corrections to historical rows are generally invisible;
- schema/query failure maps to `0`, conflating unavailable with empty.

TickFlow's explicit publisher event can cover updates/deletes if every writer
calls it. Neither approach is universally safe.

A durable monotonic change sequence per category, incremented in the same SQLite
transaction as the mutation, would be stronger and cheaper to compare.

## 8. Aletheia Separates Alert and Regular Cadence

Collector failures and in-memory warning/critical infra events use a 100 ms tier;
ordinary categories use 500 ms. This expresses consequence and avoids running
every query at alert cadence.

TickFlow's subscriber wake is immediate after the publisher call, but provider
poll cadence still governs when a condition is observed. Both systems correctly
distinguish acquisition cadence from notification latency.

## 9. Aletheia's REST Authority Is Stronger for Market Snapshots

Market snapshots are exact-venue observations persisted in one batch
transaction. The WebSocket fingerprint notices a committed timestamp, and REST
returns committed rows. The notification cannot precede the database write
because it is derived by a later sampler.

TickFlow updates fetch metadata, attempts several read-model writes, then emits;
swallowed errors can cause an event without a new enriched publication.

Aletheia's pull-after-commit ordering is stronger, although the scalar
fingerprint can still miss certain mutations.

## 10. Aletheia Has Stronger Decision Freshness

The shared freshness policy classifies missing, invalid, future, stale, and fresh
with bounded domain ages. Stale observations are removed from decision context
rather than merely dimmed.

TickFlow shows quote age and non-realtime mode in the UI, but its monitor/live
paths rely on a mix of market phase and date checks. Aletheia should not weaken
decision freshness to match visual transport liveness.

Transport connected and data decision-usable are independent facts.

## 11. Aletheia's Ticker UI Is Honest

`MarketTicker` queries BTC/ETH/SOL independently so a global newest-N list cannot
drop them. It keeps an old price visible but dims and labels it stale after five
minutes. Unwired indices display unavailable rather than fabricated values.

This matches TickFlow's best UI honesty and should be generalized to other live
panels.

## 12. Aletheia Price Charts Hide Prolonged Tail Failure

`PriceChart` polls the newest two candles every ten seconds. Tail errors are
silently ignored and the component remains `ok` after a successful initial
load. There is no last-success age, retry/degraded indicator, or source label.

The backend already emits `X-Aletheia-Data-Source` distinguishing provider,
memory cache, database fallback, and unavailable. The generic client discards
response headers, so the chart cannot surface that provenance.

The chart also requests `limit=2`, which bypasses the route's default latest
cache by design and can hit the provider every ten seconds per open chart.
Multiple tabs/charts have no frontend single-flight, and the backend provider
call may duplicate despite the general OHLCV cache.

TickFlow's focused-symbol invalidation over a locally updated read model is a
useful alternative when a canonical collector already publishes the tail.

## 13. Aletheia Has Duplicate Safety-Net Refreshes

Many Aletheia queries retain `refetchInterval` while WebSocket invalidation can
also trigger them. This is often deliberate recovery, especially for status,
risk, and market ticker. It can amplify REST work when updates are frequent.

TickFlow centralizes which quote keys are invalidated and explicitly excludes
upstream-limited minute queries. Aletheia's `CHANGE_MAP` centralizes push routing,
but polling cadence remains scattered across pages/components.

A query registry should document for each live resource:

```text
push category
poll fallback cadence
stale threshold
source cost
background/tab policy
required recovery behavior
```

## 14. Hidden-Tab Policy Differs

Aletheia closes the WebSocket while hidden and reconnects/invalidates all when
visible. This saves resources and avoids delivering a large invalidation backlog
to an inactive dashboard.

TickFlow keeps the top-level SSE connection; EventSource/browser lifecycle may
continue receiving bounded queues. This is useful for immediate alert toasts but
uses resources in hidden tabs.

The correct policy depends on alerts: a hidden desktop tab may still be an
operator notification surface. If Aletheia expects background alerts, closing
the socket means it must rely on system/Telegram/other durable channels or show
catch-up on return.

## 15. Neither System Has a Full Replay Protocol

TickFlow has bounded per-client event lists but no sequence/replay after
disconnect. Aletheia has initial state invalidation, which recovers current
state, but event-like infra notifications are not replayed by WebSocket identity.

Current state recovery and event recovery differ:

- positions/snapshots can refetch current state;
- “an alert occurred while disconnected” needs a durable event ID and cursor;
- a toast is presentation, while an audit/incident record must be durable;
- reconnect should report a gap even when current values now look healthy.

Aletheia already persists many incidents and audits; realtime transport should
point to those records rather than become their authority.

## 16. Recommended Aletheia Realtime Contract

### Connection state

```text
connected / reconnecting / polling_only / disconnected
connected_at and last_frame_at
retry_count and next_retry_at
backend_instance or runtime_session_id
```

### Change notification

```text
category
monotonic_change_seq
committed_at
source_as_of where relevant
urgency: alert / regular
```

### Client acknowledgement state

For state invalidation, the client only needs the greatest sequence seen and can
refetch REST. For durable alerts, use a separate record cursor/ID and reconcile
missed records.

### REST response

```text
data
as_of / observed_at
source/provider/venue
freshness verdict
read_model/publication sequence
partial/unavailable reason
```

## 17. What to Borrow From TickFlow

### Directly

- visible push-connection status and repeated-failure warning;
- domain-specific event categories;
- separate notification after derived result completion;
- per-client bounded queues/coalescing;
- centralized, cost-aware query-key invalidation;
- focused-symbol refresh instead of global chart invalidation;
- mode/scope honesty such as watchlist-only versus full market;
- labelled daily fallback when realtime cache is empty;
- market-session boundary semantics where the domain applies.

### After adaptation

- per-client asyncio queues rather than blocking worker-thread waits;
- queue drop counters and last sequence;
- initial generation/catch-up event on connection;
- durable alert cursor rather than replaying transient toast payloads;
- publication success envelope before `updated` notification;
- supervised tasks in FastAPI lifespan rather than daemon threads.

## 18. What Aletheia Should Keep

- WebSocket carries invalidations, not trading data authority;
- initial all-category catch-up;
- close while hidden and catch up on visible, if background alert policy is
  explicitly handled elsewhere;
- frontdoor bidirectional proxy and recovery event;
- committed SQLite snapshots as REST authority;
- exact instrument identity and provider/database fallback header;
- fail-closed decision freshness;
- honest stale/missing ticker display;
- periodic safety-net polling for critical status where cost is bounded.

## 19. Comparison Conclusion

TickFlow is ahead in push-plane product polish: connection visibility,
per-client queues, event taxonomy, and cost-aware invalidation. Aletheia is ahead
in commit ordering, exact persisted authority, initial catch-up, decision
freshness, and runtime proxy integration.

The most valuable combined direction is a committed monotonic change sequence
per category, distributed by the existing WebSocket, with visible connection
degradation, per-client slow-consumer isolation, and REST responses that expose
source/as-of/freshness. That improves realtime truth without turning the socket
into a trading data bus.
