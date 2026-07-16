# Runtime Communication Lanes

Purpose: keep the dashboard responsive when market data, recommendations, or
external providers are slow. The UI should not treat every backend request as
the same kind of work.

## Lanes

1. **Liveness lane**
   - Endpoint: `/api/ready`
   - Frontend use: backend reconnect guard only.
   - Target: local process check, no DB, no config, no exchange call.
   - Polling: 10s is acceptable; alert only after repeated failures.

2. **Control/status lane**
   - Endpoints: `/api/status`, `/api/health`, kill switch/status-bar data.
   - Frontend use: top bar and operator visibility.
   - Target: local cached state; never run an external exchange probe directly
     inside the request path.
   - Polling: >= 10s. Cache repeated health rollups for a few seconds.

3. **Market display lane**
   - Endpoint: `/api/market/snapshots`.
   - Frontend use: ticker and lightweight panels.
   - Target: read the latest persisted snapshot. Stale data should be labeled
     honestly instead of blocking the page.
   - Polling: background only; should not drive the top progress bar.

4. **Advisory/background lane**
   - Endpoint: `/api/operator/recommendations` and similar summary builders.
   - Frontend use: suggestions, panels, non-critical guidance.
   - Target: cache briefly and refresh more slowly than liveness/status.
   - Polling: background only.

5. **Future push lane**
   - Candidate use: agent progress, command output, long-running diagnostics.
   - Reference: OpenAlice uses SSE with auto-reconnect/backoff for streaming AI
     progress. Aletheia already has operator SSE helpers; extend only when a
     workflow needs live intermediate updates.

## Rules

- The global top progress bar is for user-visible page loads, not permanent
  background polling.
- A failed business health check is not the same as a disconnected backend.
  Use `/api/ready` for reachability and `/api/health` for degraded state.
- External network probes belong in scheduler/startup monitors. API handlers
  should consume the last cached result.
- Market feed failures should preserve the last known snapshot and surface age;
  they should not make the whole dashboard feel offline.
