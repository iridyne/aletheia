# Domain 10: Realtime Data, Push Transport, and Frontend Refresh

This domain traces TickFlow and Aletheia from data acquisition through hot-state
publication, change notification, REST refetch, UI freshness, reconnect, and
degraded operation.

## Included

- realtime quote pollers, market phases, final snapshots, and write/compute order;
- SSE/WebSocket connection ownership, broadcast, queues, coalescing, and
  backpressure;
- event taxonomy, invalidation routing, React Query keys, and selective refetch;
- reconnect, tab visibility, initial synchronization, heartbeat, and replay;
- freshness/source/status display and stale-data behavior;
- REST authority versus transport payload authority;
- Aletheia's fingerprint hub, frontend WebSocket hook, chart/ticker polling, and
  frontdoor WebSocket proxy.

## Excluded

- file/dataset consistency, covered in Domain 9;
- complete scheduler and job ownership, covered in Domain 11;
- authentication, full security, and test strategy, covered in Domain 12;
- runtime provider calls, browsers, servers, network capture, or implementation.

## Reports

- `source-map.md`
- `realtime-sync-analysis.md`
- `aletheia-comparison.md`
- `lessons.md`

## Evidence Boundary

The findings are static: current source, CodeGraph call paths, and focused
inventories. No server, provider, WebSocket, SSE stream, browser, clock/market
phase, network fault, or test suite was run.
