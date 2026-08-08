# Domain 4: Client State, Cache, and Realtime Synchronization

This domain studies how TickFlow coordinates server state in TanStack Query,
routes realtime notifications into cache invalidation, and preserves long-lived
streaming work across page lifecycles.

## Included

- QueryClient defaults and global error behavior;
- query-key identity, factories, prefixes, and ownership;
- shared query and mutation hooks;
- stale times, previous-data retention, polling, and refetch fallbacks;
- SSE/WebSocket lifecycle, event routing, reconnect, and visible health;
- direct cache writes after confirmed server responses;
- streaming state that outlives a page component;
- comparison with Aletheia's query cache and realtime update layer.

## Excluded

- HTTP request/response typing and error-body contracts, covered in Domain 5;
- backend stream production and database fingerprinting internals, covered in
  later backend/runtime domains;
- browser network traces, reconnect fault injection, or timing benchmarks;
- any change to Aletheia cache or realtime behavior.

## Reports

- `source-map.md`
- `client-state-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The study uses current source and CodeGraph call paths. No live SSE/WebSocket
connection, offline transition, multi-tab session, polling measurement, or
frontend test suite was run.
