# TickFlow Client-State and Synchronization Analysis

## Finding

TickFlow uses React Query as the authoritative server-state cache and treats SSE
primarily as a change notification rather than a second copy of the database.
Its strongest practice is the combination of a centralized key factory,
shared global observers, event-specific invalidation, deliberate polling
fallbacks, and visible stream health.

The design is not fully centralized and its prefix conventions are behaviorally
fragile. The review-stream singleton also solves page-lifecycle loss at the
cost of process-local, single-task state.

## 1. Global Defaults Reduce Navigation Churn

The QueryClient gives ordinary data a five-second stale window and disables
window-focus refetch. Pages can override this for slow-changing fundamentals,
schemas, versions, job progress, or minute data.

This avoids two common extremes:

- zero stale time that turns every remount into a request and blank transition;
- one long global TTL that makes rapidly changing market state appear current.

The global QueryCache and mutation handlers also route recognized session and
initialization failures to Login once, preserving the current path as a return
target.

### Risks

- Authentication classification depends on matching human/error message text
  such as `401`, session-expired wording, or an initialization token.
- Query errors other than those selected authentication cases have no global
  presentation policy here.
- The five-second default is documented against a repository invariant, but
  static frontend source cannot prove the backend freshness contract.

## 2. Query Keys Are Treated as Architecture

`QK` centralizes fixed keys and parameterized factories. Parameters such as
symbol, date range, extension columns, asset type, limit, and analysis ID are
part of cache identity. Shared hooks and most pages reuse those factories.

The same file records which key prefixes can be invalidated by quote events.
This makes realtime fan-out inspectable without opening every page.

### Valuable consequence

The minute-batch key intentionally avoids the `watchlist` namespace. A comment
explains that watchlist-prefixed data is invalidated at quote frequency, while
minute mini charts should refresh on their bounded interval to avoid provider
rate limits. Cache naming therefore documents a resource boundary.

### Architectural warning

When string prefixes determine refresh behavior, a key name is no longer only
an identity. Renaming or adding a key can change network load. The stream uses
`startsWith` on the first key segment rather than exact typed dependency data,
so similarly named prefixes can be captured accidentally.

The registry is also incomplete: static inventory still found roughly 54
literal query-key declarations and several literal invalidations.

## 3. Shared Observers Establish Poll Ownership

Capabilities, settings, preferences, quote status, quote interval, version, and
data status have shared hooks. Quote status accepts a `poll` option, but source
comments require only the globally mounted Layout observer to enable its
60-second fallback. Other consumers share the key without adding timers.

This is a meaningful ownership model. A value may have many readers while the
fallback polling responsibility remains singular and reviewable.

The 60-second poll covers trading-session transitions that may produce no quote
event, such as entering lunch break or market close. Push and poll cover
different absence modes instead of blindly duplicating one another.

## 4. SSE Usually Signals Change, Not Authoritative Rows

For market, strategy, depth, and alert-list changes, the client invalidates the
smallest known query family and lets the normal query function fetch the
authoritative representation. This avoids maintaining two normalization and
merge implementations for REST and SSE.

Event types encode causal boundaries:

- a quote tick does not invalidate static strategy cache;
- strategy results refresh only after recomputation completes;
- depth correction refreshes only depth-dependent views;
- a focused stock gets a precise K-line invalidation rather than globally
  refreshing every chart.

This reduces stale windows and protects provider quota better than one generic
“realtime changed, refetch everything” handler.

## 5. Refresh Scope Is User-Configurable

Quote invalidation can be disabled by page prefix through saved preferences,
while quote status always refreshes. Strategy alerts remain active even if the
realtime-quote display toggle is off.

This distinguishes “do not spend requests refreshing this analytical page”
from “disconnect the alert transport.” It is appropriate for optional market
views. The same pattern would be unsafe for risk, execution authority, kill
switch, or other mandatory control state.

## 6. Stream Health Is a First-Class UI State

The EventSource hook exposes connected, reconnecting, and disconnected through
a small `useSyncExternalStore` module store. After three consecutive failures it
shows one error toast, resets that suppression after recovery, and retries with
exponential backoff up to 60 seconds.

This matters because a quiet stream can otherwise look identical to a healthy
market with no changes. The connection badge makes missing push delivery an
observable product fact.

### Reconnect gaps

- Backoff has no random jitter, so many clients can reconnect together.
- The custom reconnect path carries no explicit event cursor or replay token;
  static source does not show recovery of missed events.
- Malformed payloads are silently ignored.
- The connection remains active in a hidden tab, which preserves alerts but
  consumes background resources.
- There is no client-side invalidation coalescing when high-frequency events
  arrive while active queries are already refetching.

## 7. Direct Cache Writes Follow Confirmed Facts

Shared mutations usually wait for server success, then either invalidate the
relevant queries or write the returned representation directly before
invalidating dependents. Examples include quote interval, onboarding settings,
watchlist responses, and AI settings.

This is not broad optimistic state. The write is used to remove a known race or
reflect a confirmed server response. That is the safer default for settings and
control surfaces.

One data-reset path performs an unscoped `invalidateQueries()`. It is logically
understandable after replacing the data plane, but expensive and hard to reason
about compared with an explicit dependency set.

## 8. Streaming Review State Outlives the Route

Review generation is not stored in a page component. A module-level store owns
phase, content, metadata, focus, abort controller, source, and subscribers. A
manual stream therefore continues if the user navigates away, and the Review
page can resubscribe later.

The store also arbitrates manual and scheduled SSE flows:

- a manual generation takes precedence over scheduled events;
- scheduled deltas require an earlier metadata event;
- retry clears partial scheduled content;
- only the manual path invokes the client archive callback;
- scheduled completion assumes the backend has archived the result.

This is a careful solution to route-lifecycle loss and double archival.

### Limits

- State disappears on full page reload or process restart.
- Only one generation/source can exist at once.
- The store uses `any` for incoming scheduled events and silently ignores
  malformed or out-of-order data.
- Ignoring scheduled events during manual generation can create an observation
  gap unless the archived result is later recovered from history.
- It is a bespoke external store rather than a generalized durable-job model.

## 9. Polling Is Adaptive Where Completion Matters

Pipeline and financial status queries use polling only while work is active and
stop or slow when terminal. Previous data is retained for chart and table
queries where a background refresh should not blank the view.

This is better than a single application-wide interval. The next level of
maturity would make every poll owner, fallback reason, active cadence, idle
cadence, and stop condition reviewable as one policy table.

## 10. Static Evidence Does Not Prove Timing Correctness

No reference frontend test/spec files were found. The code shows deliberate
event boundaries, but does not prove:

- events are not lost across reconnect;
- high-frequency invalidation stays within provider limits;
- multi-tab sessions do not multiply work unexpectedly;
- background throttling preserves alert timeliness;
- message ordering and manual/SSE arbitration behave under faults.
