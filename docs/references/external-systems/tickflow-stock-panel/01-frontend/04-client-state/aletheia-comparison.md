# Domain 4 Aletheia Comparison

## Summary

TickFlow has the more centralized query-key catalogue, shared observer model,
and visible realtime connection health. Aletheia has the stronger abort-aware
request policy, visibility-aware WebSocket lifecycle, explicit database-change
dependency map, and conservative handling of safety-control cache state.

The largest Aletheia maintainability gap is that its centralized realtime map
references a query namespace that is otherwise distributed across hundreds of
literal key declarations and invalidations.

## Comparison Matrix

| Boundary | TickFlow | Aletheia | Assessment |
|---|---|---|---|
| default stale policy | five seconds | 30 seconds | neither is universally correct; Aletheia optimizes navigation reuse more strongly |
| focus refetch | disabled | disabled | shared deliberate choice |
| retry policy | no explicit query retry override | one retry, none for AbortError | Aletheia is more explicit and avoids retrying cancelled/timeout work |
| request cancellation | outside this domain's inspected reference boundary | caller signal combined with a 15-second timeout | Aletheia is stronger |
| key registry | broad typed QK constants/factories | literal arrays at call sites | TickFlow is materially easier to audit |
| key-registry adoption | broad but incomplete | not present | TickFlow's direction is stronger even with escape hatches |
| realtime source | EventSource with domain event names | WebSocket with backend database-change categories | both use push-to-invalidate; event semantics differ |
| dependency routing | quote prefix list plus event-specific invalidations | centralized CHANGE_MAP from change category to query prefixes | Aletheia's source-to-consumer dependency map is stronger conceptually |
| hidden-tab behavior | stays connected | closes socket and reconnects when visible | Aletheia reduces background cost; TickFlow favors background alerts |
| reconnect | exponential to 60 seconds, visible status, toast after failures | 1.5x backoff to 30 seconds, polling fallback | TickFlow is more observable; Aletheia is more resource-aware |
| replay/cursor | not found | not found | shared delivery gap for missed changes |
| fallback poll | explicit quote-session boundary poll plus page-specific jobs | existing refetch intervals continue; topbar uses adaptive active/idle polling | both deliberately hybridize push and poll |
| confirmed cache writes | selected server responses written, then dependents invalidated | server-confirmed kill switch and streamed operator events written | both avoid unsafe broad optimism |
| long-lived stream state | module-level review store survives route unmount | Command Center merges server events into query cache; page owns EventSource | reference has the stronger route-independent generation model |
| transport-health UI | connected/reconnecting/disconnected badge | WebSocket state is not exposed directly | TickFlow is stronger |

## What Aletheia Already Does Better

### Abort-aware bounded requests

The API client combines a caller-provided signal with its own timeout signal.
The QueryClient refuses to retry AbortError and otherwise allows only one retry.
This prevents a timed-out request from silently doubling its wait and work.

### Database-change dependency map

`CHANGE_MAP` says which client cache families depend on each backend change
category. Positions and fills invalidate positions, portfolio, and equity curve;
market snapshots invalidate ticker, list snapshots, and sparklines; decisions
and risks refresh the relevant funnel summaries.

This is more semantically useful than a broad family name alone: it records the
fan-out from changed authoritative data to read models.

### Visibility-aware connection lifecycle

Aletheia closes the global WebSocket while the document is hidden, cancels the
reconnect timer, resets backoff on visibility, and reconnects immediately. For
a local operator dashboard with REST polling fallbacks, this is a reasonable
resource policy.

### Adaptive operational polling

Topbar status queries poll slowly under normal conditions and speed up while a
restart is in progress. Restart recovery polls a narrow health endpoint, waits
briefly for heavy subsystems to warm, then invalidates the cache. This directly
models a transient recovery phase instead of leaving ordinary long intervals in
place.

### Conservative safety-control updates

