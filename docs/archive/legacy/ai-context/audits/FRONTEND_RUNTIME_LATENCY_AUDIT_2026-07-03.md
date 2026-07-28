# Frontend/API Runtime Latency Audit - 2026-07-03

## Scope

Branch: `audit/frontend-runtime-latency`

This audit covers the current single-port dashboard runtime at
`http://127.0.0.1:8787/`. It is measurement-only:

- no frontend behavior changes
- no backend route semantics changes
- no trading, risk, execution, live-unlock, credential, or order-path changes
- no writes to the dirty `master` worktree

The local `:8787` gateway was already running. `:5173` was not listening, which
matches `scripts/dev.ps1`: the gateway serves the dashboard and API from one
entrypoint.

## Method

Each path below was requested 10 times over HTTP against the live local gateway.
The table is sorted by average elapsed time. Measurements are wall-clock
client-side milliseconds from `Invoke-WebRequest` and include local HTTP
overhead and JSON serialization.

## Runtime Baseline

| Path | Status | Avg ms | P50 ms | P90 ms | Max ms | Bytes |
|---|---:|---:|---:|---:|---:|---:|
| `/api/operator/recommendations` | 200 | 492.0 | 476.4 | 545.5 | 545.5 | 2,341 |
| `/api/operator/runtime` | 200 | 102.6 | 87.1 | 201.3 | 201.3 | 1,185 |
| `/api/operator/runtime/events?limit=100` | 200 | 85.8 | 81.2 | 119.8 | 119.8 | 2 |
| `/api/knowledge/guards` | 200 | 81.4 | 73.0 | 115.8 | 115.8 | 7,642 |
| `/api/operator/sessions` | 200 | 55.2 | 37.3 | 138.6 | 138.6 | 23,851 |
| `/api/status` | 200 | 48.3 | 47.6 | 50.7 | 50.7 | 835 |
| `/api/operator/events?limit=100` | 200 | 40.7 | 40.0 | 45.7 | 45.7 | 47,005 |
| `/api/health` | 200 | 31.9 | 26.3 | 61.0 | 61.0 | 115 |
| `/api/operator/status` | 200 | 29.2 | 29.0 | 30.8 | 30.8 | 724 |
| `/api/positions` | 200 | 20.8 | 7.8 | 137.3 | 137.3 | 101,261 |
| `/api/portfolio` | 200 | 18.9 | 13.3 | 61.5 | 61.5 | 7,124 |
| `/api/operator/tasks` | 200 | 17.7 | 17.6 | 18.8 | 18.8 | 39,325 |
| `/assets/index.js` | 200 | 16.6 | 13.6 | 27.1 | 27.1 | 937,770 |
| `/api/persistence/lessons?limit=100` | 200 | 8.7 | 8.0 | 10.7 | 10.7 | 63,896 |
| `/api/analytics/trade-stats` | 200 | 8.2 | 6.9 | 13.9 | 13.9 | 1,909 |
| `/` | 200 | 8.0 | 2.9 | 48.8 | 48.8 | 846 |
| `/assets/recharts.js` | 200 | 7.5 | 7.2 | 11.1 | 11.1 | 434,303 |
| `/api/proposals` | 200 | 6.7 | 6.6 | 7.3 | 7.3 | 108,241 |
| `/api/trades` | 200 | 6.3 | 6.3 | 6.7 | 6.7 | 47,401 |
| `/api/knowledge?type=research_note` | 200 | 6.0 | 5.7 | 8.1 | 8.1 | 21,251 |
| `/api/agent-runs` | 200 | 5.8 | 5.3 | 8.8 | 8.8 | 60,889 |
| `/api/knowledge/strategy-memory` | 200 | 5.6 | 5.3 | 7.0 | 7.0 | 22,093 |
| `/assets/index.css` | 200 | 5.3 | 3.6 | 17.2 | 17.2 | 96,731 |
| `/api/knowledge/dreaming-logs` | 200 | 5.2 | 4.9 | 6.9 | 6.9 | 22,029 |
| `/api/knowledge?type=daily_review` | 200 | 5.0 | 4.7 | 6.3 | 6.3 | 9,125 |
| `/api/positions/reconciliation` | 200 | 4.9 | 4.5 | 6.7 | 6.7 | 7,183 |
| `/api/operator/extensions?status=enabled&limit=50` | 200 | 4.7 | 4.4 | 5.8 | 5.8 | 2 |
| `/api/infra/events?limit=12&min_severity=warning` | 200 | 4.4 | 4.1 | 6.3 | 6.3 | 13 |
| `/api/risk/kill-switch` | 200 | 4.1 | 4.0 | 4.5 | 4.5 | 16 |

