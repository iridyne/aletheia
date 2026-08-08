# Domain 10 Lessons and Reflections

## Reuse Directly

### Push invalidations, fetch state through REST

Keep one authoritative data contract. Push only category/version/urgency and
let React Query retrieve the current typed state.

### One connection owner at the application root

Mount the SSE/WebSocket hook once beneath the Query client. Share connection
status and cached data rather than opening one transport per page.

### Separate state-change and event payload semantics

Coalesce “quotes changed” or “positions changed” into a version. Queue true
events such as alerts only when their payload must be delivered, and persist
trust-bearing events separately.

### Initial catch-up on every connection

Send current category sequences or invalidate-all immediately after connect so
data changed during a gap cannot remain stale indefinitely.

### Visible degraded connection

Expose connected, reconnecting, polling-only, and disconnected. Show repeated
failure once, retain the last truthful data, and never equate transport liveness
with data freshness.

### Centralize change-to-query routing

Maintain one mapping from backend categories to React Query key families. Review
it alongside source cost and fallback polling to avoid accidental request storms.

### Refresh derived state only after it is published

Emit a strategy-result/read-model notification after the derived result commits,
not merely after its source quote arrives.

### Keep rate-limit-sensitive resources off high-frequency invalidation

Do not refetch minute bars or external-provider endpoints on every quote tick.
Use local read models, focused scope, or a bounded cadence.

### Preserve old values with visible staleness

Missing, stale, fallback, and live are different. Keep useful last-known values
while showing age/source and fail closed for decision use.

## Reuse After Adaptation

### Per-client bounded asyncio channels

Give each client independent coalesced state versions and bounded event queues.
Set send deadlines, disconnect persistently slow clients, and expose drop counts.

### Monotonic committed change sequence

Replace fragile `MAX(timestamp)` fingerprints with a per-category sequence
incremented in the same SQLite transaction as authoritative mutation. The
WebSocket sampler can compare a compact sequence table.

### Consequence-based tiers

Retain Aletheia's fast alert and regular tiers, but measure SQL load and latency.
High priority should reflect operator consequence, not an arbitrary desire for
more freshness.

### Durable alert cursor

Push a stable incident/audit/alert ID. On reconnect, fetch records after the
last acknowledged cursor. Toast display is best effort; incident history is not.

### Publication envelope

For market/read-model changes, include:

```text
category and sequence
source_observed_at
published_at
complete / partial / failed
affected scope
runtime session
```

### Chart tail from local authority

If a collector already persists exact closed/current observations, invalidate a
focused chart query against local REST rather than letting every open chart poll
the exchange. Preserve current-forming bar semantics and source labels.

### Surface response provenance to components

Extend the frontend client to expose selected headers or put provenance in a
typed response envelope. `PriceChart` should distinguish provider fetch,
database fallback, cache, stale, and unavailable.

### Market/session state as a domain service

Where session calendars apply, separate continuous trading, collection window,
final-boundary acquisition, closed, and holiday. Do not use one `is_open` boolean.

### Visibility-aware policy by event consequence

Closing sockets in hidden tabs saves work. If background alerting is expected,
rely on durable/system channels and reconcile on return rather than silently
dropping operator events.

## Aletheia-Specific Questions

- Which `MAX(...)` fingerprints miss updates/deletes/corrections in their tables?
- Can a monotonic change-sequence table be updated in existing business
  transactions without expanding writer hold time materially?
- Which mapped queries truly retain a polling fallback, at what cadence and
  cost?
- How should the top bar display WebSocket down but REST healthy?
- Which alert/incident rows can provide a durable reconnect cursor?
- Does a hidden browser need background alerts, or are Telegram/system channels
  authoritative for that use?
- What slow-client send timeout is appropriate for a local desktop/frontdoor?
- Should fingerprint query errors surface a `sampler_degraded` state instead of
  scalar `0`?
- Which chart tail endpoints hit external providers and can be served from the
  canonical collector instead?
- How should UI and decision freshness share timestamps without sharing policy?

## Study Further

- Build an exact Aletheia fingerprint table: category, SQL, mutation types,
  writers, timestamp precision, missed-update cases, query plan, and REST keys.
- Measure fingerprint sampler SQL statements, elapsed time, executor occupancy,
  WebSocket latency, and SQLite writer interaction with one and many clients.
- Inspect every frontend `refetchInterval` and classify safety-net, necessary
  external poll, duplicate push refresh, or removable.
- Trace WebSocket connection state through frontdoor restart/backend switch and
  verify whether reconnect receives the new runtime session identity.
- Inspect slow-client behavior and ASGI/websocket library queue limits.
- Define durable incident cursors for infra/collector alerts and map catch-up UI.
- Trace TickFlow alert persistence relative to broadcast and determine what a
  reconnecting client can recover from REST.
- Inspect TickFlow final-sync status after daily/enriched/monitor failures and
  separate acquisition from publication.
- Trace TickFlow watchlist batch failures into status/UI completeness.
- Verify event-source heartbeat, proxy idle timeout, browser retry, and custom
  retry interaction in a later bounded runtime experiment.
- Inventory Aletheia REST response provenance headers that are discarded by the
  frontend client.
- Measure open-chart provider request amplification across tabs/symbols and
  evaluate local-tail invalidation.

## Do Not Reuse

### Connected equals fresh

A healthy socket can carry no new data while acquisition is stalled. Show
connection, source health, publication state, and data age independently.

### Notification before verified publication

Do not mark final sync or emit `updated` merely because a provider returned rows
if persistence/derived model failed.

### One shared consumptive queue

The first tab must not remove alerts intended for other clients.

### Sequential unbounded broadcast to all clients

One slow socket should not block every client. Use per-client queues and
deadlines.

### Timestamp maximum as universal change identity

It misses updates, deletions, historical corrections, and timestamp collisions.

### No initial catch-up

A reconnect with no later event can leave data changed during the outage stale.

### Transient payload as audit

SSE/WebSocket delivery is not proof that an alert/decision occurred or was seen.
Persist stable identity first.

### Silent prolonged chart polling failure

Do not leave an old chart in an `ok` state indefinitely without last-success age
or fallback/source indication.

### High-frequency invalidation of external-provider queries

One quote event must not multiply into provider calls across every mounted
component/tab.

### Queue truncation without telemetry

If oldest alerts/reviews are dropped, expose dropped count/sequence gap so the
client can reconcile.

### Generic prefix invalidation without cost review

String-prefix convenience can refresh unrelated or expensive query families as
the key space grows.

## Final Reflection

Realtime UI is not fundamentally a socket problem. It is a synchronization
protocol among acquisition, committed state, derived models, notifications,
client caches, reconnect, and operator interpretation.

TickFlow demonstrates thoughtful product behavior at the transport/UI edge;
Aletheia demonstrates stronger committed-state and freshness semantics. The best
combined architecture lets the socket say only, “committed category sequence N
exists,” then proves the actual value, source, and freshness through REST.
