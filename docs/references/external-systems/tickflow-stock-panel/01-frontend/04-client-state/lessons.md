# Domain 4 Lessons and Reflections

## Reuse Directly

### Central query-key factories

Put fixed keys, parameterized key factories, and safe family prefixes in a
data-only module. Include all response-defining parameters and reuse the same
factories in queries, mutations, realtime routing, tests, and diagnostics.

### Push-to-invalidate for derived server state

Use realtime messages to name what changed, then refetch the authoritative
REST/read-model representation. Push full data directly only when it is already
the canonical append-like event or stream payload and has an explicit merge
identity.

### One owner for each fallback poll

Many components may observe a cached fact; one reviewed global observer should
own its fallback interval. Document why polling is still needed when push is
healthy.

### Visible realtime transport state

Expose connected, reconnecting, disconnected/fallback, and recovery. Keep this
separate from backend process health and data freshness.

### Event-specific invalidation

Invalidate a strategy result only after recomputation, a depth view only after
depth correction, and a focused chart only when its symbol changes. Do not use
one market event to refetch unrelated analytical data.

## Reuse After Adaptation

### Source-to-read-model dependency map

Keep Aletheia's CHANGE_MAP concept, but build entries from typed query-key
families rather than independent literal arrays. Add a small validation that
registered change categories and critical key families are covered.

### Route-independent streaming state

Move long-lived work out of page-local state, but adapt the reference pattern to
backend task IDs, durable status, replay/reconciliation, and multiple concurrent
tasks when the domain requires them.

### Hybrid push and poll

For each family, specify the push source, fallback cadence, stale threshold,
active/idle behavior, and stop condition. Avoid using both mechanisms at full
frequency without a distinct failure mode to cover.

### Confirmed cache insertion

After a server-confirmed control or append event, insert the returned fact to
remove avoidable UI races, then invalidate projections. Keep speculative
optimism limited to reversible, non-authoritative convenience state.

### Hidden-tab policy

Aletheia's socket suspension saves resources; TickFlow's persistent SSE
preserves background alerts. Choose per channel. Safety notifications may need
an OS/backend delivery path rather than relying on a hidden browser tab.

## Study Further

- Inventory unique Aletheia query families semantically, not by text count, and
  map each to its query function, identity parameters, invalidators, push source,
  and poll owner.
- Determine which WebSocket change categories lack a reconciliation poll and
  which literal query families are absent from CHANGE_MAP.
- Measure refetch amplification during clustered backend change frames and
  runtime restart recovery.
- Define whether transport health belongs in the topbar, system panel, or a
  narrow diagnostic popover.
- Decide whether operator events need event IDs, replay cursors, or last-seen
  reconciliation after EventSource reconnect.
- Test multi-tab behavior and whether background timers/sockets should be
  leader-elected.
- Review authentication errors in Domain 5 using machine-readable status/code
  fields rather than message matching.

## Do Not Reuse

### Prefix naming as the only refresh policy

Do not make a string prefix the sole declaration of provider cost and realtime
fan-out. Use typed dependency metadata and allow names to remain identity-first.

### User-disabled refresh for safety facts

Optional market pages may suppress automatic refresh. Risk state, kill switch,
execution authority, positions, and required health must retain approved
freshness and degraded-state behavior.

### Speculative safety mutations

Never show pause, resume, live unlock, kill-switch release, or permission change
as successful before the backend confirms it.

### Silent permanent event loss

Ignoring malformed frames is acceptable only with diagnostics and eventual
authoritative reconciliation. Do not assume reconnect means missed changes were
replayed.

### Unscoped invalidation as a normal mutation pattern

Whole-cache invalidation is an exceptional recovery/reset tool, not a substitute
for a reviewed dependency graph.

### Module singleton as durable job storage

A module store cannot survive reload, process replacement, or another client.
Use it for transient rendering continuity, not authoritative task completion.

## Durable Design Reflections

### A query key is a data contract

It names representation identity, cache sharing, invalidation scope, and often
provider cost. Treat changes to key shape like changes to a schema.

### Push freshness and source truth are separate

A notification can reduce latency without becoming the authoritative record.
This separation keeps reconnect and reconciliation tractable.

### Silence is not health

A stream with no messages may be quiet, disconnected, throttled, or hidden. The
transport state and age of the last authoritative refresh are both needed.

### Every fallback needs an absence model

Polling should cover a named case: session boundary with no quote, missed event,
hidden-tab reconnect, or active job completion. Otherwise it merely adds load
and hides transport defects.

### Realtime fan-out is a dependency graph

The durable abstraction is not SSE versus WebSocket. It is the mapping from a
changed authoritative category to every read model whose answer is now stale.

### Conservative cache updates are a safety feature

Fast UI is useful, but an automated trading interface benefits from showing a
brief pending state rather than a false confirmed control state.

## Evidence Boundary

All conclusions are static. No reconnect fault, message loss, hidden-tab
throttling, server restart, request-count measurement, or multi-client exercise
was performed, and no frontend test suite was run.