Static first-load assets total 1,468,804 bytes:

- `/assets/index.js`: 937,770 bytes
- `/assets/recharts.js`: 434,303 bytes
- `/assets/index.css`: 96,731 bytes

## Page-Level Request Pressure

Source inspection shows the following initial query pressure:

- Global shell / status bar: 6 recurring queries every 10 seconds
  (`status`, `operator-status`, `risk/kill-switch`, `agent-runs`, `health`,
  `infra-events`).
- Command Center: at least 8 base queries on mount
  (`operator-status`, `operator-sessions`, `operator-tasks`,
  `operator-extensions`, `operator-recommendations`, `operator-events`,
  `operator-runtime`, `operator-runtime-events`), plus selected-session detail,
  selected-session events, selected-session context, and an SSE stream.
- Learning knowledge tab: 6 unique tab/count queries on the knowledge tab
  (`research_note`, `strategy-memory`, `daily_review`, `dreaming-logs`,
  `lessons`, `guards`).
- Trades & Positions: 3 direct queries (`positions`, `trades`,
  `positions/reconciliation`).

## Findings

1. The largest measured latency offender is
   `/api/operator/recommendations`: P50 476.4 ms and P90 545.5 ms. It is called
   when Command Center mounts. This is not a static-asset problem: the endpoint
   returns only 2.3 KB, but rebuilds recommendations by reading operator store,
   health state, knowledge, agent runs, positions, strategies, artifacts, and
   related derived state.

2. Command Center has high fan-out. Even if each endpoint were fast, it issues
   8-11 initial API reads plus an SSE stream. The slow recommendation endpoint
   dominates perceived load and should be optimized before broad UI work.

3. The static JS payload is material but not the current top runtime offender:
   `index.js + recharts.js + index.css` total about 1.47 MB, while local asset
   HTTP p90 stayed below 28 ms. Bundle splitting remains a later frontend build
   optimization, not the first runtime fix.

4. The global status bar is honest but chatty: 6 recurring queries at 10 second
   intervals. This is acceptable on localhost, but the shared shell means any
   slow endpoint added there would affect every page. No slow global endpoint
   was found in this run.

5. The audit did not run a browser-render trace because this worktree does not
   have `frontend/node_modules`. The quantitative baseline is therefore HTTP
   runtime latency plus source-level request fan-out, not React render timing.

## Quantified Outcome

- Validated 29 live paths with 10 HTTP samples each: 290 runtime requests.
- Corrected 3 initially mis-guessed endpoint paths before final measurement:
  `/api/risk/kill-switch`, `/api/analytics/trade-stats`,
  `/api/knowledge/dreaming-logs`.
- Identified 1 clear P2 latency fix target:
  `/api/operator/recommendations`, which is 4.8x slower than the next
  non-empty operator endpoint average (`492.0 ms` vs `102.6 ms`) and 12.1x
  slower than `/api/operator/events?limit=100` (`492.0 ms` vs `40.7 ms`).

## Recommended Next Branch

Open `fix/frontend-latency-top-offenders` from integration and target the
recommendation endpoint first. The preferred narrow fix is to reduce duplicated
work inside `build_recommendations()` while preserving every recommendation kind
and existing tests. Do not change Command Center UI or recommendation semantics
until the backend endpoint has a focused regression and a before/after latency
measurement.