Gateway pause/resume and other status mutations invalidate after success rather
than inventing optimistic status. CommandPalette writes kill-switch cache only
from the server's confirmed response, then invalidates kill-switch and risk
state. An existing regression test explicitly guards against optimistic status
in Overview.

### Direct merge for append-like operator events

Command Center merges a pushed, authoritative OperatorEvent into the general
event cache and invalidates dependent session/task/context views. This avoids a
round trip for the event feed while still refetching derived projections.

## Where Aletheia Can Learn

### Establish a typed query-key registry

The realtime dependency map is centralized, but consumers declare roughly 340
literal query-key occurrences and mutations contain roughly 165 invalidation
occurrences. A renamed or parameterized key can drift away from CHANGE_MAP
without a type error.

Introduce a data-only registry incrementally, beginning with:

- safety/system status;
- positions, fills, portfolio, and equity;
- agent runs, opportunities, proposals, and decisions;
- market snapshots, ticker snapshots, and sparklines;
- operator/runtime families.

Factories should include every representation-defining parameter and expose
safe prefix constants for dependency invalidation.

### Separate transport health from backend health

The WebSocket can fail while REST polling remains healthy. That is degraded
freshness, not necessarily an offline backend. A small connected/reconnecting/
polling-fallback state would let the UI state this honestly.

### Declare fallback coverage

`useRealtimeUpdates` comments list values not covered by the WebSocket, and
existing intervals remain fallback. Turn that into a reviewable policy: each
important cache family should name its push source, fallback cadence, stale
threshold, and user-visible degraded state.

### Share global query observers

The StatusBar legitimately owns many operational queries, but other widely read
facts can benefit from canonical hooks so query function, key, polling owner,
and error semantics cannot drift across pages.

### Add reconnect jitter and invalidation coalescing

Both applications can reconnect synchronized clients at the same delay and can
invalidate repeatedly during an active change burst. Bounded jitter and event
coalescing would reduce a thundering herd without turning push notifications
into an unbounded queue.

### Preserve route-independent work only where the work is durable

TickFlow's review store demonstrates why long generation should not be owned by
a page. Aletheia should prefer backend task identity plus queryable status for
important work, using client external stores only for transient rendering state.

## Aletheia Risks Found During Comparison

### Query identity and invalidation are maintained separately

CHANGE_MAP can only invalidate keys its authors know. Literal arrays across
pages and features are not mechanically tied to it. The comments already record
past omissions, such as newly added funnel snapshots, which is concrete evidence
of drift cost.

### Realtime degradation is invisible

The WebSocket has no exported connection state or failure notice. Polling may
keep some surfaces fresh, but the operator cannot distinguish healthy push from
fallback-only behavior.

### Unknown and malformed frames are silent

Malformed JSON and unknown change categories are ignored. That protects the UI
from crashing, but there is no diagnostic counter or degraded-state signal.

The separate Command Center EventSource parses `operator_event` payloads without
its own try/catch, unlike the global WebSocket handler.

### Broad post-restart invalidation is expensive

After backend recovery, App invalidates all queries. This is reasonable after a
runtime restart but can create a request burst against a warming process. The
existing 1.5-second warm-up pause reduces the risk; an explicit critical-first
refetch sequence would be more predictable.

### Event fan-out is not coalesced

Each changed backend category loops through its mappings and calls invalidate.
Overlapping categories or rapid frames can invalidate the same family several
times. Static source does not establish actual request amplification because
React Query can deduplicate overlapping work.

## Invariants That Must Remain

- Backend and database representations remain authoritative for trading,
  positions, risk, and execution controls.
- Safety controls must not use speculative optimistic state.
- Push delivery cannot be the only path to a required state; reconnect and
  bounded reconciliation are necessary.
- Query identity must include all dimensions that change meaning, including
  market type where applicable.
- A healthy REST response must not be presented as proof that realtime push is
  healthy, and vice versa.
- Unknown/missed events must lead to eventual authoritative reconciliation, not
  permanent client divergence.
